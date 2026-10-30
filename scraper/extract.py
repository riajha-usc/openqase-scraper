"""Stage 2: fill the four required fields. A record missing any of them never reaches the queue."""
from typing import Optional

from .llm import parse
from .schemas import Extraction

EXTRACT_SYSTEM = """You extract a structured case-study record from ONE web page for OpenQase, an open catalogue of real-world quantum computing case studies. Use only what the page says. Never invent, never fill a field from general knowledge. If the page does not support a field, leave it null.

Four required fields:
- who: the named organisation(s) doing or using the quantum work; for a research paper, the authors' institution. If a quantum vendor or partner is named, append it after a semicolon, e.g. "Volkswagen; D-Wave".
- what: the specific business or operational problem addressed, in one sentence.
- how: the quantum method or hardware used. Must name an algorithm, method, hardware class or specific machine (QAOA, VQE, quantum annealing, trapped-ion, superconducting, D-Wave Advantage, IBM Heron, IonQ Forte...). "Used quantum computing" or "a quantum algorithm" is NOT sufficient: leave how null. Copy only what the page states: do not add qualifiers from general knowledge (if the page says "IBM's quantum processors", write that, not "IBM superconducting processors").
- outcome: the reported result, even preliminary, partial or negative. A plan, intention or future milestone is NOT an outcome: leave outcome null.

Also provide:
- title: a short, specific title a business reader would understand ("Volkswagen cuts taxi wait times 9% with D-Wave traffic routing").
- year: the year the work was reported, if stated or clearly implied.
- confidence: 0 to 1, how confident you are that this page describes genuine applied work with a reported result.
- notes: anything a human reviewer should double-check (unverified numbers, vendor-authored page, missing second source)."""


def extract(page_text: str, url: str, organisation: Optional[str] = None, problem: Optional[str] = None) -> Extraction:
    """organisation/problem: what triage found on this same page, so both stages agree on who and what."""
    hint = ""
    if organisation or problem:
        hint = (f"TRIAGE FINDINGS for this page: organisation: {organisation or 'unknown'}; problem: {problem or 'unknown'}. "
                "Use them for who and what unless the page clearly contradicts them.\n\n")
    user = f"{hint}URL: {url}\n\nPAGE TEXT:\n{page_text}"
    return reconcile(parse(EXTRACT_SYSTEM, user, Extraction, max_tokens=1800), organisation, problem)


def reconcile(result: Extraction, organisation: Optional[str], problem: Optional[str]) -> Extraction:
    """Triage already passed 'named organisation' and 'specific problem': never let extraction lose them."""
    if not (result.who or "").strip() and organisation:
        result.who = organisation
    if not (result.what or "").strip() and problem:
        result.what = problem
    return result
