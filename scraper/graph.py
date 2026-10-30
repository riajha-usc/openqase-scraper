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
            lines = [f"{'PASS' if e['passed'] else 'FAIL'} {e['test']}: {e['evidence']}" for e in result.evidence_lines()]
            update["reason"] = "\n".join(lines)
        return update

    def extract_node(state: CandidateState) -> CandidateState:
        triage_data = state.get("triage") or {}
        result = extract(state["text"], state["url"], triage_data.get("organisation"), triage_data.get("problem"))
        update: CandidateState = {"extraction": result.model_dump()}
        if not result.complete:
            update["status"] = "extract_failed"
            update["reason"] = f"missing {', '.join(result.missing_fields())}"
            return update
        update["status"] = "queued"
        update["possible_duplicate_of"] = find_possible_duplicate(published, result.who, result.what)
        return update

    def store(state: CandidateState) -> CandidateState:
        triage_data = state.get("triage") or {}
        extraction = state.get("extraction") or {}
        candidate_id = db.upsert_candidate(
            conn,
            url=state["url"],
            source_name=state.get("source_name"),
            source_domain=urlparse(state["url"]).netloc.lower().removeprefix("www."),
            provenance=state.get("provenance"),
            status=state["status"],
            title=extraction.get("title") or state.get("title"),
            organisation=extraction.get("who") or triage_data.get("organisation"),
            problem=extraction.get("what") or triage_data.get("problem"),
            method=extraction.get("how"),
            outcome=extraction.get("outcome"),
            year=extraction.get("year"),
            triage_json=triage_data or None,
            extraction_json=extraction or None,
            text_excerpt=(state.get("text") or "")[:1500],
            page_text=state.get("text"),
            possible_duplicate_of=state.get("possible_duplicate_of"),
            reason=state.get("reason"),
        )
        return {"candidate_id": candidate_id}

    def after_check(state: CandidateState) -> str:
        if state.get("already_seen"):
            return END
        return "store" if state.get("status") else "fetch"

    def continue_or_store(state: CandidateState) -> str:
        return "store" if state.get("status") else "next"

    graph = StateGraph(CandidateState)
    graph.add_node("check_url", check_url)
    graph.add_node("fetch", fetch)
    graph.add_node("triage", triage_node)
    graph.add_node("extract", extract_node)
    graph.add_node("store", store)
    graph.add_edge(START, "check_url")
    graph.add_conditional_edges("check_url", after_check, {END: END, "store": "store", "fetch": "fetch"})
    graph.add_conditional_edges("fetch", continue_or_store, {"store": "store", "next": "triage"})
    graph.add_conditional_edges("triage", continue_or_store, {"store": "store", "next": "extract"})
    graph.add_edge("extract", "store")
    graph.add_edge("store", END)
    return graph.compile()
