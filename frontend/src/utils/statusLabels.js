/* Ticket status display labels. The five keys are fixed (the workflow
 * depends on them); the admin can rename their labels (Settings), which
 * the backend serves from /api/meta. Loaded once before the app renders
 * (main.jsx) and refreshed when the admin saves. */

export const STATUS_KEYS = ["open", "in_progress", "pending_customer", "resolved", "closed"];

const DEFAULTS = {
  open: "Open",
  in_progress: "In Progress",
  pending_customer: "Pending Customer",
  resolved: "Resolved",
  closed: "Closed",
};

let labels = { ...DEFAULTS };

export function setStatusLabels(next) {
  labels = { ...DEFAULTS, ...(next || {}) };
}

export function statusLabel(key) {
  return labels[key] || key;
}
