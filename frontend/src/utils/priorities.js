export const PRIORITIES = ["urgent", "high", "medium", "low"];
export const PRIORITY_LABELS = { urgent: "Urgent", high: "High", medium: "Medium", low: "Low" };

function isoDay(date) {
  return date.toISOString().slice(0, 10);
}

/* The last 30 days (UTC), as the backend's default report range. */
export function defaultRange() {
  const until = new Date();
  const since = new Date(until.getTime() - 30 * 24 * 3600 * 1000);
  return { since: isoDay(since), until: isoDay(until), priority: "" };
}
