/* One vocabulary for every ticket mark, learned once:
 *
 *   status    open=warning  in_progress=info  pending_customer=accent
 *             resolved=positive  closed=neutral
 *   priority  urgent=negative  high=warning  medium=neutral  low=outline
 *   route     ai / clarify = accent (the bot), human = neutral
 *   SLA       breached=negative  at_risk=warning  on_track=info
 *             met=positive  paused=neutral
 *
 * Every mark is a word on a pale tint; colour never carries meaning alone. */

import { BOT_NAME } from "@/lib/people";
import { Badge } from "@/ui/badge";
import { PRIORITY_LABELS } from "@/utils/priorities";
import { statusLabel } from "@/utils/statusLabels";
import { absoluteTime, dueText } from "@/utils/time";

const STATUS_TONE = {
  open: "warning",
  in_progress: "info",
  pending_customer: "accent",
  resolved: "positive",
  closed: "neutral",
};

export function StatusBadge({ status, className }) {
  return (
    <Badge tone={STATUS_TONE[status] || "neutral"} dot className={className}>
      {statusLabel(status)}
    </Badge>
  );
}

const PRIORITY_TONE = { urgent: "negative", high: "warning", medium: "neutral", low: "outline" };

export function PriorityBadge({ priority, className }) {
  if (!priority) return <span className="text-muted-fg">—</span>;
  return (
    <Badge tone={PRIORITY_TONE[priority] || "neutral"} className={className}>
      {PRIORITY_LABELS[priority] || priority}
    </Badge>
  );
}

const ROUTE = {
  ai: ["accent", BOT_NAME],
  clarify: ["accent", "Clarifying"],
  human: ["neutral", "Support team"],
};

export function RouteBadge({ route, className }) {
  if (!route) return null;
  const [tone, label] = ROUTE[route] || ["neutral", route];
  return (
    <Badge tone={tone} className={className}>
      {label}
    </Badge>
  );
}

const SLA_STATES = {
  on_track: { tone: "info", label: "On track" },
  at_risk: { tone: "warning", label: "At risk" },
  breached: { tone: "negative", label: "Breached" },
  met: { tone: "positive", label: "Met" },
  paused: { tone: "neutral", label: "Paused" },
};

const TARGET_LABEL = { first_response: "First response", resolution: "Resolution" };

// The running target nearest to (or past) its due time.
function focusTarget(sla) {
  const running = ["first_response", "resolution"].filter((k) => !sla[k].done_at && sla[k].state !== "paused");
  if (running.length === 0) return null;
  return running.sort((a, b) => new Date(sla[a].due_at) - new Date(sla[b].due_at))[0];
}

export function SlaStateBadge({ state }) {
  const meta = SLA_STATES[state];
  if (!meta) return null;
  return (
    <Badge tone={meta.tone} dot>
      {meta.label}
    </Badge>
  );
}

/* A ticket's SLA (backend/sla.py evaluate()): the worst state of its two
 * targets and, unless compact, which target is due when. */
export function SlaBadge({ sla, compact = false }) {
  if (!sla) return <span className="text-muted-fg">—</span>;
  const meta = SLA_STATES[sla.state] || SLA_STATES.on_track;
  const target = focusTarget(sla);
  const detail = target ? `${TARGET_LABEL[target]} ${dueText(sla[target].due_at)}` : null;
  const title = ["first_response", "resolution"]
    .map((k) => `${TARGET_LABEL[k]}: ${SLA_STATES[sla[k].state]?.label} (due ${absoluteTime(sla[k].due_at)})`)
    .join("\n");
  return (
    <span className="inline-flex flex-wrap items-center gap-2" title={title}>
      <Badge tone={meta.tone} dot>
        {compact ? meta.label : `SLA ${meta.label.toLowerCase()}`}
      </Badge>
      {!compact && detail && <span className="text-sm tabular-nums text-muted-fg">{detail}</span>}
    </span>
  );
}
