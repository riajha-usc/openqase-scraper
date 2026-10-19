"""Stage 1 gate: the three-part triage test, applied inline while discovering."""
from typing import Optional

from .llm import parse
from .schemas import PROVENANCE_BY_SOURCE_TYPE, TriageResult

TRIAGE_SYSTEM = """You are the triage gate for OpenQase, an open catalogue of real-world quantum computing case studies. You read ONE web page and decide whether it describes a real, applied quantum computing case study.

Apply three tests. ALL three must pass.

1. Named organisation. A specific company, lab, or government agency is named as the organisation whose problem is being worked on. "Researchers", "a financial firm", "a major bank", "a European OEM" fail. A quantum vendor describing only its own hardware or product, with no named customer, partner or research institution applying it to a problem, fails.
2. Specific operational problem. A concrete task is named: portfolio optimisation, drug docking, route scheduling, battery materials simulation, fraud detection, and so on. Broad aspirations fail: "exploring quantum", "quantum-ready strategy", "quantum for finance".
3. Work done and a result reported. A run, experiment or deployment was actually executed, and some outcome is described, even preliminary, partial or negative (benchmark matched, error rate reduced, no advantage found). Investment announcements, funding rounds, grants, partnerships announced, roadmaps, planned whitepapers, and conference talk titles without results all fail. This test is the most important one; when in doubt, fail it.

Examples:
- "IBM is committing $50M to quantum computing research" -> fails test 3 (no work done).
- "Volkswagen used D-Wave to optimise traffic routing across 10,000 taxis and reduced average wait time by 9%" -> passes all three.
- "A European OEM ran QAOA on a logistics problem and found a 5% improvement" -> fails test 1 (anonymous).
- "Bank X announces a three-year quantum-readiness roadmap" -> fails tests 2 and 3.

Also classify the source type of the page itself:
- first_party: the organisation's own website or report, or a peer-reviewed paper / arXiv preprint with named institutional authors.
- specialist: specialist quantum press (The Quantum Insider, Quantum Computing Report), consulting firm reports, conference proceedings.
- general: general tech or business press, aggregators, investor or VC blogs.

Be strict and literal. In reasoning, give one line per test with the evidence quoted or paraphrased from the page. Do not infer work that the page does not describe."""


def triage(page_text: str, url: str) -> TriageResult:
    user = f"URL: {url}\n\nPAGE TEXT:\n{page_text}"
    return parse(TRIAGE_SYSTEM, user, TriageResult, max_tokens=4000)


def provenance_score(result: TriageResult, override: Optional[int] = None) -> int:
    """Source-level config wins over the model's classification of the page."""
    if override in (1, 2, 3):
        return override
    return PROVENANCE_BY_SOURCE_TYPE[result.source_type]
