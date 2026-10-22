"""MCP server tests with a stubbed API: no network."""
import asyncio

import mcp_server.server as srv
from mcp_server.client import OpenQaseClient, normalise_type, page_url

INDEX = [
    {"id": "1", "title": "Volkswagen optimises taxi routing with D-Wave", "description": "Quantum annealing for traffic flow in Lisbon",
     "slug": "vw-dwave-traffic", "type": "case_studies", "metadata": {"companies": ["Volkswagen", "D-Wave"], "year": 2019}},
    {"id": "2", "title": "QAOA", "description": "Quantum Approximate Optimization Algorithm for combinatorial problems",
     "slug": "qaoa", "type": "algorithms", "metadata": {}},
    {"id": "3", "title": "Logistics", "description": "Routing, scheduling and supply chain optimisation",
     "slug": "logistics", "type": "industries", "metadata": {}},
]


class StubClient(OpenQaseClient):
    def __init__(self):
        super().__init__()
        self.calls = []

    def _get(self, path, params=None):
        self.calls.append((path, params))
        if path == "search-data":
            return INDEX
        if params and params.get("slug"):
            if params["slug"] == "missing":
                return {"error": "not found"}
            return {"slug": params["slug"], "title": "Full record", "main_content": "...", "deleted_at": None}
        if path == "quantum-hardware":
            return [{"slug": "ibm-heron", "name": "IBM Heron", "technology_type": "Superconducting", "vendor": None}]
        return {"items": [{"slug": "vw-dwave-traffic", "title": "VW", "year": 2019, "algorithms": ["annealing"], "partner_companies": []}],
                "pagination": {"page": 1, "pageSize": 20, "totalItems": 1, "totalPages": 1}}


def setup_module(module):
    srv._client = StubClient()


def test_search_ranks_title_and_companies_and_filters_by_type():
    results = srv.search_openqase("D-Wave routing")
    assert results[0]["slug"] == "vw-dwave-traffic" and results[0]["url"].endswith("/case-study/vw-dwave-traffic")
    assert [r["slug"] for r in srv.search_openqase("optimisation", type="industries")] == ["logistics"]
    assert srv.search_openqase("") == []


def test_list_handles_both_api_shapes():
    cs = srv.list_case_studies(page_size=20)
    assert cs["items"][0]["url"] == "https://www.openqase.com/case-study/vw-dwave-traffic"
    assert "partner_companies" not in cs["items"][0]  # empty lists are dropped from compact rows
    hw = srv.list_items("hardware")
    assert hw["items"][0]["technology_type"] == "Superconducting" and hw["pagination"] is None


def test_get_item_strips_internal_fields_and_reports_missing():
    rec = srv.get_case_study("vw-dwave-traffic")
    assert rec["url"].endswith("/case-study/vw-dwave-traffic") and "deleted_at" not in rec
    assert "error" in srv.get_item("algorithms", "missing")


def test_type_aliases_and_urls():
    assert normalise_type("Case_Study") == "case-studies" and normalise_type("hardware") == "quantum-hardware"
    assert page_url("algorithms", "qaoa") == "https://www.openqase.com/paths/algorithm/qaoa"
    try:
        normalise_type("recipes")
    except ValueError as e:
        assert "unknown type" in str(e)


def test_tools_are_registered():
    names = {t.name for t in asyncio.run(srv.server.list_tools())}
    assert {"search_openqase", "get_case_study", "list_case_studies", "list_items", "get_item", "content_types"} <= names
