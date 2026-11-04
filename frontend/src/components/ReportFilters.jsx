import { useId } from "react";
import { PRIORITIES, PRIORITY_LABELS } from "../utils/priorities";

/* Date range (UTC days) and priority, shared by the Dashboard and SLA pages. */
export default function ReportFilters({ value, onChange }) {
  const ids = { since: useId(), until: useId(), priority: useId() };
  const set = (key) => (e) => onChange({ ...value, [key]: e.target.value });
  return (
    <div className="list-filters report-filters">
      <label className="inline-select" htmlFor={ids.since}>
        <span>From</span>
        <input id={ids.since} type="date" value={value.since} max={value.until} onChange={set("since")} />
      </label>
      <label className="inline-select" htmlFor={ids.until}>
        <span>To</span>
        <input id={ids.until} type="date" value={value.until} min={value.since} onChange={set("until")} />
      </label>
      <label className="inline-select" htmlFor={ids.priority}>
        <span>Priority</span>
        <select id={ids.priority} value={value.priority} onChange={set("priority")}>
          <option value="">All</option>
          {PRIORITIES.map((p) => (
            <option key={p} value={p}>
              {PRIORITY_LABELS[p]}
            </option>
          ))}
        </select>
      </label>
      <span className="hint">Tickets created in this range (UTC)</span>
    </div>
  );
}
