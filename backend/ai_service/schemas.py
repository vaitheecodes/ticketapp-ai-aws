"""Validated output schemas for AI operations. Lengths are capped so a
runaway response can't bloat the database or the technician UI."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema

_Text = Field(min_length=1, max_length=2000)
_Items = Field(default_factory=list, max_length=12)


class ChatResolutionSummary(BaseModel):
    """Final summary of a chat the customer confirmed as resolved."""

    problem: str = _Text
    troubleshooting: list[str] = _Items
    solution: str = _Text
    # "fallback" = built from the transcript without the model (provider
    # down or invalid output); reviewers should read the transcript. Set by
    # code, never by the model: hidden from the schema the model sees, and
    # overwritten after validation (see summaries.py).
    source: SkipJsonSchema[Literal["ai", "fallback"]] = "ai"


class EscalationSummary(BaseModel):
    """Hand-off brief for the technician who picks up an escalated chat."""

    issue: str = _Text
    tried: list[str] = _Items
    findings: list[str] = _Items
    unresolved_questions: list[str] = _Items
    suggested_next_steps: list[str] = _Items
    source: SkipJsonSchema[Literal["ai", "fallback"]] = "ai"
