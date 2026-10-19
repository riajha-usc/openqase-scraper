"""Synthetic test data: realistic articles written from published OpenQase case studies, with known labels.

For each case study we generate one article per category. Two categories should pass triage and be queued,
four should be stopped somewhere in the pipeline. That gives a labelled set to score the agent against.
"""
import json
import random
from html import escape
from pathlib import Path

from .config import DATA_DIR
from .llm import parse
from .openqase_api import fetch_published
from .schemas import SyntheticSet

SYNTH_DIR = DATA_DIR / "synthetic"
LABELS_FILE = SYNTH_DIR / "labels.json"

# category -> (description for the writer, expected to pass triage, expected to reach the queue)
CATEGORIES: dict[str, tuple[str, bool, bool]] = {
    "pass_first_party": (
        "A case-study page on the user organisation's own website describing the completed work, the quantum method or hardware used, and the result.",
        True, True),
    "pass_specialist_press": (
        "A specialist quantum-industry press article reporting the completed work, naming the organisation and the method, quoting a spokesperson, and stating the result.",
        True, True),
    "fail_investment": (
        "A news article about the SAME organisation announcing an investment, funding round, grant or partnership in quantum computing. No experiment has been run and no result exists; only intentions and quotes about the future.",
        False, False),
    "fail_roadmap": (
        "A strategy piece or 'quantum-ready roadmap' published by the SAME organisation: goals, timelines and planned pilots, with no work completed and no result.",
        False, False),
    "fail_anonymous": (
        "A press article describing the same completed work and result but never naming the organisation: use phrases like 'a major European bank' or 'a leading automaker'. The quantum vendor may be named.",
        False, False),
    "fail_no_method": (
        "A short general-press article saying the organisation 'used quantum computing' on the problem and obtained the result, but never naming any algorithm, method, hardware class or machine.",
        True, False),
}

SYNTH_SYSTEM = """You write realistic synthetic web articles used to test a classifier that detects real quantum computing case studies. Each article must read like a genuine page of its category: plausible publication, date, byline, quotes, numbers consistent with the facts given. Do not say or hint that the article is synthetic or a test. Keep each article 250 to 450 words of plain text with paragraphs separated by blank lines, no HTML, no markdown headings. Write exactly one article per requested category, and set each article's category field to the requested category name."""


def _prompt(case_study: dict) -> str:
    facts = {k: case_study.get(k) for k in ("title", "description", "partner_companies", "quantum_companies",
                                             "algorithms", "quantum_hardware", "year")}
    body = (case_study.get("main_content") or "")[:5000]
    categories = "\n".join(f"- {name}: {desc}" for name, (desc, _, _) in CATEGORIES.items())
    return (f"FACTS (JSON):\n{json.dumps(facts, ensure_ascii=False)}\n\nSOURCE TEXT:\n{body}\n\n"
            f"Write one article for each category:\n{categories}")


def _wrap_html(title: str, body: str) -> str:
    paragraphs = "\n".join(f"<p>{escape(p.strip())}</p>" for p in body.split("\n\n") if p.strip())
    return f"<!doctype html><html><head><meta charset='utf-8'><title>{escape(title)}</title></head><body><article><h1>{escape(title)}</h1>\n{paragraphs}\n</article></body></html>\n"


def generate(n: int = 5, seed: int = 0, slugs: list[str] | None = None) -> list[dict]:
    """Write n case studies x len(CATEGORIES) articles to data/synthetic and return the labels."""
    SYNTH_DIR.mkdir(parents=True, exist_ok=True)
    published = fetch_published()
    if slugs:
        chosen = [cs for cs in published if cs["slug"] in slugs]
    else:
        rng = random.Random(seed)
        chosen = rng.sample(published, min(n, len(published)))
    labels: list[dict] = json.loads(LABELS_FILE.read_text()) if LABELS_FILE.exists() else []
    existing = {label["file"] for label in labels}
    for case_study in chosen:
        print(f"== generating {len(CATEGORIES)} articles for {case_study['slug']}")
        result = parse(SYNTH_SYSTEM, _prompt(case_study), SyntheticSet, max_tokens=16000)
        for article in result.articles:
            if article.category not in CATEGORIES:
                print(f"   skipped unknown category {article.category!r}")
                continue
            filename = f"{case_study['slug']}__{article.category}.html"
            path = SYNTH_DIR / filename
            path.write_text(_wrap_html(article.title, article.body), encoding="utf-8")
            if filename in existing:
                labels = [label for label in labels if label["file"] != filename]
