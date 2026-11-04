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

/* Incident times are always shown in IST, whatever the viewer's zone. */
export function istTime(iso) {
  const date = parseUtc(iso);
  if (!date) return "";
  return `${date.toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata",
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  })} IST`;
}

/* Value for <input type="datetime-local">, as IST wall-clock time. */
export function istInputNow() {
  const ist = new Date(Date.now() + 330 * 60 * 1000);
  return ist.toISOString().slice(0, 16);
}

/* A duration in minutes as "45m", "3h 20m" or "2d 4h". */
export function formatMinutes(minutes) {
  if (minutes === null || minutes === undefined) return "—";
  const m = Math.round(minutes);
  if (m < 60) return `${m}m`;
  if (m < 48 * 60) return m % 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m / 60}h`;
  const h = Math.round(m / 60);
  return h % 24 ? `${Math.floor(h / 24)}d ${h % 24}h` : `${h / 24}d`;
}

/* "due in 2h 10m" or "overdue by 25m". */
export function dueText(iso) {
  const date = parseUtc(iso);
  if (!date) return "";
  const minutes = (date.getTime() - Date.now()) / 60000;
  return minutes >= 0 ? `due in ${formatMinutes(minutes)}` : `overdue by ${formatMinutes(-minutes)}`;
}
