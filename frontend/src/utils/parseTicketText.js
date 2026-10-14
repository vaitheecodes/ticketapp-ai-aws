// tickets.ticket_text sometimes has a case summary appended when a chat
// escalates mid-conversation (backend/main.py's ticket_chat handler,
// using backend/graph.py's summarize_case() output). Splits that back
// into its two logical parts for display, instead of showing one
// run-on string. Tickets that escalated without going through chat
// (e.g. an immediate high-risk escalation) have no marker — `summary`
// comes back null and callers should just show `original`.
const ESCALATION_MARKER = "\n\n--- Escalated after chat ---\n";

export function parseTicketText(ticketText) {
  const idx = ticketText.indexOf(ESCALATION_MARKER);
  if (idx === -1) {
    return { original: ticketText, summary: null };
  }
  return {
    original: ticketText.slice(0, idx),
    summary: ticketText.slice(idx + ESCALATION_MARKER.length),
  };
}
