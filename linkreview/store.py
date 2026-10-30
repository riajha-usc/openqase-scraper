"""The reviewer's labels and the leads behind lead sources. Both are plain TSVs in docs/ so they can be committed
and handed over as a dataset; the leads also keep a JSON twin with everything the UI shows."""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import threading
from pathlib import Path

from .paths import LEADS_TSV, VERDICTS_TSV

LEADS_JSON = LEADS_TSV.with_suffix(".json")
LABELS = ("yes", "no", "lead", "maybe")
_lock = threading.Lock()


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ----------------------------------------------------------------------------- labels

def load_verdicts() -> dict[int, dict]:
    """{n: {url, label, note, decided_at}} from the TSV; rows without a label carry a note only."""
    if not VERDICTS_TSV.exists():
        return {}
    out: dict[int, dict] = {}
    with VERDICTS_TSV.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            try:
                n = int(row["n"])
            except (KeyError, ValueError):
                continue
            out[n] = {"url": row.get("url", ""), "label": row.get("label", ""), "note": row.get("note", ""),
                      "decided_at": row.get("decided_at", "")}
    return out


def save_verdicts(verdicts: dict[int, dict]) -> None:
    lines = ["n\turl\tlabel\tnote\tdecided_at"]
    for n in sorted(verdicts):
        v = verdicts[n]
        if not v.get("label") and not v.get("note"):
            continue
        note = (v.get("note") or "").replace("\t", " ").replace("\n", " ")
        lines.append(f"{n}\t{v['url']}\t{v.get('label', '')}\t{note}\t{v.get('decided_at', '')}")
    _atomic_write(VERDICTS_TSV, "\n".join(lines) + "\n")


def set_verdict(n: int, url: str, label: str | None, note: str | None) -> dict:
    """Set the label and/or note for one link. label '' clears it; None leaves it as it is (same for note)."""
    if label not in LABELS and label not in ("", None):
        raise ValueError(f"label must be one of {LABELS} or empty")
    with _lock:
        verdicts = load_verdicts()
        current = verdicts.get(n, {"url": url, "label": "", "note": "", "decided_at": ""})
        current["url"] = url
        if label is not None:
            current["label"] = label
            current["decided_at"] = _now() if label else ""
        if note is not None:
            current["note"] = note.strip()
        verdicts[n] = current
        save_verdicts(verdicts)
        if label is not None:
            _write_leads_tsv(load_leads())   # source_label column follows the current label
    return current


# ----------------------------------------------------------------------------- leads

def load_leads() -> dict[int, dict]:
    if not LEADS_JSON.exists():
        return {}
    return {int(k): v for k, v in json.loads(LEADS_JSON.read_text(encoding="utf-8")).items()}


def _write_leads_json(all_leads: dict[int, dict]) -> None:
    _atomic_write(LEADS_JSON, json.dumps({str(k): v for k, v in sorted(all_leads.items())},
                                         indent=1, ensure_ascii=False) + "\n")


def _write_leads_tsv(all_leads: dict[int, dict]) -> None:
    """Flat TSV for Ria: one row per lead, from sources currently labelled yes or lead.
    source_label is looked up at write time so relabelling a source keeps the file truthful.
    Caller holds _lock."""
    labels = {k: v.get("label", "") for k, v in load_verdicts().items()}
    lines = ["source_n\tsource_url\tsource_label\tlead_url\tlead_title\tlead_year\tlead_venue\tfound_via"]
    for k in sorted(all_leads):
        label = labels.get(k, "")
        if label not in ("yes", "lead"):
            continue
        entry = all_leads[k]
        for lead in entry["leads"]:
            cells = [str(k), entry["url"], label, lead.get("url", ""), lead.get("title", ""),
                     str(lead.get("year") or ""), lead.get("venue", ""), lead.get("via", "")]
            lines.append("\t".join(c.replace("\t", " ").replace("\n", " ") for c in cells))
    _atomic_write(LEADS_TSV, "\n".join(lines) + "\n")


def save_leads(n: int, url: str, leads: list[dict], error: str = "") -> dict:
    """Store the leads found behind one source and regenerate the flat TSV."""
    with _lock:
        all_leads = load_leads()
        all_leads[n] = {"url": url, "found_at": _now(), "error": error, "leads": leads}
        _write_leads_json(all_leads)
        _write_leads_tsv(all_leads)
    return all_leads[n]


def drop_leads(n: int) -> None:
    with _lock:
        all_leads = load_leads()
        if n in all_leads:
            del all_leads[n]
            _write_leads_json(all_leads)
            _write_leads_tsv(all_leads)
