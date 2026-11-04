-- SLA and response-time reporting (backend/sla.py).
-- first_response_at / resolved_at: for the admin dashboard, kept for every
-- ticket. sla_*: the targets and due times a ticket got from its priority
-- when SLA was on at creation (NULL otherwise), and the paused time of the
-- resolution clock (Pending Customer, Resolved/Closed).
ALTER TABLE tickets ADD COLUMN first_response_at          TIMESTAMPTZ;
ALTER TABLE tickets ADD COLUMN resolved_at                TIMESTAMPTZ;
ALTER TABLE tickets ADD COLUMN sla_first_response_minutes INTEGER;
ALTER TABLE tickets ADD COLUMN sla_resolution_minutes     INTEGER;
ALTER TABLE tickets ADD COLUMN sla_first_response_due     TIMESTAMPTZ;
ALTER TABLE tickets ADD COLUMN sla_resolution_due         TIMESTAMPTZ;
ALTER TABLE tickets ADD COLUMN sla_paused_at              TIMESTAMPTZ;
ALTER TABLE tickets ADD COLUMN sla_paused_seconds         INTEGER NOT NULL DEFAULT 0;
CREATE INDEX idx_tickets_created ON tickets (created_at);

-- First response: the first public reply by a staff member (an incident
-- broadcast has no author), or by the AI on a ticket it handled.
UPDATE tickets SET first_response_at = (
  SELECT MIN(m.created_at) FROM ticket_messages m
  WHERE m.ticket_id = tickets.ticket_id AND m.visibility = 'public_reply'
    AND ((m.sender = 'staff' AND m.author_id IS NOT NULL)
         OR (m.sender = 'ai' AND tickets.route <> 'human')))
WHERE first_response_at IS NULL;
-- Resolved: the latest move to Resolved/Closed of a ticket that is still so.
UPDATE tickets SET resolved_at = COALESCE((
  SELECT MAX(h.changed_at) FROM ticket_status_history h
  WHERE h.ticket_id = tickets.ticket_id AND h.to_status IN ('resolved', 'closed')), created_at)
WHERE resolved_at IS NULL AND status IN ('resolved', 'closed');
