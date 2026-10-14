/* One status vocabulary, used everywhere a ticket appears: the customer's
 * past tickets, the agent queue, My Tickets, the admin table, the simulator
 * trace. A reader should learn the colors once.
 *
 *   ok       resolved        the AI or an agent answered it
 *   accent   awaiting_reply  the AI is waiting on the customer
 *   info     assigned        a specific human has it
 *   warn     open            queued, nobody on it yet
 *   neutral  closed          terminal, archived
 *
 * Route chips share the scale so "handled by AI" and "waiting on the
 * customer" read as the same family of fact.
 */

const STATUS = {
  resolved: { tone: "ok", label: "Resolved" },
  awaiting_reply: { tone: "accent", label: "Awaiting reply" },
  assigned: { tone: "info", label: "Assigned" },
  open: { tone: "warn", label: "Open" },
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

export function StatusChip({ status }) {
  if (!status) return null;
  const meta = STATUS[status] || { tone: "neutral", label: titleCase(status) };
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
