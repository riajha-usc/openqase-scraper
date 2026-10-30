"""Fetch and cache what the reviewer looks at: metadata (title, authors, abstract, date, venue), the PDF, the text.

Metadata comes from the publisher APIs where they exist (arXiv, HAL, Crossref) and from the page's meta tags
otherwise. PDFs and extracted text are cached under data/linkreview/ so the UI opens instantly on the second visit.
"""
from __future__ import annotations

import csv
import html as htmllib
import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx

from scraper.fetch import full_text_url, html_to_text, pdf_to_text, title_of
from .paths import AGENT_TSV, CACHE_DIR, META_JSON

# A browser-like agent: several of these publishers refuse the scraper's own identifier.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36 OpenQaseLinkReview/0.1")
TIMEOUT = 40.0
ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})(?:v\d+)?", re.I)
HAL_RE = re.compile(r"hal\.science/(hal-\d+)(?:v(\d+))?", re.I)
DOI_PATH_RE = re.compile(r"/(10\.\d{4,9}/[^\s?#]+)")


@dataclass
class Item:
    n: int
    url: str
    domain: str
    kind: str                      # arxiv | hal | doi | pdf | web
    agent_verdict: str = ""        # e.g. "dropped at triage"
    agent_model: str = ""          # e.g. "gpt-oss-120b"
    agent_evidence: str = ""
    checks: list[dict] = field(default_factory=list)  # [{test, passed, evidence}]
    title: str = ""
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    date: str = ""                 # ISO date when known
    year: int | None = None
    venue: str = ""                # journal / conference / publisher
    categories: list[str] = field(default_factory=list)
    doi: str = ""
    arxiv_id: str = ""
    landing_url: str = ""          # human-readable page
    pdf_url: str = ""
    meta_source: str = ""          # which API or heuristic filled the record
    meta_error: str = ""


# ----------------------------------------------------------------------------- the agent's TSV

def read_agent_rows() -> list[dict]:
    with AGENT_TSV.open(newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh, delimiter="\t")
        header = next(reader)
        rows = []
        for i, row in enumerate(reader, start=1):
            if not row or not row[0].strip():
                continue
            row += [""] * (3 - len(row))
            rows.append({"n": i, "url": row[0].strip(), "verdict": row[1].strip(), "evidence": row[2].strip()})
    return rows


def split_verdict(raw: str) -> tuple[str, str]:
    m = re.match(r"^(.*?)\s*\[([^\]]+)\]\s*$", raw)
    return (m.group(1).strip(), m.group(2).strip()) if m else (raw.strip(), "")


def parse_checks(evidence: str, verdict: str = "") -> list[dict]:
    """'FAIL Named organisation: ... PASS Quantum method: ...' -> one dict per test.

    Queued rows carry the extraction instead: 'Who | What | How'. Anything else becomes a single note.
    """
    if verdict.startswith("queued") and evidence.count(" | ") == 2:
        return [{"test": name, "passed": True, "evidence": part.strip()}
                for name, part in zip(("Who", "What", "How"), evidence.split(" | "))]
    parts = re.split(r"(?:^|\s)(PASS|FAIL)\s+([^:]{2,60}):\s*", evidence)
    if len(parts) < 4:
        return [{"test": "Agent note", "passed": None, "evidence": evidence}] if evidence else []
    checks = []
    for i in range(1, len(parts) - 2, 3):
        checks.append({"test": parts[i + 1].strip(), "passed": parts[i] == "PASS",
                       "evidence": parts[i + 2].strip(" |")})
    return checks


def classify(url: str) -> str:
    if ARXIV_RE.search(url):
        return "arxiv"
    if HAL_RE.search(url):
        return "hal"
    if "doi.org/" in url or DOI_PATH_RE.search(url):
        return "doi"
    if url.lower().endswith(".pdf"):
        return "pdf"
    return "web"


def base_items() -> list[Item]:
    items = []
    for row in read_agent_rows():
        verdict, model = split_verdict(row["verdict"])
        url = row["url"]
        items.append(Item(n=row["n"], url=url, domain=urlparse(url).netloc.removeprefix("www."),
                          kind=classify(url), agent_verdict=verdict, agent_model=model,
                          agent_evidence=row["evidence"], checks=parse_checks(row["evidence"], verdict)))
    return items


# ----------------------------------------------------------------------------- HTTP

def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/pdf,"
                                                              "application/json,*/*;q=0.8"},
                        follow_redirects=True, timeout=TIMEOUT)


def _is_pdf(resp: httpx.Response) -> bool:
    return "application/pdf" in resp.headers.get("content-type", "") or resp.content[:5] == b"%PDF-"


# ----------------------------------------------------------------------------- metadata sources

def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", htmllib.unescape(text)).strip()


def arxiv_metadata(items: list[Item]) -> None:
    """One export API call for all arXiv ids. Fills everything the UI shows for 48 of the 71 links."""
    by_id = {}
    for it in items:
        m = ARXIV_RE.search(it.url)
        if m:
            it.arxiv_id = m.group(1)
            it.landing_url = f"https://arxiv.org/abs/{it.arxiv_id}"
            it.pdf_url = f"https://arxiv.org/pdf/{it.arxiv_id}"
            by_id[it.arxiv_id] = it
    if not by_id:
        return
    ns = {"a": "http://www.w3.org/2005/Atom", "ax": "http://arxiv.org/schemas/atom"}
    ids = list(by_id)
    with _client() as client:
        for start in range(0, len(ids), 50):
            chunk = ids[start:start + 50]
            try:
                resp = client.get("https://export.arxiv.org/api/query",
                                  params={"id_list": ",".join(chunk), "max_results": len(chunk)})
                resp.raise_for_status()
                root = ET.fromstring(resp.text)
            except (httpx.HTTPError, ET.ParseError) as e:
                for aid in chunk:
                    by_id[aid].meta_error = f"arXiv API: {e}"
                continue
            for entry in root.findall("a:entry", ns):
                entry_id = (entry.findtext("a:id", "", ns) or "")
                m = re.search(r"(\d{4}\.\d{4,5})", entry_id)
                if not m or m.group(1) not in by_id:
                    continue
                it = by_id[m.group(1)]
                it.title = _clean(entry.findtext("a:title", "", ns))
                it.abstract = _clean(entry.findtext("a:summary", "", ns))
                it.authors = [_clean(a.findtext("a:name", "", ns)) for a in entry.findall("a:author", ns)]
                it.date = (entry.findtext("a:published", "", ns) or "")[:10]
                it.year = int(it.date[:4]) if it.date[:4].isdigit() else None
                primary = entry.find("ax:primary_category", ns)
                cats = [c.get("term", "") for c in entry.findall("a:category", ns)]
                if primary is not None and primary.get("term") in cats:
                    cats.remove(primary.get("term"))
                    cats.insert(0, primary.get("term"))
                it.categories = [c for c in cats if c]
                it.doi = entry.findtext("ax:doi", "", ns) or ""
                journal = entry.findtext("ax:journal_ref", "", ns) or ""
                it.venue = _clean(journal) if journal else "arXiv preprint"
                it.meta_source = "arxiv-api"
            time.sleep(1.0)


def hal_metadata(items: list[Item]) -> None:
    fields = ("halId_s,title_s,abstract_s,authFullName_s,producedDate_s,publicationDate_s,journalTitle_s,"
              "conferenceTitle_s,bookTitle_s,docType_s,files_s,fileMain_s,doiId_s,domain_s,uri_s")
    with _client() as client:
        for it in items:
            m = HAL_RE.search(it.url)
            if not m:
                continue
            hal_id = m.group(1)
            it.landing_url = it.url
            try:
                resp = client.get("https://api.archives-ouvertes.fr/search/",
                                  params={"q": f"halId_s:{hal_id}", "fl": fields, "wt": "json"})
                resp.raise_for_status()
                docs = resp.json().get("response", {}).get("docs", [])
            except (httpx.HTTPError, ValueError) as e:
                it.meta_error = f"HAL API: {e}"
                continue
            if not docs:
                it.meta_error = "HAL API: no record"
                continue
            d = docs[0]
            it.title = _clean((d.get("title_s") or [""])[0])
            it.abstract = _clean((d.get("abstract_s") or [""])[0])
            it.authors = d.get("authFullName_s") or []
            it.date = (d.get("publicationDate_s") or d.get("producedDate_s") or "")[:10]
            it.year = int(it.date[:4]) if it.date[:4].isdigit() else None
            it.venue = d.get("journalTitle_s") or d.get("conferenceTitle_s") or d.get("bookTitle_s") or \
                f"HAL {d.get('docType_s', '')}".strip()
            it.categories = [c.split(".")[-1] for c in (d.get("domain_s") or [])][:4]
            it.doi = d.get("doiId_s") or ""
            it.pdf_url = d.get("fileMain_s") or ((d.get("files_s") or [""])[0])
            it.meta_source = "hal-api"
            time.sleep(0.3)


def _doi_of(url: str) -> str:
    m = re.search(r"doi\.org/(10\.\d{4,9}/\S+)", url)
    if m:
        return m.group(1)
    m = re.search(r"/(?:article|chapter|doi|abs|full)/(10\.\d{4,9}/[^\s?#]+)", url)
    if m:
        return m.group(1)
    m = re.search(r"nature\.com/articles/([a-z0-9\-]+)", url, re.I)
    if m:
        return f"10.1038/{m.group(1)}"
    return ""


def crossref_metadata(items: list[Item]) -> None:
    with _client() as client:
        for it in items:
            if it.kind != "doi":
                continue
            doi = _doi_of(it.url)
            if not doi:
                continue
            it.doi = doi
            it.landing_url = f"https://doi.org/{doi}"
            try:
                resp = client.get(f"https://api.crossref.org/works/{doi}",
                                  headers={"User-Agent": f"{UA} (mailto:scraper@openqase.com)"})
                resp.raise_for_status()
                msg = resp.json()["message"]
            except (httpx.HTTPError, ValueError, KeyError) as e:
                it.meta_error = f"Crossref: {e}"
                continue
            it.title = _clean((msg.get("title") or [""])[0])
            it.abstract = _clean(msg.get("abstract", ""))
            it.authors = [" ".join(p for p in (a.get("given"), a.get("family")) if p) for a in msg.get("author", [])]
            parts = ((msg.get("published-print") or msg.get("published-online") or msg.get("issued") or {})
                     .get("date-parts") or [[None]])[0]
            if parts and parts[0]:
                it.year = int(parts[0])
                it.date = "-".join(f"{p:02d}" if i else str(p) for i, p in enumerate(parts))
            container = (msg.get("container-title") or [""])[0]
            it.venue = _clean(container or msg.get("publisher", ""))
            it.categories = [msg.get("type", "")] if msg.get("type") else []
            for link in msg.get("link", []):
                if "pdf" in (link.get("content-type") or ""):
                    it.pdf_url = link["URL"]
                    break
            it.meta_source = "crossref"
            time.sleep(0.3)


def _meta_tags(html: str) -> dict[str, list[str]]:
    tags: dict[str, list[str]] = {}
    for tag in re.findall(r"<meta\b[^>]*>", html, re.I):
        name = re.search(r"""(?:name|property)\s*=\s*["']([^"']+)["']""", tag, re.I)
        content = re.search(r"""content\s*=\s*["']([^"']*)["']""", tag, re.I)
        if name and content:  # AISeL prefixes the standard citation_* tags with bepress_
            key = name.group(1).lower().removeprefix("bepress_")
            tags.setdefault(key, []).append(htmllib.unescape(content.group(1)))
    return tags


def page_metadata(items: list[Item]) -> None:
    """Meta tags for publisher landing pages and corporate pages; PDF metadata for direct PDFs."""
    with _client() as client:
        for it in items:
            if it.kind in ("arxiv", "hal") or (it.meta_source and it.abstract):
                continue  # publisher pages fill in what Crossref lacks (usually the abstract)
            it.landing_url = it.landing_url or it.url
            try:
                resp = client.get(it.url)
                resp.raise_for_status()
            except httpx.HTTPError as e:
                it.meta_error = (it.meta_error + "; " if it.meta_error else "") + f"page: {e}"
                if it.kind == "pdf":  # the browser may still be allowed where the server is not
                    it.pdf_url = it.url
                    slug = re.sub(r"[-_]+", " ", Path(urlparse(it.url).path).stem).strip()
                    it.title = it.title or re.sub(r"^\d{4} \d{2} ", "", slug).capitalize()
                continue
            if _is_pdf(resp):
                it.pdf_url = it.url
                _pdf_metadata(it, resp.content)
                continue
            html = resp.text
            tags = _meta_tags(html)

            def first(*names: str) -> str:
                for name in names:
                    if tags.get(name):
                        return _clean(tags[name][0])
                return ""
            it.title = it.title or first("citation_title", "dc.title", "og:title") or title_of(html)
            it.abstract = it.abstract or first("citation_abstract", "dc.description", "description", "og:description")
            if not it.authors:
                it.authors = [_clean(a) for a in tags.get("citation_author", []) or tags.get("dc.creator", [])]
            date = first("citation_publication_date", "citation_date", "citation_online_date", "dc.date",
                         "article:published_time", "date")
            if date and not it.date:
                m = re.search(r"(\d{4})(?:[-/](\d{1,2}))?(?:[-/](\d{1,2}))?", date)
                if m:
                    it.year = int(m.group(1))
                    it.date = "-".join(p.zfill(2) for p in m.groups() if p)
            it.venue = it.venue or first("citation_conference_title", "citation_journal_title",
                                         "citation_publisher", "og:site_name") or it.domain
            it.doi = it.doi or first("citation_doi")
            it.pdf_url = it.pdf_url or full_text_url(html, it.url) or ""
            it.meta_source = (it.meta_source + "+" if it.meta_source else "") + "page-meta"
            if not it.year:
                m = re.search(r"(20[0-3]\d)", it.url)
                if m:
                    it.year = int(m.group(1))
            time.sleep(0.3)


def _pdf_metadata(it: Item, data: bytes) -> None:
    import io
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        info = reader.metadata or {}
        pdf_title = _clean(str(info.get("/Title") or ""))
        if re.search(r"credit|untitled|microsoft word|\.docx?|\.indd|\|", pdf_title, re.I) or len(pdf_title) < 12:
            pdf_title = ""  # layout-tool junk, not the paper's title
        it.title = it.title or pdf_title
        author = _clean(str(info.get("/Author") or ""))
        if author and not it.authors:
            it.authors = [a.strip() for a in re.split(r"[;,]| and ", author) if a.strip()]
        created = str(info.get("/CreationDate") or "")
        m = re.search(r"D:(\d{4})(\d{2})?(\d{2})?", created)
        if m and not it.date:
            it.year = int(m.group(1))
            it.date = "-".join(p for p in m.groups() if p)
        first_page = ""
        for page in reader.pages[:2]:
            try:
                first_page += (page.extract_text() or "") + "\n"
            except Exception:  # noqa: BLE001
                pass
        if not it.title:
            lines = [ln.strip() for ln in first_page.splitlines()[:40]
                     if 20 <= len(ln.strip()) <= 160
                     and not re.search(r"universit|department|faculty|\||@|http|copyright|©|\d{4}-\d{2}", ln, re.I)]
            slug = re.sub(r"[-_]+", " ", Path(urlparse(it.url).path).stem).strip()
            if len(slug.split()) >= 4:  # a descriptive file name beats guessing from the first page
                it.title = re.sub(r"^\d{4} \d{2} ", "", slug).capitalize()
            else:
                it.title = (max(lines[:6], key=len) if lines else slug or "Untitled PDF")[:200]
        if not it.abstract:
            m = re.search(r"abstract\s*[:.\-—]?\s*(.{200,2000}?)(?:\n\s*\n|keywords|1\s+introduction)",
                          first_page, re.I | re.S)
            it.abstract = _clean(m.group(1)) if m else _clean(first_page[:1200])
    except Exception as e:  # noqa: BLE001
        it.meta_error = f"PDF metadata: {e}"
    if not it.year:
        m = re.search(r"(20[0-3]\d)", it.url)
        if m:
            it.year = int(m.group(1))
    it.venue = it.venue or it.domain
    it.meta_source = (it.meta_source + "+" if it.meta_source else "") + "pdf"


# ----------------------------------------------------------------------------- cached PDF and text

def _pdf_path(n: int) -> Path:
    return CACHE_DIR / "pdf" / f"{n:03d}.pdf"


def _text_path(n: int) -> Path:
    return CACHE_DIR / "text" / f"{n:03d}.txt"


def get_pdf(it: Item | dict) -> Path | None:
    """The paper's PDF, downloaded once. None when there is no PDF or the publisher refuses."""
    d = it if isinstance(it, dict) else asdict(it)
    path = _pdf_path(d["n"])
    if path.exists():
        return path if path.stat().st_size > 0 else None
    path.parent.mkdir(parents=True, exist_ok=True)
    candidates = [u for u in (d.get("pdf_url"), d["url"] if d["kind"] == "pdf" else None) if u]
    with _client() as client:
        for url in candidates:
            try:
                resp = client.get(url)
                resp.raise_for_status()
            except httpx.HTTPError:
                continue
            if _is_pdf(resp):
                path.write_bytes(resp.content)
                return path
    path.write_bytes(b"")  # remember the miss so the UI does not retry on every open
    return None


def get_text(it: Item | dict) -> str:
    """Readable text: the full paper when the PDF is available, otherwise the landing page's main content."""
    d = it if isinstance(it, dict) else asdict(it)
    path = _text_path(d["n"])
    pdf_path = _pdf_path(d["n"])
    # A PDF dropped into the cache by hand (publishers that block the server) replaces the earlier page text.
    pdf_is_newer = pdf_path.exists() and pdf_path.stat().st_size > 0 and (
        not path.exists() or pdf_path.stat().st_mtime > path.stat().st_mtime)
    if path.exists() and not pdf_is_newer:
        return path.read_text(encoding="utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = ""
    pdf = get_pdf(d)
    if pdf:
        try:
            text = pdf_to_text(pdf.read_bytes())
        except Exception as e:  # noqa: BLE001
            text = f"[could not read PDF: {e}]"
    if len(text) < 500:
        with _client() as client:
            try:
                resp = client.get(d.get("landing_url") or d["url"])
                resp.raise_for_status()
                page_text = pdf_to_text(resp.content) if _is_pdf(resp) else html_to_text(resp.text, d["url"])
                text = page_text if len(page_text) > len(text) else text
            except Exception as e:  # noqa: BLE001
                text = text or f"[page could not be fetched: {e}]"
    path.write_text(text, encoding="utf-8")
    return text


def get_html(it: Item | dict) -> str | None:
    """The landing page's raw HTML (for link extraction); not cached beyond one call."""
    d = it if isinstance(it, dict) else asdict(it)
    with _client() as client:
        try:
            resp = client.get(d.get("landing_url") or d["url"])
            resp.raise_for_status()
        except httpx.HTTPError:
            return None
    return None if _is_pdf(resp) else resp.text


# ----------------------------------------------------------------------------- the metadata file

def load_meta() -> list[dict]:
    if META_JSON.exists():
        return json.loads(META_JSON.read_text(encoding="utf-8"))
    return [asdict(it) for it in base_items()]


def save_meta(items: list[Item]) -> None:
    META_JSON.write_text(json.dumps([asdict(it) for it in items], indent=1, ensure_ascii=False) + "\n",
                         encoding="utf-8")


def prefetch(with_content: bool = True, only_missing: bool = True) -> list[Item]:
    """Gather metadata for every link (and, optionally, the PDFs and text). Prints progress."""
    items = base_items()
    if only_missing and META_JSON.exists():
        previous = {d["url"]: d for d in json.loads(META_JSON.read_text(encoding="utf-8"))}
        for it in items:
            old = previous.get(it.url)
            if old and old.get("meta_source"):
                for k, v in old.items():
                    if k in Item.__dataclass_fields__ and k not in ("agent_verdict", "agent_model",
                                                                      "agent_evidence", "checks"):
                        setattr(it, k, v)
    todo = [it for it in items if not it.meta_source]
    print(f"{len(items)} links; metadata to fetch for {len(todo)}")
    arxiv_metadata([it for it in todo if it.kind == "arxiv"])
    hal_metadata([it for it in todo if it.kind == "hal"])
    crossref_metadata([it for it in todo if it.kind == "doi"])
    page_metadata(todo)
    save_meta(items)
    done = sum(1 for it in items if it.meta_source)
    print(f"metadata: {done}/{len(items)} filled; {sum(1 for it in items if it.meta_error)} with errors")
    if with_content:
        for it in items:
            have_pdf = get_pdf(it) is not None
            text = get_text(it)
            print(f"#{it.n:<3} {'pdf' if have_pdf else '   '} {len(text):>7} chars  {it.title[:70]}")
            time.sleep(0.5)
    return items


if __name__ == "__main__":
    import sys
    prefetch(with_content="--meta-only" not in sys.argv, only_missing="--refresh" not in sys.argv)
