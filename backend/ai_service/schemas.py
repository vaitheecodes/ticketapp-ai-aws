"""Input and output schemas for every AI operation.

Outputs are what the model must produce; provider.complete_json validates
them (one retry) before anything is saved. Lengths are capped so a
runaway response can't bloat the database or the UI. Fields the model
must not control (`source`) are hidden from the schema it sees and set by
code.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema

# The 17 KB categories (src/pipeline/config.py:CATEGORY_INTENTS); kept
# here so the AI layer has no dependency on the routing graph.
CATEGORIES = (
    "ACCOUNT", "AGENT", "ALERTS", "APM", "APPLICATION_MONITORING", "CLOUD",
    "CONTAINERS", "DATA", "INTEGRATION", "LOGS", "MONITORING", "NETFLOW",
    "REPORTING", "SERVER_MONITORING", "SNMP", "SUPPORT", "WEB_MONITORING",
)
Category = Literal[CATEGORIES]  # type: ignore[valid-type]


def _text(max_length: int = 2000):
    return Field(min_length=1, max_length=max_length)


def _items(max_length: int = 12, **kwargs):
    return Field(default_factory=list, max_length=max_length, **kwargs)


Source = SkipJsonSchema[Literal["ai", "fallback"]]


# ---------------------------------------------------------------------------
# Shared inputs
# ---------------------------------------------------------------------------
class KBCandidate(BaseModel):
    kb_entry_id: str
    category: str
    intent: str
    instruction: str
    response: str
    score: float


class HistoryItem(BaseModel):
    ticket_text: str
    category: str | None = None
    status: str | None = None


# ---------------------------------------------------------------------------
# categorize_ticket
# ---------------------------------------------------------------------------
class CategorizeInput(BaseModel):
    ticket_text: str
    history: list[HistoryItem] = []
    kb_candidates: list[KBCandidate] = []


class Categorization(BaseModel):
    category: Category
    confidence: float = Field(ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# chat_response
# ---------------------------------------------------------------------------
class AnswerInput(BaseModel):
    ticket_text: str
    category: str | None = None
    history: list[HistoryItem] = []
    kb_candidates: list[KBCandidate] = []
    order_context: str = ""
    continuation: bool = False
    reply_format: Literal["chat", "email"] = "chat"
    customer_name: str | None = None


class ClarifyInput(BaseModel):
    ticket_text: str
    category: str | None = None
    confidence: float = 0.0
    kb_candidates: list[KBCandidate] = []
    reply_format: Literal["chat", "email"] = "chat"
    customer_name: str | None = None


class ChatReply(BaseModel):
    text: str = _text(8000)


class ContinuationInput(BaseModel):
    message: str
    prior_ai_response: str


class ContinuationVerdict(BaseModel):
    is_continuation: bool = False


# ---------------------------------------------------------------------------
# summarize_conversation / summarize_ticket / suggest_steps
# ---------------------------------------------------------------------------
class ChatResolutionSummary(BaseModel):
    """Final summary of a chat the customer confirmed as resolved."""

    problem: str = _text()
    troubleshooting: list[str] = _items()
    solution: str = _text()
    # "fallback" = built without the model (provider down or invalid
    # output); reviewers should read the transcript instead.
    source: Source = "ai"


class HandoffSummary(BaseModel):
    """Brief for the technician who picks up an escalated chat or ticket."""

    issue: str = _text()
    tried: list[str] = _items()
    findings: list[str] = _items()
    unresolved_questions: list[str] = _items()
    source: Source = "ai"

    def as_text(self, steps: list[str]) -> str:
        """Plain-text form, appended to ticket_text so the queue shows it."""
        lines = [f"Issue: {self.issue}"]
        for title, items in (
            ("Already tried", self.tried),
            ("Findings", self.findings),
            ("Unresolved questions", self.unresolved_questions),
            ("Suggested next steps", steps),
        ):
            if items:
                lines.append(f"{title}:")
                lines.extend(f"- {item}" for item in items)
        return "\n".join(lines)


class SuggestStepsInput(BaseModel):
    summary: HandoffSummary
    category: str | None = None
    kb_candidates: list[KBCandidate] = []


class SuggestedSteps(BaseModel):
    steps: list[str] = Field(min_length=1, max_length=8)


# ---------------------------------------------------------------------------
# correlate_incidents / summarize_incident
# ---------------------------------------------------------------------------
class CorrelationTicket(BaseModel):
    """One candidate ticket, as the model sees it. Customers are
    pseudonymous references (no names or emails) and only the public
    conversation is included — never internal notes."""

    ticket_id: str
    customer_ref: str
    created_at: str
    category: str | None = None
    subject: str
    details: str = Field(max_length=3000)


class DepartmentOption(BaseModel):
    name: str
    categories: list[str]


class CorrelateInput(BaseModel):
    tickets: list[CorrelationTicket] = Field(min_length=2, max_length=40)
    departments: list[DepartmentOption] = []
    window_hours: int


class CorrelationJudgement(BaseModel):
    """The model's judgement on one candidate group. Facts and hypotheses
    are kept apart; a shared cause is only claimed with evidence."""

    is_related: bool
    related_ticket_ids: list[str] = _items(40)
    title: str = Field(default="Possible related reports", max_length=120)
    # Required either way: why they may be related, or why not.
    why_related: str = _text()
    common_symptoms: list[str] = _items()
    affected_services: list[str] = _items()
    observed_facts: list[str] = _items()
    hypotheses: list[str] = _items()
    possible_shared_cause: str = Field(default="Requires further investigation", max_length=500)
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_reasons: list[str] = _items()
    suggested_next_steps: list[str] = _items()
    candidate_departments: list[str] = _items(10)


class SummarizeIncidentInput(BaseModel):
    title: str
    tickets: list[CorrelationTicket] = Field(min_length=1, max_length=40)
    observed_facts: list[str] = []
    hypotheses: list[str] = []


class IncidentSummary(BaseModel):
    title: str = _text(120)
    summary: str = _text()
    common_symptoms: list[str] = _items()
    affected_services: list[str] = _items()
    suggested_next_steps: list[str] = _items()
