"""Pydantic schemas for every structured LLM call. These double as the record shapes."""
from typing import Literal, Optional

from pydantic import BaseModel, Field

SourceType = Literal["first_party", "specialist", "general"]

# Provenance score from the revised design: 3 first-party, 2 specialist, 1 general press.
PROVENANCE_BY_SOURCE_TYPE = {"first_party": 3, "specialist": 2, "general": 1}


class TriageResult(BaseModel):
    """The three-part triage test applied inline during discovery."""

    named_organisation: bool = Field(description="A specific company, lab or agency is named as the user of quantum computing")
    organisation: Optional[str] = Field(default=None, description="The organisation name if test 1 passes")
    specific_problem: bool = Field(description="A concrete operational problem is named")
    problem: Optional[str] = Field(default=None, description="The problem if test 2 passes")
    work_done_with_result: bool = Field(description="A run was executed and some outcome is reported")
    result_summary: Optional[str] = Field(default=None, description="One sentence on the reported outcome if test 3 passes")
    source_type: SourceType = Field(description="first_party, specialist, or general")
    reasoning: str = Field(description="Brief evidence for each test, quoting the page where possible")

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
