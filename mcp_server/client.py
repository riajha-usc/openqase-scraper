"""Thin client over the OpenQase public API with an in-memory cache."""
import re
import time
from typing import Any, Optional

import httpx

BASE_URL = "https://www.openqase.com"
USER_AGENT = "OpenQaseMCP/0.1 (+https://openqase.com)"
CACHE_TTL_SECONDS = 15 * 60

# API path -> (search-data type name, public page prefix)
TYPES: dict[str, tuple[str, str]] = {
    "case-studies": ("case_studies", "/case-study/"),
    "algorithms": ("algorithms", "/paths/algorithm/"),
    "industries": ("industries", "/paths/industry/"),
    "personas": ("personas", "/paths/persona/"),
    "quantum-hardware": ("quantum_hardware", "/paths/quantum-hardware/"),
    "quantum-software": ("quantum_software", "/paths/quantum-software/"),
    "quantum-companies": ("quantum_companies", "/paths/quantum-companies/"),
    "partner-companies": ("partner_companies", "/paths/partner-companies/"),
    "blog-posts": ("blog_posts", "/blog/"),
}
SEARCH_TYPE_TO_API = {search: api for api, (search, _) in TYPES.items()}
LIST_FIELDS = ("slug", "name", "title", "description", "year", "partner_companies", "quantum_companies",
               "algorithms", "quantum_hardware", "quantum_software", "technology_type", "vendor", "qubit_count",
               "published_at")


def normalise_type(value: str) -> str:
    key = value.strip().lower().replace("_", "-")
    aliases = {"case-study": "case-studies", "algorithm": "algorithms", "industry": "industries", "persona": "personas",
               "hardware": "quantum-hardware", "software": "quantum-software", "companies": "quantum-companies",
               "partners": "partner-companies", "blog": "blog-posts", "blog-post": "blog-posts"}
    key = aliases.get(key, key)
    if key not in TYPES:
        raise ValueError(f"unknown type {value!r}; choose one of {', '.join(TYPES)}")
    return key


def page_url(api_type: str, slug: str) -> str:
    return f"{BASE_URL}{TYPES[api_type][1]}{slug}"


def _tokens(text: Any) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", str(text or "").lower()) if len(t) > 1}


class OpenQaseClient:
    def __init__(self, base_url: str = BASE_URL, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=timeout)
        self._cache: dict[str, tuple[float, Any]] = {}

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        key = f"{path}?{sorted((params or {}).items())}"
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL_SECONDS:
            return hit[1]
        response = self._http.get(f"{self.base_url}/api/{path}", params=params)
        response.raise_for_status()
        data = response.json()
        self._cache[key] = (time.time(), data)
        return data

    # ---- listings -------------------------------------------------------
    def list_items(self, api_type: str, page: int = 1, page_size: int = 50) -> dict:
        """One page of a content type, compact fields only. Handles both {items,pagination} and bare-list shapes."""
        data = self._get(api_type, {"page": page, "pageSize": min(max(page_size, 1), 100)})
        items = data["items"] if isinstance(data, dict) else data
        pagination = data.get("pagination") if isinstance(data, dict) else None
        return {"type": api_type, "items": [self._compact(api_type, item) for item in items], "pagination": pagination}

    def get_item(self, api_type: str, slug: str) -> Optional[dict]:
        try:
            data = self._get(api_type, {"slug": slug})
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
