import { statusLabel } from "../utils/statusLabels";

export function StatusPill({ status }) {
  return <span className={`pill pill-${status}`}>{statusLabel(status)}</span>;
}

const ROUTE_LABELS = { ai: "AI", clarify: "Clarifying", human: "Human" };

export function RoutePill({ route }) {
  if (!route) return null;
  return <span className={`pill pill-${route}`}>{ROUTE_LABELS[route] || route}</span>;
}
