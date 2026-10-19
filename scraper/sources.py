"""Source list and per-source discovery: turn a configured source into candidate seeds."""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import feedparser
import httpx
import yaml

from .config import ROOT, USER_AGENT, FETCH_TIMEOUT
from .fetch import extract_links, fetch_html

SOURCES_FILE = ROOT / "sources.yaml"
ARXIV_API = "https://export.arxiv.org/api/query"


@dataclass
class Seed:
    url: str
    source_name: str
    provenance: Optional[int] = None  # source-level override of the LLM's source_type score
    title: Optional[str] = None
    text: Optional[str] = None  # pre-fetched text (arXiv abstracts) so the agent skips the fetch


def load_sources(path: Path = SOURCES_FILE) -> list[dict]:
    return yaml.safe_load(path.read_text())["sources"]


def discover_arxiv(source: dict, limit: int) -> list[Seed]:
    params = {
        "search_query": source["query"],
        "max_results": min(limit, source.get("max_results", limit)),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    response = httpx.get(ARXIV_API, params=params, headers={"User-Agent": USER_AGENT}, timeout=FETCH_TIMEOUT)
    response.raise_for_status()
    feed = feedparser.parse(response.text)
    seeds = []
    for entry in feed.entries:
        authors = ", ".join(a.get("name", "") for a in entry.get("authors", []))
        text = f"{entry.title}\n\nAuthors: {authors}\n\nAbstract:\n{entry.summary}"
        seeds.append(Seed(url=entry.link, source_name=source["name"], provenance=source.get("provenance"),
                          title=entry.title, text=text))
    return seeds


ASSET_SUFFIXES = (".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".woff", ".woff2", ".xml", ".json")


def discover_rss(source: dict, limit: int) -> list[Seed]:
    """RSS/Atom feed: the most reliable listing for press sites, and it carries the title."""
    response = httpx.get(source["url"], headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=FETCH_TIMEOUT)
    response.raise_for_status()
    feed = feedparser.parse(response.text)
    return [Seed(url=entry.link, source_name=source["name"], provenance=source.get("provenance"), title=entry.get("title"))
            for entry in feed.entries[:limit]]


def discover_html(source: dict, limit: int) -> list[Seed]:
    """Crawl one listing page and keep same-domain links that look like articles."""
    html = fetch_html(source["url"])
    domain = urlparse(source["url"]).netloc.lower().removeprefix("www.")
    pattern = source.get("link_pattern")
    seeds = []
    for link in extract_links(html, source["url"]):
        parsed = urlparse(link)
        if parsed.netloc.lower().removeprefix("www.") != domain:
            continue
        if parsed.path.lower().endswith(ASSET_SUFFIXES) or "/_next/" in parsed.path:
            continue
        if pattern and pattern not in parsed.path:
            continue
        if not pattern and parsed.path.count("/") < 2:  # skip nav links like /about
            continue
        if link.rstrip("/") == source["url"].rstrip("/"):
            continue
        seeds.append(Seed(url=link, source_name=source["name"], provenance=source.get("provenance")))
