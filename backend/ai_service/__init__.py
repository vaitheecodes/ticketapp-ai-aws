"""Modular AI service: one module per operation, each with pydantic input
and output schemas (schemas.py), all model calls through provider.py.

    categorize_ticket        category + confidence for a ticket
    chat_response            answer / clarify / is_continuation
    summarize_conversation   chat resolution + chat escalation briefs
    summarize_ticket         hand-off brief for an email-style ticket
    suggest_steps            technician next steps
    correlate_incidents      do these tickets share an incident?
    summarize_incident       refresh an incident's summary

Routes and the LangGraph agent (backend/graph.py) call these; nothing
else talks to the model. Untrusted text is always passed as delimited
data, outputs are validated before use, and failures raise AIError for
the caller to handle without changing ticket or chat state.
"""

from backend.ai_service.provider import AIError  # noqa: F401
