"""Stage 3: the approval UI. One card at a time; Approve, Reject (reason required), Skip."""
import json
import os
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import db
from ..config import DB_PATH
from ..pipeline import Runner
from ..sources import Seed

app = FastAPI(title="OpenQase review queue")
_runner: Optional[Runner] = None


def _get_runner() -> Runner:
    """One Runner per process so the published case study list is fetched once."""
    global _runner
    if _runner is None:
        _runner = Runner(db_path=Path(os.environ.get("OQ_DB", DB_PATH)), quiet=True)
    return _runner
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
INCLUDE_SKIPPED = os.environ.get("OQ_REVIEW_INCLUDE_SKIPPED") == "1"


def _conn():
    return db.connect(Path(os.environ.get("OQ_DB", DB_PATH)))


@app.get("/", response_class=HTMLResponse)
def card(request: Request, error: Optional[str] = None, result: Optional[str] = None):
    conn = _conn()
    try:
        row = db.next_queued(conn, include_skipped=INCLUDE_SKIPPED)
        stats = db.counts(conn)
        record = dict(row) if row else None
        if record:
            record["extraction"] = json.loads(record["extraction_json"] or "{}")
            record["triage"] = json.loads(record["triage_json"] or "{}")
            record["evidence"] = _evidence_from(record["triage"])
    finally:
        conn.close()
    try:
        run_result = json.loads(result) if result else None
    except ValueError:
        run_result = None
    return templates.TemplateResponse(request, "card.html",
                                      {"record": record, "stats": stats, "error": error, "result": run_result})


def _evidence_from(triage: dict) -> list[dict]:
    """Per-test lines for display; falls back to the old single-paragraph reasoning for older rows."""
    if "named_organisation_evidence" in triage:
        from ..schemas import TriageResult
        return TriageResult.model_validate(triage).evidence_lines()
    return [{"test": "Reasoning", "passed": None, "evidence": triage.get("reasoning", "")}] if triage else []


@app.post("/run")
def run_url(url: str = Form(...), provenance: Optional[int] = Form(None)):
    """Run the agent on one pasted URL (synchronous; takes a few seconds per page)."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return RedirectResponse("/?error=Enter+a+full+http(s)+URL", status_code=303)
    final = _get_runner().process(Seed(url=url, source_name="manual", provenance=provenance or None))
    result = {"url": url, "status": "already_seen" if final.get("already_seen") else final.get("status", "unknown"),
              "title": (final.get("extraction") or {}).get("title"), "lines": []}
    triage = final.get("triage")
    if triage:
        from ..schemas import TriageResult
        result["lines"] = TriageResult.model_validate(triage).evidence_lines()
    if final.get("status") in ("extract_failed", "fetch_failed", "duplicate", "llm_error"):
        result["lines"].append({"test": "Reason", "passed": None, "evidence": final.get("reason", "")})
    return RedirectResponse(f"/?result={quote(json.dumps(result))}", status_code=303)


@app.post("/decide")
def decide(candidate_id: int = Form(...), action: str = Form(...), reason: str = Form("")):
    status = {"approve": "approved", "reject": "rejected", "skip": "skipped"}.get(action)
    if status is None:
        return RedirectResponse("/?error=unknown+action", status_code=303)
    if status == "rejected" and not reason.strip():
        return RedirectResponse("/?error=A+rejection+reason+is+required", status_code=303)
    conn = _conn()
    try:
        db.set_decision(conn, candidate_id, status, reason.strip() or None)
    finally:
        conn.close()
    return RedirectResponse("/", status_code=303)
