"""Pydantic schemas for every structured LLM call. These double as the record shapes."""
from typing import ClassVar, Literal, Optional

from pydantic import BaseModel, Field

SourceType = Literal["first_party", "specialist", "general"]

# Provenance score from the revised design: 3 first-party, 2 specialist, 1 general press.
PROVENANCE_BY_SOURCE_TYPE = {"first_party": 3, "specialist": 2, "general": 1}


class TriageResult(BaseModel):
    """The three-part triage test applied inline during discovery."""

    named_organisation: bool = Field(description="Test 1: a specific company, university, lab or agency is named as doing or using the quantum work; a research paper's own institution counts")
    organisation: Optional[str] = Field(default=None, description="The organisation name if test 1 passes")
    named_organisation_evidence: str = Field(description="One sentence of evidence for test 1, quoting the page where possible")
    specific_problem: bool = Field(description="Test 2: a concrete operational problem is named")
    problem: Optional[str] = Field(default=None, description="The problem if test 2 passes")
    specific_problem_evidence: str = Field(description="One sentence of evidence for test 2")
    work_done_with_result: bool = Field(description="Test 3: a run was executed and some outcome is reported")
    result_summary: Optional[str] = Field(default=None, description="One sentence on the reported outcome if test 3 passes")
    work_done_evidence: str = Field(description="One sentence of evidence for test 3")
    source_type: SourceType = Field(description="first_party, specialist, or general")
    work_type: Optional[Literal["industry_deployment", "applied_research"]] = Field(
        default=None, description="Set only if all three tests pass. Recorded for the reviewer; never a reason to fail")
    reasoning: str = Field(default="", description="Optional one-line overall note")

    TEST_LABELS: ClassVar[dict[str, str]] = {
        "named_organisation": "Named organisation",
        "specific_problem": "Specific operational problem",
        "work_done_with_result": "Work done with a result",
    }

    def evidence_lines(self) -> list[dict]:
        """One entry per test: label, pass/fail, evidence. The UI renders these as pointers."""
        return [
            {"test": self.TEST_LABELS["named_organisation"], "passed": self.named_organisation, "evidence": self.named_organisation_evidence},
            {"test": self.TEST_LABELS["specific_problem"], "passed": self.specific_problem, "evidence": self.specific_problem_evidence},
            {"test": self.TEST_LABELS["work_done_with_result"], "passed": self.work_done_with_result, "evidence": self.work_done_evidence},
        ]

    @property
    def passes(self) -> bool:
        return self.named_organisation and self.specific_problem and self.work_done_with_result

    def failed_tests(self) -> list[str]:
        names = []
        if not self.named_organisation:
            names.append("named_organisation")
        if not self.specific_problem:
            names.append("specific_problem")
        if not self.work_done_with_result:
            names.append("work_done_with_result")
        return names


class Extraction(BaseModel):
    """The four required fields of a queued record, plus reviewer aids."""

    who: Optional[str] = Field(default=None, description="Named organisation(s) using quantum computing")
    what: Optional[str] = Field(default=None, description="The specific business or operational problem, one sentence")
    how: Optional[str] = Field(default=None, description="Quantum method, algorithm, hardware class or machine. Null if the page only says 'used quantum computing'")
    outcome: Optional[str] = Field(default=None, description="The reported result, even preliminary or null. Never a plan")
    title: str = Field(description="Short, specific title for a business reader")
    year: Optional[int] = Field(default=None, description="Year the work was reported")
    confidence: float = Field(ge=0, le=1, description="Confidence this is a genuine applied case study")
    notes: str = Field(default="", description="Anything a human reviewer should double-check")

    def missing_fields(self) -> list[str]:
        return [name for name in ("who", "what", "how", "outcome") if not (getattr(self, name) or "").strip()]

    @property
    def complete(self) -> bool:
        return not self.missing_fields()


class SyntheticArticle(BaseModel):
    category: str
    title: str
    body: str = Field(description="Plain-text article body, paragraphs separated by blank lines")


class SyntheticSet(BaseModel):
    articles: list[SyntheticArticle]
