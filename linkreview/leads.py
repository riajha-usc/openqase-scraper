"""What a lead source points at: its reference list (Semantic Scholar, when the paper is indexed), plus the
DOIs, arXiv ids and URLs in its text, plus outgoing links when the source is a web page."""
from __future__ import annotations

import os
import re
import threading
import time
from urllib.parse import urlparse

import httpx

from scraper.fetch import extract_links
from .content import UA, get_html, get_text

S2 = "https://api.semanticscholar.org/graph/v1/paper/{ident}/references"
S2_SEARCH = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_FIELDS = "title,year,venue,externalIds,url"
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>\]\)]+)")
ARXIV_RE = re.compile(r"(?:arxiv[:\s/]*(?:abs/|pdf/)?)(\d{4}\.\d{4,5})(?:v\d+)?", re.I)
URL_RE = re.compile(r"https?://[^\s\"'<>\]\)]+")
JUNK_PATHS = ("/privacy", "/terms", "/legal/", "/membership", "/subscribe", "/login", "/about", "/contact", "/cookie",
              "/sitemap", "/careers", "/api/", "/accessibility", "/help", "/faq", "/feed", "/rss", "/search", "/account",
              "/newsletter", "/advertis", "/policies", "/copyright")
JUNK_HOSTS = ("creativecommons.org", "orcid.org", "arxiv.org/licenses", "twitter.com", "x.com", "facebook.com",
              "linkedin.com", "youtube.com", "instagram.com", "google.com", "apple.com", "cookiepedia",
              "onetrust", "w3.org", "schema.org", "fonts.", "gstatic", "doubleclick", "addthis", "mailto:")


def _key(lead: dict) -> str:
    if lead.get("doi"):
        return "doi:" + lead["doi"].lower().rstrip(".")
    if lead.get("arxiv"):
        return "arxiv:" + lead["arxiv"]
    return "url:" + (lead.get("url") or "").lower().rstrip("/")


def _normalise_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


class RateLimited(RuntimeError):
    pass


# An API key allows 1 request per second across all endpoints; space calls out so two
# concurrent lead jobs never share a second.
S2_MIN_INTERVAL = 1.1
_s2_lock = threading.Lock()
_s2_last_call = 0.0


def _s2_get(client: httpx.Client, url: str, params: dict) -> httpx.Response:
    """Semantic Scholar's public pool is shared and often answers 429; back off a few times before giving up.
    An S2_API_KEY environment variable gives a private 1 req/s budget, which this call paces."""
    global _s2_last_call
    headers = {"x-api-key": os.environ["S2_API_KEY"]} if os.environ.get("S2_API_KEY") else {}
    for attempt in range(5):
        with _s2_lock:
            wait = S2_MIN_INTERVAL - (time.monotonic() - _s2_last_call)
            if wait > 0:
                time.sleep(wait)
            try:
                resp = client.get(url, params=params, headers=headers)
            finally:
                _s2_last_call = time.monotonic()
        if resp.status_code != 429:
            return resp
        time.sleep(2 ** (attempt + 1))  # 2, 4, 8, 16, 32 s
    raise RateLimited("Semantic Scholar rate-limited (press x to retry later, or set S2_API_KEY)")


def _s2_lookup_by_title(client: httpx.Client, title: str) -> str:
    """Semantic Scholar paper id for a title (for sources with no DOI or arXiv id, such as AISeL papers)."""
    if len(title) < 15:
        return ""
    resp = _s2_get(client, S2_SEARCH, params={"query": title, "limit": 3, "fields": "title"})
    if resp.status_code != 200:
        return ""
    wanted = _normalise_title(title)
    for paper in resp.json().get("data") or []:
        if _normalise_title(paper.get("title") or "") == wanted:
            return paper["paperId"]
    return ""


def _semantic_scholar(item: dict) -> tuple[list[dict], str]:
    ident = f"arXiv:{item['arxiv_id']}" if item.get("arxiv_id") else (f"DOI:{item['doi']}" if item.get("doi") else "")
    leads: list[dict] = []
    try:
        with httpx.Client(headers={"User-Agent": UA}, timeout=60) as client:
            if not ident:
                ident = _s2_lookup_by_title(client, item.get("title", ""))
                if not ident:
                    return [], "Semantic Scholar: no DOI or arXiv id, and no title match"
            offset = 0
            while True:
                resp = _s2_get(client, S2.format(ident=ident), {"fields": S2_FIELDS, "limit": 500, "offset": offset})
                if resp.status_code == 404:
                    return [], "Semantic Scholar does not index this paper"
                resp.raise_for_status()
                data = resp.json()
                refs = data.get("data") or []   # null, not [], when S2 has no reference list
                for ref in refs:
                    paper = ref.get("citedPaper") or {}
                    if not paper.get("title"):
                        continue
                    ext = paper.get("externalIds") or {}
                    doi = ext.get("DOI") or ""      # S2 sends explicit nulls, not missing keys
                    arxiv = ext.get("ArXiv") or ""
                    url = f"https://doi.org/{doi}" if doi else (f"https://arxiv.org/abs/{arxiv}" if arxiv
                                                               else paper.get("url") or "")
                    leads.append({"title": paper["title"], "year": paper.get("year"), "venue": paper.get("venue") or "",
                                  "doi": doi, "arxiv": arxiv, "url": url, "via": "references (Semantic Scholar)"})
                if data.get("next") is None or not data.get("data"):
                    break
                offset = data["next"]
    except RateLimited as e:
        return leads, str(e)
    except (httpx.HTTPError, ValueError) as e:
        return leads, f"Semantic Scholar: {e}"
    if not leads:
        return [], "Semantic Scholar indexes this paper but has no reference list for it"
    return leads, ""


# ----------------------------------------------------------------------------- OpenAlex (no rate-limit trouble)

OPENALEX = "https://api.openalex.org/works"
# Without a key OpenAlex shares a small daily budget per IP address (enough for a handful of lead sources a day);
# an OPENALEX_API_KEY environment variable gives the tool its own.
OA_PARAMS = {"mailto": "scraper@openqase.com", **({"api_key": os.environ["OPENALEX_API_KEY"]}
                                                 if os.environ.get("OPENALEX_API_KEY") else {})}


def _openalex_work(client: httpx.Client, item: dict) -> dict | None:
    """The OpenAlex record for this source: by DOI when there is one, otherwise by an exact title match."""
    if item.get("doi"):
        resp = client.get(f"{OPENALEX}/doi:{item['doi']}", params={**OA_PARAMS, "select": "id,title,referenced_works"})
        if resp.status_code == 200:
            return resp.json()
    title = item.get("title", "")
    if len(title) < 15:
        return None
    query = re.sub(r"[,:;|()\[\]{}\"']", " ", title)  # these are filter syntax in OpenAlex
    resp = client.get(OPENALEX, params={**OA_PARAMS, "filter": f"title.search:{query}", "per-page": 5,
                                        "select": "id,title,referenced_works"})
    if resp.status_code != 200:
        return None
    wanted = _normalise_title(title)
    for work in resp.json().get("results", []):
        if _normalise_title(work.get("title") or "") == wanted:
            return work
    return None


def _openalex(item: dict) -> tuple[list[dict], str]:
    leads: list[dict] = []
    try:
        with httpx.Client(headers={"User-Agent": UA}, timeout=60) as client:
            work = _openalex_work(client, item)
            if work is None:
                return [], "OpenAlex: paper not found"
            ids = [w.rsplit("/", 1)[-1] for w in work.get("referenced_works") or []]
            if not ids:
                return [], "OpenAlex: no reference list for this paper"
            for start in range(0, len(ids), 50):
                resp = client.get(OPENALEX, params={**OA_PARAMS, "filter": "openalex:" + "|".join(ids[start:start + 50]),
                                                    "per-page": 50,
                                                    "select": "id,title,doi,publication_year,primary_location,ids"})
                resp.raise_for_status()
                for w in resp.json().get("results", []):
                    if not w.get("title"):
                        continue
                    doi = (w.get("doi") or "").replace("https://doi.org/", "")
                    arxiv = ""
                    landing = ((w.get("primary_location") or {}).get("landing_page_url") or "")
                    m = re.search(r"arxiv\.org/abs/(\d{4}\.\d{4,5})", landing)
                    if m:
                        arxiv = m.group(1)
                    url = f"https://doi.org/{doi}" if doi else (landing or w["id"])
                    source = ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or ""
                    leads.append({"title": w["title"], "year": w.get("publication_year"), "venue": source,
                                  "doi": doi, "arxiv": arxiv, "url": url, "via": "references (OpenAlex)"})
    except (httpx.HTTPError, ValueError) as e:
        return leads, f"OpenAlex: {e}"
    return leads, ""


def _from_text(item: dict) -> list[dict]:
    text = get_text(item)
    if text.startswith("[page could not be fetched"):
        return []   # a placeholder error note, not the paper; its URLs are not citations
    leads: list[dict] = []
    own_doi = (item.get("doi") or "").lower()
    for m in DOI_RE.finditer(text):
        doi = m.group(1).rstrip(".,;")
        if doi.lower() != own_doi:
            leads.append({"title": "", "year": None, "venue": "", "doi": doi, "arxiv": "",
                          "url": f"https://doi.org/{doi}", "via": "DOI in text"})
    for m in ARXIV_RE.finditer(text):
        if m.group(1) != item.get("arxiv_id"):
            leads.append({"title": "", "year": None, "venue": "", "doi": "", "arxiv": m.group(1),
                          "url": f"https://arxiv.org/abs/{m.group(1)}", "via": "arXiv id in text"})
    for m in URL_RE.finditer(text):
        url = m.group(0).rstrip(".,;)")
        if "doi.org/" in url or "arxiv.org/" in url or any(j in url for j in JUNK_HOSTS) \
                or any(j in url.lower() for j in JUNK_PATHS):
            continue
        if urlparse(url).netloc == urlparse(item["url"]).netloc:
            continue
        leads.append({"title": "", "year": None, "venue": "", "doi": "", "arxiv": "", "url": url, "via": "URL in text"})
    return leads


def _from_page_links(item: dict) -> list[dict]:
    if item.get("kind") in ("arxiv", "hal", "pdf"):
        return []
    html = get_html(item)
    if not html:
        return []
    own_host = urlparse(item["url"]).netloc
    leads = []
    for url in extract_links(html, item["url"]):
        host = urlparse(url).netloc
        if host == own_host or any(j in url for j in JUNK_HOSTS) or any(j in url.lower() for j in JUNK_PATHS):
            continue
        if len(urlparse(url).path.strip("/")) < 2:  # bare home pages are not leads
            continue
        leads.append({"title": "", "year": None, "venue": "", "doi": "", "arxiv": "", "url": url,
                      "via": "link on page"})
    return leads[:150]


def extract_leads(item: dict) -> tuple[list[dict], str]:
    """All distinct leads behind one source, best-described first. Returns (leads, error_note)."""
    refs, error = _openalex(item)
    if not refs:  # second opinion: Semantic Scholar indexes some things OpenAlex lacks
        s2_refs, s2_error = _semantic_scholar(item)
        refs, error = s2_refs, "; ".join(e for e in (error, s2_error) if e) if not s2_refs else ""
    seen: dict[str, dict] = {}
    for lead in refs + _from_text(item) + _from_page_links(item):
        key = _key(lead)
        if key not in seen:
            seen[key] = lead
    return list(seen.values()), error
