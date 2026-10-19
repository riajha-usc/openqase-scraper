"""Score the agent against the labelled synthetic set. Uses its own database so it never touches the real queue."""
import json
from collections import defaultdict

from .config import DATA_DIR
from .pipeline import Runner
from .sources import Seed
from .synth import LABELS_FILE

EVAL_DB = DATA_DIR / "eval.db"
REPORT = DATA_DIR / "eval_report.json"


def evaluate() -> dict:
    labels = json.loads(LABELS_FILE.read_text())
    if EVAL_DB.exists():
        EVAL_DB.unlink()
    runner = Runner(db_path=EVAL_DB, quiet=True)
    rows = []
    for label in labels:
        final = runner.process(Seed(url=label["url"], source_name="synthetic"))
        status = final.get("status", "unknown")
        triage_pass = status not in ("triaged_out", "fetch_failed")
        queued = status == "queued"
        rows.append({**label, "status": status, "triage_pass": triage_pass, "queued": queued,
                     "reason": (final.get("reason") or "")[:200]})
        mark = "ok " if (triage_pass == label["expected_triage_pass"] and queued == label["expected_queued"]) else "BAD"
        print(f"{mark} {label['category']:<22} -> {status:<14} {label['slug'][:40]}")

    per_category: dict[str, dict] = defaultdict(lambda: {"n": 0, "triage_correct": 0, "queue_correct": 0})
    tp = fp = fn = tn = 0
    for row in rows:
        bucket = per_category[row["category"]]
        bucket["n"] += 1
        bucket["triage_correct"] += row["triage_pass"] == row["expected_triage_pass"]
        bucket["queue_correct"] += row["queued"] == row["expected_queued"]
        if row["expected_queued"] and row["queued"]:
            tp += 1
        elif row["expected_queued"]:
            fn += 1
        elif row["queued"]:
            fp += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    summary = {
        "articles": len(rows),
        "queue_precision": round(precision, 3),
        "queue_recall": round(recall, 3),
        "queue_accuracy": round((tp + tn) / len(rows), 3) if rows else 0.0,
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "per_category": dict(per_category),
    }
    REPORT.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1))
    print("\n== summary")
    print(f"queue precision {precision:.2f}  recall {recall:.2f}  accuracy {summary['queue_accuracy']:.2f}  (n={len(rows)})")
    for name, bucket in per_category.items():
        print(f"  {name:<22} n={bucket['n']}  triage {bucket['triage_correct']}/{bucket['n']}  queue {bucket['queue_correct']}/{bucket['n']}")
    print(f"report: {REPORT}")
    return summary
