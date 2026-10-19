"""Fetch a page and turn it into clean text. Supports http(s) URLs and local files (synthetic data)."""
import html as htmllib
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag

import httpx
import trafilatura

from .config import FETCH_TIMEOUT, MAX_PAGE_CHARS, USER_AGENT


class FetchError(RuntimeError):
    pass


@dataclass
class Page:
    url: str
    title: str
    text: str


def is_local(url: str) -> bool:
    return url.startswith("file://") or Path(url).exists()


def _local_path(url: str) -> Path:
    return Path(url[len("file://"):] if url.startswith("file://") else url)


def fetch_html(url: str) -> str:
    if is_local(url):
        try:
            return _local_path(url).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            raise FetchError(f"{url}: {e}") from e
    try:
        response = httpx.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
            follow_redirects=True,
            timeout=FETCH_TIMEOUT,
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        raise FetchError(f"{url}: {e}") from e
    return response.text


def title_of(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if not match:
        return ""
    return htmllib.unescape(re.sub(r"\s+", " ", match.group(1))).strip()


def html_to_text(html: str, url: str | None = None) -> str:
    """Main-content extraction via trafilatura, with a crude tag-stripping fallback."""
    text = trafilatura.extract(
        html,
        url=url if url and url.startswith("http") else None,
        include_comments=False,
        include_tables=True,
        favor_recall=True,
    ) or ""
    if not text.strip():
        text = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = htmllib.unescape(re.sub(r"\s+", " ", text))
    return text.strip()[:MAX_PAGE_CHARS]


def fetch_page(url: str) -> Page:
    html = fetch_html(url)
    return Page(url=url, title=title_of(html), text=html_to_text(html, url))


def extract_links(html: str, base_url: str) -> list[str]:
    """All absolute http(s) links on a listing page, fragments stripped, order preserved."""
    seen: set[str] = set()
    links: list[str] = []
    for href in re.findall(r"""href\s*=\s*["']([^"']+)["']""", html, flags=re.I):
        absolute, _ = urldefrag(urljoin(base_url, href.strip()))
        if urlparse(absolute).scheme not in ("http", "https"):
            continue
        if absolute not in seen:
            seen.add(absolute)
            links.append(absolute)
    return links


def normalize_url(url: str) -> str:
    """Canonical form used for duplicate checks: lowercase host, no fragment, no trailing slash, no tracking params."""
    url, _ = urldefrag(url.strip())
    parsed = urlparse(url)
    query = "&".join(p for p in parsed.query.split("&") if p and not p.lower().startswith(("utm_", "fbclid", "gclid")))
    path = parsed.path.rstrip("/") or "/"
    rebuilt = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}"
    return f"{rebuilt}?{query}" if query else rebuilt
