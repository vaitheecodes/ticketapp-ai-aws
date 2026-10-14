/* created_at arrives as an ISO-8601 UTC string (backend writes
 * datetime.now(timezone.utc).isoformat()). SQLite hands it back without a
 * trailing Z in some paths, which Safari and Firefox then read as local
 * time — so normalize before parsing rather than trusting Date().
 */

function parseUtc(iso) {
  if (!iso) return null;
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : `${iso}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function relativeTime(iso) {
  const date = parseUtc(iso);
  if (!date) return "";

  const seconds = Math.round((Date.now() - date.getTime()) / 1000);
  if (seconds < 45) return "just now";
  if (seconds < 90) return "1 min ago";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days === 1) return "yesterday";
  if (days < 7) return `${days}d ago`;

  const sameYear = date.getFullYear() === new Date().getFullYear();
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    ...(sameYear ? {} : { year: "numeric" }),
  });
}

export function absoluteTime(iso) {
  const date = parseUtc(iso);
  return date ? date.toLocaleString() : "";
}
