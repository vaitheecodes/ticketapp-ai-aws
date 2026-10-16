/* One status vocabulary, used everywhere a ticket appears: the customer's
 * My tickets, the technician queue, the admin table, the simulator trace.
 * A reader should learn the colors once.
 *
 *   warn     open              waiting on the support team, nobody on it yet
 *   info     in_progress       a technician has it
 *   accent   pending_customer  waiting on the customer's reply
 *   ok       resolved          the AI or a technician answered it
 *   neutral  closed            terminal, archived
 *
 * Route chips share the scale so "handled by AI" and "waiting on the
 * customer" read as the same family of fact.
 */

const STATUS = {
  open: { tone: "warn", label: "Open" },
  in_progress: { tone: "info", label: "In Progress" },
  pending_customer: { tone: "accent", label: "Pending Customer" },
  resolved: { tone: "ok", label: "Resolved" },
  closed: { tone: "neutral", label: "Closed" },
};

const ROUTE = {
  ai: { tone: "accent", label: "AI" },
  clarify: { tone: "accent", label: "Clarifying" },
  human: { tone: "warn", label: "Human" },
  closed: { tone: "neutral", label: "Closed" },
};

function titleCase(value) {
  return String(value || "")
    .replace(/_/g, " ")
    .replace(/^\w/, (c) => c.toUpperCase());
}

function Chip({ tone, label, title, dot = true }) {
  return (
    <span className={`chip chip-${tone}`} title={title}>
      {dot && <span className="chip-dot" aria-hidden="true" />}
      {label}
    </span>
  );
}

export function StatusChip({ status, label }) {
  if (!status) return null;
  const meta = STATUS[status] || { tone: "neutral", label: titleCase(status) };
  return <Chip tone={meta.tone} label={label || meta.label} />;
}

/* AI chat sessions have their own lifecycle (backend/chats.py), drawn
 * from the same tones: an active chat waits on the customer, an escalated
 * one is with the support team, a resolved one is done. */
const CHAT_STATUS = {
  active: { tone: "accent", label: "Active" },
  escalated: { tone: "warn", label: "Escalated" },
  resolved: { tone: "ok", label: "Resolved" },
  abandoned: { tone: "neutral", label: "Ended" },
};

export function ChatStatusChip({ status }) {
  const meta = CHAT_STATUS[status] || { tone: "neutral", label: titleCase(status) };
  return <Chip tone={meta.tone} label={meta.label} />;
}

export function RouteChip({ route }) {
  if (!route) return null;
  const meta = ROUTE[route] || { tone: "neutral", label: titleCase(route) };
  return <Chip tone={meta.tone} label={meta.label} title={`Routed to ${meta.label}`} />;
}

/* Confidence reads as a measurement, so it ships in mono with the same
 * thresholds decide_route actually uses (75% base, 90% high-risk). */
export function ConfidenceChip({ confidence }) {
  if (confidence === null || confidence === undefined) return null;
  const pct = Math.round(confidence * 100);
  const tone = pct >= 90 ? "ok" : pct >= 75 ? "info" : "warn";
  return (
    <span className={`chip chip-${tone} chip-mono`} title="Classification confidence">
      {pct}%
    </span>
  );
}

/* Incident clusters (backend/incidents.py): a fresh AI detection needs a
 * look (warn), one being investigated is in hand (info), a confirmed one
 * is a real customer-facing problem (danger), a dismissed one is done. */
const INCIDENT_STATUS = {
  detected: { tone: "warn", label: "Detected" },
  under_investigation: { tone: "info", label: "Under investigation" },
  confirmed: { tone: "danger", label: "Confirmed" },
  dismissed: { tone: "neutral", label: "Dismissed" },
};

export function IncidentStatusChip({ status }) {
  const meta = INCIDENT_STATUS[status] || { tone: "neutral", label: titleCase(status) };
  return <Chip tone={meta.tone} label={meta.label} />;
}
