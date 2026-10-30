"""The review UI's server: static page plus a small JSON API over the metadata, labels and leads.

    .venv/bin/python -m linkreview.server   # http://localhost:8010
"""
from __future__ import annotations

import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel

from . import content, leads as leads_mod, store
from .paths import LEADS_TSV, VERDICTS_TSV

app = FastAPI(title="OpenQase link review")
STATIC = Path(__file__).parent / "static"
_pool = ThreadPoolExecutor(max_workers=2)
_lead_jobs: dict[int, str] = {}   # n -> "running" | error text
_jobs_lock = threading.Lock()


def _items() -> dict[int, dict]:
    return {d["n"]: d for d in content.load_meta()}


def _item(n: int) -> dict:
    item = _items().get(n)
    if item is None:
        raise HTTPException(404, f"no link #{n}")
    return item


def _merged(item: dict, verdicts: dict, all_leads: dict) -> dict:
    v = verdicts.get(item["n"], {})
    entry = all_leads.get(item["n"])
    pdf = content._pdf_path(item["n"])
    out = dict(item)
    out.update({
        "label": v.get("label", ""), "note": v.get("note", ""), "decided_at": v.get("decided_at", ""),
        "leads_count": len(entry["leads"]) if entry else 0,
        "leads_status": _lead_jobs.get(item["n"], "done" if entry else ""),
        "has_pdf": (pdf.stat().st_size > 0) if pdf.exists() else None,
        "has_text": content._text_path(item["n"]).exists(),
    })
    return out


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/api/items")
def list_items():
    verdicts, all_leads = store.load_verdicts(), store.load_leads()
    return {"items": [_merged(it, verdicts, all_leads) for it in content.load_meta()],
            "files": {"labels": str(VERDICTS_TSV), "leads": str(LEADS_TSV)}}


@app.get("/api/items/{n}")
def get_item(n: int):
    return _merged(_item(n), store.load_verdicts(), store.load_leads())


@app.get("/api/items/{n}/text", response_class=PlainTextResponse)
def get_text(n: int):
    return content.get_text(_item(n))


@app.api_route("/api/items/{n}/pdf", methods=["GET", "HEAD"])
def get_pdf(n: int):
    path = content.get_pdf(_item(n))
    if path is None:
        raise HTTPException(404, "no PDF for this link")
    return FileResponse(path, media_type="application/pdf", headers={"Content-Disposition": "inline"})


class Verdict(BaseModel):
    label: Optional[str] = None   # "yes" | "no" | "lead" | "maybe" | "" (clear) | None (unchanged)
    note: Optional[str] = None


@app.post("/api/items/{n}/verdict")
def set_verdict(n: int, body: Verdict):
    item = _item(n)
    try:
        store.set_verdict(n, item["url"], body.label, body.note)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if body.label in ("yes", "lead") and n not in store.load_leads():
        _start_leads(item)   # a case study's own references are a discovery seed too
    return get_item(n)


def _start_leads(item: dict) -> None:
    n = item["n"]
    with _jobs_lock:
        if _lead_jobs.get(n) == "running":
            return
        _lead_jobs[n] = "running"

    def job():
        try:
            found, error = leads_mod.extract_leads(item)
            store.save_leads(n, item["url"], found, error)
            with _jobs_lock:
                _lead_jobs.pop(n, None)
        except Exception as e:  # noqa: BLE001 - surface the failure in the UI instead of a dead job
            traceback.print_exc()
            with _jobs_lock:
                _lead_jobs[n] = f"error: {e}"
    _pool.submit(job)


@app.get("/api/items/{n}/leads")
def get_leads(n: int):
    _item(n)
    entry = store.load_leads().get(n)
    return {"status": _lead_jobs.get(n, "done" if entry else "none"), "entry": entry}


@app.post("/api/items/{n}/leads")
def rerun_leads(n: int):
    item = _item(n)
    store.drop_leads(n)
    _start_leads(item)
    return {"status": "running"}


def main(port: int = 8010) -> None:
    import uvicorn
    uvicorn.run("linkreview.server:app", host="127.0.0.1", port=port, reload=False)


if __name__ == "__main__":
    main()
