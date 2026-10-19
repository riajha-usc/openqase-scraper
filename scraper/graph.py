"""Per-candidate agent as a LangGraph state graph.

    START -> check_url -> fetch -> triage -> extract -> store -> END
                 |           |         |          |
                 +-----------+---------+----------+--> store (duplicate / fetch_failed / triaged_out / extract_failed)

Triage runs inline, right after the fetch, so nothing that fails the test is collected for later filtering.
"""
import sqlite3
from typing import Optional, TypedDict
from urllib.parse import urlparse

from langgraph.graph import END, START, StateGraph

from . import db
from .extract import extract
from .fetch import FetchError, fetch_page, normalize_url
from .openqase_api import find_possible_duplicate, known_urls
from .triage import provenance_score, triage


class CandidateState(TypedDict, total=False):
    url: str
    source_name: str
    provenance_override: Optional[int]
    prefetched_title: Optional[str]
    prefetched_text: Optional[str]
    title: str
    text: str
    triage: dict
    extraction: dict
    provenance: int
    possible_duplicate_of: Optional[str]
    status: str
    reason: str
    already_seen: bool
    candidate_id: int


def build_graph(conn: sqlite3.Connection, published: list[dict]):
    published_urls = known_urls(published)

    def check_url(state: CandidateState) -> CandidateState:
        url = normalize_url(state["url"])
        if db.url_known(conn, url):
            return {"url": url, "already_seen": True}
        if url in published_urls:
            return {"url": url, "status": "duplicate", "reason": "source URL already cited by a published OpenQase case study"}
        return {"url": url}

    def fetch(state: CandidateState) -> CandidateState:
        if state.get("prefetched_text"):
            return {"title": state.get("prefetched_title") or "", "text": state["prefetched_text"]}
        try:
            page = fetch_page(state["url"])
        except FetchError as e:
            return {"status": "fetch_failed", "reason": str(e)}
        if len(page.text) < 200:
            return {"status": "fetch_failed", "reason": f"only {len(page.text)} chars of text extracted"}
        return {"title": page.title, "text": page.text}

    def triage_node(state: CandidateState) -> CandidateState:
        result = triage(state["text"], state["url"])
        score = provenance_score(result, state.get("provenance_override"))
        update: CandidateState = {"triage": result.model_dump(), "provenance": score}
        if not result.passes:
            update["status"] = "triaged_out"
            update["reason"] = f"failed {', '.join(result.failed_tests())}: {result.reasoning}"
        return update

    def extract_node(state: CandidateState) -> CandidateState:
        result = extract(state["text"], state["url"])
        update: CandidateState = {"extraction": result.model_dump()}
        if not result.complete:
            update["status"] = "extract_failed"
            update["reason"] = f"missing {', '.join(result.missing_fields())}"
            return update
        update["status"] = "queued"
        update["possible_duplicate_of"] = find_possible_duplicate(published, result.who, result.what)
        return update
