"""Try a Ticket: run a ticket through the live pipeline without saving it,
to see its category, confidence, route and reason (the per-ticket trace).

Technicians and managers only: it calls the real AI provider, so it is
not open to customers, and it is no longer part of the admin console.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend import auth, runtime
from backend.ai_service import AIError
from backend.email_utils import clean_email

router = APIRouter()

_staff = auth.require_role("technician", "manager")


class SimulateTicketRequest(BaseModel):
    ticket_text: str
    channel: str = "chat"
    customer_id: str | None = None


@router.post("/api/try-a-ticket")
def try_a_ticket(body: SimulateTicketRequest, staff_id: str = Depends(_staff)):
    """Nothing is persisted; the ids are throwaway SIM- values."""
    ticket_text = body.ticket_text.strip()
    if not ticket_text:
        raise HTTPException(status_code=400, detail="ticket_text cannot be empty")
    if body.channel == "email":
        ticket_text = clean_email(ticket_text)
    try:
        return runtime.get_graph().invoke({
            "ticket_id": f"SIM-{uuid.uuid4().hex[:8]}",
            "customer_id": body.customer_id or f"SIM-CUST-{uuid.uuid4().hex[:6]}",
            "channel": body.channel,
            "ticket_text": ticket_text,
        })
    except AIError as exc:
        raise HTTPException(status_code=503, detail="The AI provider is unavailable right now") from exc
