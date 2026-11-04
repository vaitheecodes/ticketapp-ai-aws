"""suggest_steps: next actions for the technician, grounded in the hand-off
brief and the closest KB guidance. They are things to check or ask, never
claimed results. Only called with a matching KB entry
(conversation.handoff_brief), and every step must come from it: steps the
entry doesn't support are dropped there (ai_service/grounding.py)."""

from __future__ import annotations

from backend.ai_service import provider
from backend.ai_service._format import GROUNDING_RULES, kb_guidance_text
from backend.ai_service.schemas import SuggestedSteps, SuggestStepsInput


def suggest_steps(data: SuggestStepsInput) -> SuggestedSteps:
    return provider.complete_json(
        "Suggest 2-5 concrete next steps for a Site24x7 support technician "
        "picking up this case, in the order they should do them. Phrase each as "
        "something to check, ask, or try, never as a finding. Skip anything the "
        "brief says was already tried. Every step must come from the kb_guidance "
        "block: never add a hostname, port, limit, number, menu path or setting it "
        "doesn't contain; a step it doesn't support is removed. "
        f"{GROUNDING_RULES}",
        {
            "category": data.category or "unknown",
            "handoff_brief": data.summary.model_dump_json(exclude={"source"}),
            "kb_guidance": kb_guidance_text(data.kb_candidates),
        },
        SuggestedSteps,
    )
