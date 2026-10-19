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


def _get(url: str) -> httpx.Response:
    try:
        response = httpx.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8"},
            follow_redirects=True,
            timeout=FETCH_TIMEOUT,
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        raise FetchError(f"{url}: {e}") from e
    return response


def fetch_html(url: str) -> str:
    if is_local(url):
        try:
            return _local_path(url).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            raise FetchError(f"{url}: {e}") from e
    return _get(url).text


def pdf_to_text(data: bytes) -> str:
    """Text of a PDF (papers, reports). Page by page so one broken page does not lose the rest."""
    import io
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001 - keep going on a malformed page
                continue
    except Exception as e:  # noqa: BLE001
        raise FetchError(f"could not read PDF: {e}") from e
    return re.sub(r"[ \t]+", " ", "\n".join(pages)).strip()


def full_text_url(html: str, url: str) -> str | None:
    """Where the full paper lives, for scholarly landing pages that only show an abstract.

    Publishers (arXiv, AISeL, Springer, IEEE, ACM...) put the PDF in a citation_pdf_url meta tag.
    arXiv abstract pages fall back to the constructed /pdf/ URL.
    """
    for tag in re.findall(r"<meta\b[^>]*>", html, re.I):
        if re.search(r"""name\s*=\s*["'][\w.:-]*citation_pdf_url["']""", tag, re.I):
            content = re.search(r"""content\s*=\s*["']([^"']+)["']""", tag, re.I)
            if content:
                return htmllib.unescape(content.group(1))
    arxiv = re.match(r"https?://arxiv\.org/abs/([\w.\-/]+)", url)
    if arxiv:
        return f"https://arxiv.org/pdf/{arxiv.group(1)}"
    return None


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
    if is_local(url):
        path = _local_path(url)
        if path.suffix.lower() == ".pdf":
            try:
                return Page(url=url, title=path.stem, text=pdf_to_text(path.read_bytes())[:MAX_PAGE_CHARS])
            except OSError as e:
                raise FetchError(f"{url}: {e}") from e
        html = fetch_html(url)
        return Page(url=url, title=title_of(html), text=html_to_text(html, url))
    response = _get(url)
    if "application/pdf" in response.headers.get("content-type", "") or response.content[:5] == b"%PDF-":
        return Page(url=url, title="", text=pdf_to_text(response.content)[:MAX_PAGE_CHARS])
    html = response.text
    title = title_of(html)
    text = html_to_text(html, url)
    pdf_url = full_text_url(html, url)
    if pdf_url:
        # Abstract pages are short; the judgement should be made on the paper itself.
        try:
            pdf = _get(pdf_url)
            if "application/pdf" in pdf.headers.get("content-type", "") or pdf.content[:5] == b"%PDF-":
                body = pdf_to_text(pdf.content)
                if len(body) > len(text):
                    text = (f"{text}\n\n[FULL TEXT from {pdf_url}]\n{body}")[:MAX_PAGE_CHARS]
        except FetchError:
            pass  # keep the landing-page text
    return Page(url=url, title=title, text=text)


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
