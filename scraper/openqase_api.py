"""Read the published case studies from openqase.com so the agent can skip what is already catalogued."""
import json
import re
import time
from typing import Optional

import httpx

from .config import DATA_DIR, OPENQASE_API, USER_AGENT
from .fetch import normalize_url

CACHE = DATA_DIR / "openqase_published.json"
CACHE_TTL_SECONDS = 24 * 3600
KEEP = ("slug", "title", "description", "partner_companies", "quantum_companies", "algorithms",
        "quantum_hardware", "year", "resource_links")


def fetch_published(force: bool = False) -> list[dict]:
    if not force and CACHE.exists() and time.time() - CACHE.stat().st_mtime < CACHE_TTL_SECONDS:
        return json.loads(CACHE.read_text())
    items: list[dict] = []
    page = 1
    with httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=30.0) as client:
        while True:
            response = client.get(OPENQASE_API, params={"page": page, "pageSize": 100})
            response.raise_for_status()
            data = response.json()
            items.extend({k: item.get(k) for k in KEEP} for item in data["items"])
            if page >= data["pagination"]["totalPages"]:
                break
            page += 1
    CACHE.write_text(json.dumps(items, ensure_ascii=False, indent=1))
    return items


def known_urls(items: list[dict]) -> set[str]:
    """Source URLs already cited by a published case study (resource_links is a JSON list)."""
    urls: set[str] = set()
    for item in items:
        for link in item.get("resource_links") or []:
            url = link.get("url") if isinstance(link, dict) else link
            if isinstance(url, str) and url.startswith("http"):
                urls.add(normalize_url(url))
    return urls


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 2}


# Quantum vendors and generic words: sharing these with a published case study says nothing about a duplicate.
NOT_THE_CUSTOMER = {
    "wave", "dwave", "ibm", "ionq", "quantinuum", "rigetti", "pasqal", "quera", "xanadu", "zapata", "1qbit", "classiq",
    "honeywell", "orca", "google", "microsoft", "amazon", "braket", "azure", "quantum", "computing", "research", "labs",
    "lab", "inc", "ltd", "llc", "plc", "gmbh", "corp", "corporation", "company", "group", "holdings", "systems",
    "technologies", "technology", "university", "institute", "laboratory", "national", "college", "the", "and",
}


def find_possible_duplicate(items: list[dict], organisation: Optional[str], problem: Optional[str]) -> Optional[str]:
    """Flag a published case study that names the same organisation and overlaps on the problem.

    This is a hint for the reviewer card, not an automatic rejection: the same company can have
    several distinct case studies.
    """
    if not organisation:
        return None
    customer = organisation.split(";")[0]  # "who" is "customer; vendor": match on the customer only
    org_tokens = _tokens(customer) - NOT_THE_CUSTOMER
    if not org_tokens:
        return None
    problem_tokens = _tokens(problem or "")
    best: tuple[int, Optional[str]] = (0, None)
    for item in items:
        companies = " ".join((item.get("partner_companies") or []) + (item.get("quantum_companies") or []))
        haystack = _tokens(companies) | _tokens(item.get("title", ""))
        if not (org_tokens & haystack):
            continue
        overlap = len(problem_tokens & _tokens(item.get("description", "")))
        if overlap > best[0]:
            best = (overlap, item["slug"])
    return best[1] if best[0] >= 2 else None
