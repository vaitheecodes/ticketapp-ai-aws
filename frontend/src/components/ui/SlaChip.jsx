/* A ticket's SLA (backend/sla.py evaluate()), for staff: the worst state
 * of its two targets, and the due time of the one that matters.
 *
 *   info     on_track   running, under the at-risk threshold
 *   warn     at_risk    running, at least the at-risk share of the time used
 *   danger   breached   a target was missed
 *   ok       met        both targets met
 *   neutral  paused     waiting on the customer (resolution clock stopped)
 */

import { absoluteTime, dueText } from "../../utils/time";

const SLA_STATES = {
  on_track: { tone: "info", label: "On track" },
  at_risk: { tone: "warn", label: "At risk" },
  breached: { tone: "danger", label: "Breached" },
  met: { tone: "ok", label: "Met" },
  paused: { tone: "neutral", label: "Paused" },
};

const TARGET_LABEL = { first_response: "First response", resolution: "Resolution" };

// The running target nearest to (or past) its due time.
function focusTarget(sla) {
  const running = ["first_response", "resolution"].filter((k) => !sla[k].done_at && sla[k].state !== "paused");
  if (running.length === 0) return null;
  return running.sort((a, b) => new Date(sla[a].due_at) - new Date(sla[b].due_at))[0];
}

export function SlaStateChip({ state }) {
  const meta = SLA_STATES[state];
  if (!meta) return null;
  return (
    <span className={`chip chip-${meta.tone}`}>
      <span className="chip-dot" aria-hidden="true" />
      {meta.label}
    </span>
  );
}

/* compact: just the chip (a list row); otherwise the chip plus which
 * target and when it is due. */
export default function SlaChip({ sla, compact = false }) {
  if (!sla) return null;
  const meta = SLA_STATES[sla.state] || SLA_STATES.on_track;
  const target = focusTarget(sla);
  const detail = target ? `${TARGET_LABEL[target]} ${dueText(sla[target].due_at)}` : null;
  const title = ["first_response", "resolution"]
    .map((k) => `${TARGET_LABEL[k]}: ${SLA_STATES[sla[k].state]?.label} (due ${absoluteTime(sla[k].due_at)})`)
    .join("\n");
  return (
    <span className="sla-chip" title={title}>
      <span className={`chip chip-${meta.tone}`}>
        <span className="chip-dot" aria-hidden="true" />
        SLA: {meta.label}
      </span>
      {!compact && detail && <span className="hint sla-chip-detail">{detail}</span>}
    </span>
  );
}
