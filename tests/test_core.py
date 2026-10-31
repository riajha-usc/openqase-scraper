"""Unit tests that need no network and no API key."""
import sqlite3
from pathlib import Path

from scraper import db
from scraper.fetch import extract_links, html_to_text, normalize_url, title_of
from scraper.openqase_api import find_possible_duplicate
from scraper.schemas import Extraction, TriageResult
from scraper.triage import provenance_score


def test_triage_passes_only_when_all_three_hold():
    base = dict(organisation="VW", problem="routing", result_summary="9%", source_type="first_party",
                named_organisation_evidence="VW named", specific_problem_evidence="routing", work_done_evidence="9% result")
    assert TriageResult(named_organisation=True, specific_problem=True, work_done_with_result=True, **base).passes
    failing = TriageResult(named_organisation=True, specific_problem=True, work_done_with_result=False, **base)
    assert not failing.passes
    assert failing.failed_tests() == ["work_done_with_result"]


def test_provenance_override_beats_model_classification():
    result = TriageResult(named_organisation=True, specific_problem=True, work_done_with_result=True, source_type="general",
                          named_organisation_evidence="a", specific_problem_evidence="b", work_done_evidence="c")
    assert [e["passed"] for e in result.evidence_lines()] == [True, True, True]
    assert provenance_score(result) == 1
    assert provenance_score(result, override=3) == 3


def test_extraction_completeness():
    partial = Extraction(who="VW", what="routing", how=None, outcome="9%", title="t", confidence=0.8)
    assert partial.missing_fields() == ["how"]
    assert not partial.complete
    assert Extraction(who="VW", what="routing", how="annealing", outcome="9%", title="t", confidence=0.8).complete


def test_normalize_url_strips_tracking_and_fragments():
    assert normalize_url("HTTPS://Example.com/a/?utm_source=x#top") == "https://example.com/a"
    assert normalize_url("https://example.com/a?page=2&utm_medium=y") == "https://example.com/a?page=2"


def test_html_helpers():
    html = "<html><head><title> Hello &amp; World </title></head><body><nav><a href='/about'>about</a></nav>" \
           "<article><p>" + "Quantum routing pilot cut wait times by nine percent. " * 20 + "</p>" \
           "<a href='https://example.com/news/one#x'>one</a><a href='/news/two'>two</a></article></body></html>"
    assert title_of(html) == "Hello & World"
    assert "wait times" in html_to_text(html)
    assert extract_links(html, "https://example.com/news/") == [
        "https://example.com/about", "https://example.com/news/one", "https://example.com/news/two"]


def test_db_roundtrip(tmp_path: Path):
    conn = db.connect(tmp_path / "t.db")
    cid = db.upsert_candidate(conn, url="https://x.test/a", status="queued", provenance=2, organisation="A",
                              triage_json={"a": 1})
    low = db.upsert_candidate(conn, url="https://x.test/b", status="queued", provenance=3, organisation="B")
    assert db.url_known(conn, "https://x.test/a")
    assert db.next_queued(conn)["id"] == low  # higher provenance first
    db.set_decision(conn, low, "approved")
    assert db.next_queued(conn)["id"] == cid
    db.set_decision(conn, cid, "skipped")
    assert db.next_queued(conn) is None
    assert db.next_queued(conn, include_skipped=True)["id"] == cid
    assert db.reset_skipped(conn) == 1
    assert db.counts(conn)["queued"] == 1 and db.counts(conn)["approved"] == 1
    # upsert by URL updates instead of duplicating
    db.upsert_candidate(conn, url="https://x.test/a", status="rejected")
    assert conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 2


def test_possible_duplicate_needs_org_and_problem_overlap():
    published = [{"slug": "vw-dwave-traffic", "title": "Volkswagen optimises taxi routing with D-Wave",
                  "description": "Volkswagen used quantum annealing to optimise taxi traffic routing in Lisbon",
                  "partner_companies": ["Volkswagen"], "quantum_companies": ["D-Wave"]}]
    assert find_possible_duplicate(published, "Volkswagen; D-Wave", "optimise taxi routing in Lisbon") == "vw-dwave-traffic"
    assert find_possible_duplicate(published, "Volkswagen", "battery materials simulation") is None
    assert find_possible_duplicate(published, "Airbus", "taxi routing") is None
    # a shared quantum vendor is not a shared customer
    assert find_possible_duplicate(published, "SkyWater Technology; D-Wave", "optimise taxi routing in Lisbon") is None


def test_extraction_keeps_triage_organisation_and_problem():
    from scraper.extract import reconcile
    missing = Extraction(who=None, what="", how="QAOA", outcome="5% better", title="t", confidence=0.7)
    fixed = reconcile(missing, "Airbus", "aircraft loading")
    assert fixed.who == "Airbus" and fixed.what == "aircraft loading" and fixed.complete
    kept = reconcile(Extraction(who="BBVA", what="CVA", how="QAE", outcome="ok", title="t", confidence=0.9), "Other", "x")
    assert kept.who == "BBVA" and kept.what == "CVA"


def test_work_type_is_optional_and_recorded():
    base = dict(named_organisation=True, specific_problem=True, work_done_with_result=True, source_type="first_party",
                named_organisation_evidence="a", specific_problem_evidence="b", work_done_evidence="c")
    assert TriageResult(**base).work_type is None
    assert TriageResult(**base, work_type="applied_research").passes


def test_page_text_column_is_added_and_stored(tmp_path: Path):
    conn = db.connect(tmp_path / "t.db")
    db.upsert_candidate(conn, url="https://x.test/p", status="queued", page_text="full paper text")
    assert conn.execute("SELECT page_text FROM candidates").fetchone()[0] == "full paper text"


def test_rerun_keeps_old_row_when_the_llm_call_fails(tmp_path: Path, monkeypatch):
    from scraper import graph
    from scraper.pipeline import Runner
    from scraper.sources import Seed

    def rate_limited(*args, **kwargs):
        raise RuntimeError("Provider rate limit")

    monkeypatch.setattr(graph, "triage", rate_limited)
    runner = Runner(db_path=tmp_path / "t.db", published=[], quiet=True)
    db.upsert_candidate(runner.conn, url="https://x.test/p", status="triaged_out", page_text="old text")
    result = runner.process(Seed(url="https://x.test/p", source_name="spreadsheet", text="new text"), rerun=True)
    assert result["status"] == "llm_error"
    assert runner.conn.execute("SELECT status FROM candidates").fetchone()[0] == "triaged_out"
