"""Stage 3: the approval UI. One card at a time; Approve, Reject (reason required), Skip."""
import json
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import db
from ..config import DB_PATH

app = FastAPI(title="OpenQase review queue")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
INCLUDE_SKIPPED = os.environ.get("OQ_REVIEW_INCLUDE_SKIPPED") == "1"


def _conn():
    return db.connect(Path(os.environ.get("OQ_DB", DB_PATH)))


@app.get("/", response_class=HTMLResponse)
def card(request: Request, error: Optional[str] = None):
    conn = _conn()
    try:
        row = db.next_queued(conn, include_skipped=INCLUDE_SKIPPED)
        stats = db.counts(conn)
        record = dict(row) if row else None
        if record:
            record["extraction"] = json.loads(record["extraction_json"] or "{}")
            record["triage"] = json.loads(record["triage_json"] or "{}")
    finally:
        conn.close()
    return templates.TemplateResponse(request, "card.html", {"record": record, "stats": stats, "error": error})


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
