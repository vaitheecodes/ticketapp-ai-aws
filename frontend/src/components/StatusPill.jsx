const STATUS_LABELS = {
  awaiting_reply: "awaiting reply",
};

export function StatusPill({ status }) {
  return <span className={`pill pill-${status}`}>{STATUS_LABELS[status] || status}</span>;
}

const ROUTE_LABELS = { ai: "AI", clarify: "Clarifying", human: "Human" };

export function RoutePill({ route }) {
  if (!route) return null;
  return <span className={`pill pill-${route}`}>{ROUTE_LABELS[route] || route}</span>;
}
