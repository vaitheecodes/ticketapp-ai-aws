import { useCallback, useEffect, useId, useState } from "react";
import { api } from "../api";
import { statusLabel } from "../utils/statusLabels";
import { absoluteTime, dueText } from "../utils/time";
import { defaultRange, PRIORITIES, PRIORITY_LABELS } from "../utils/priorities";
import ReportFilters from "./ReportFilters";
import Toast from "./Toast";
import { SlaStateChip } from "./ui/SlaChip";

/* Admin SLA page (backend/sla.py): the targets per priority, and how
 * tickets are doing against them.
 *
 * SLA is off until enabled. Targets apply to tickets created while it is
 * on (a priority change recalculates from creation). The clock runs 24x7;
 * the resolution clock pauses while a ticket is Pending Customer. */

// A target in minutes, edited as a number plus minutes/hours.
function TargetInput({ id, label, minutes, onChange }) {
  const inHours = minutes % 60 === 0;
  const [unit, setUnit] = useState(inHours ? "hours" : "minutes");
  const value = unit === "hours" ? minutes / 60 : minutes;
  const unitId = useId();
  return (
    <div className="target-input">
      <label className="sr-only" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        type="number"
        min={1}
        step={unit === "hours" ? 0.5 : 1}
        value={value}
        onChange={(e) => onChange(Math.round(Number(e.target.value) * (unit === "hours" ? 60 : 1)))}
        required
      />
      <label className="sr-only" htmlFor={unitId}>
        {label} unit
      </label>
      <select id={unitId} value={unit} onChange={(e) => setUnit(e.target.value)}>
        <option value="minutes">min</option>
        <option value="hours">h</option>
      </select>
    </div>
  );
}

function SlaSettings({ onSaved }) {
  const [values, setValues] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const enabledId = useId();
  const riskId = useId();

  useEffect(() => {
    api.adminSettings().then((s) => setValues(s.sections.sla)).catch((err) => setError(err.message));
  }, []);

  if (!values) return error ? <p className="error">{error}</p> : <p className="hint">Loading SLA settings...</p>;

  const setTarget = (priority, key) => (minutes) =>
    setValues({ ...values, [priority]: { ...values[priority], [key]: minutes } });

  async function handleSubmit(e) {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      const saved = await api.adminSaveSettings("sla", values);
      setValues(saved.sections.sla);
      onSaved(saved.sections.sla.enabled ? "SLA saved and on." : "SLA saved (off).");
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="card sla-settings" onSubmit={handleSubmit}>
      <h3 className="card-title">Targets</h3>
      <label className="checkbox-label" htmlFor={enabledId}>
        <input
          id={enabledId}
          type="checkbox"
          checked={values.enabled}
          onChange={(e) => setValues({ ...values, enabled: e.target.checked })}
        />
        <span>Track SLA on new tickets</span>
      </label>
      <p className="hint">
        Applies to tickets created while it is on. Clock: 24x7. The resolution clock pauses while a ticket is
        waiting on the customer.
      </p>
      <div className="table-wrap">
        <table className="report-table">
          <thead>
            <tr>
              <th>Priority</th>
              <th>First response</th>
              <th>Resolution</th>
            </tr>
          </thead>
          <tbody>
            {PRIORITIES.map((p) => (
              <tr key={p}>
                <th scope="row">
                  <span className={`priority-chip priority-${p}`}>{PRIORITY_LABELS[p]}</span>
                </th>
                <td>
                  <TargetInput
                    id={`sla-${p}-fr`}
                    label={`${PRIORITY_LABELS[p]} first-response target`}
                    minutes={values[p].first_response_minutes}
                    onChange={setTarget(p, "first_response_minutes")}
                  />
                </td>
                <td>
                  <TargetInput
                    id={`sla-${p}-res`}
                    label={`${PRIORITY_LABELS[p]} resolution target`}
                    minutes={values[p].resolution_minutes}
                    onChange={setTarget(p, "resolution_minutes")}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="field-group field-narrow">
        <label className="field-label" htmlFor={riskId}>
          At risk when this much of the time is used (%)
        </label>
        <input
          id={riskId}
          type="number"
          min={1}
          max={99}
          value={values.at_risk_percent}
          onChange={(e) => setValues({ ...values, at_risk_percent: Number(e.target.value) })}
          required
        />
      </div>
      <div className="row-actions">
        <button type="submit" className="btn-primary" disabled={saving}>
          {saving ? "Saving..." : "Save SLA"}
        </button>
      </div>
      {error && <p className="error">{error}</p>}
    </form>
  );
}

function percent(value) {
  return value === null || value === undefined ? "—" : `${value}%`;
}

function SlaReport({ refreshKey }) {
  const [filters, setFilters] = useState(defaultRange);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let current = true;
    api
      .adminSla(filters)
      .then((d) => {
        if (current) {
          setData(d);
          setError("");
        }
      })
      .catch((err) => current && setError(err.message));
    return () => {
      current = false;
    };
  }, [filters, refreshKey]);

  return (
    <section className="sla-report">
      <ReportFilters value={filters} onChange={setFilters} />
      {error && <p className="error">{error}</p>}
      {!data && !error && <p className="hint">Loading...</p>}
      {data && (
        <>
          {!data.enabled && (
            <p className="pending-text">SLA is off: only tickets created while it was on are shown.</p>
          )}
          <div className="stat-grid">
            <div className="stat-tile">
              <div className="stat-label">Tickets with an SLA</div>
              <div className="stat-value">{data.tickets_with_sla}</div>
            </div>
            <div className="stat-tile">
              <div className="stat-label">Within SLA</div>
              <div className="stat-value">{data.totals.within_sla}</div>
            </div>
            <div className="stat-tile">
              <div className="stat-label">At risk</div>
              <div className="stat-value">{data.totals.at_risk}</div>
            </div>
            <div className="stat-tile">
              <div className="stat-label">Breached</div>
              <div className="stat-value">{data.totals.breached}</div>
            </div>
          </div>

          <section className="card report-card">
            <h3 className="card-title">Compliance by priority</h3>
            <div className="table-wrap">
              <table className="report-table">
                <thead>
                  <tr>
                    <th>Priority</th>
                    <th className="num">Tickets</th>
                    <th className="num">First response met</th>
                    <th className="num">Resolution met</th>
                    <th className="num">At risk</th>
                    <th className="num">Breached</th>
                  </tr>
                </thead>
                <tbody>
                  {PRIORITIES.map((p) => {
                    const r = data.by_priority[p];
                    return (
                      <tr key={p}>
                        <th scope="row">
                          <span className={`priority-chip priority-${p}`}>{PRIORITY_LABELS[p]}</span>
                        </th>
                        <td className="num">{r.tickets}</td>
                        <td className="num" title={`${r.first_response.met} met, ${r.first_response.breached} missed`}>
                          {percent(r.first_response.percent)}
                        </td>
                        <td className="num" title={`${r.resolution.met} met, ${r.resolution.breached} missed`}>
                          {percent(r.resolution.percent)}
                        </td>
                        <td className="num">{r.at_risk}</td>
                        <td className="num">{r.breached}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="hint">Compliance: targets met out of targets finished or already missed.</p>
          </section>

          <section className="card report-card">
            <h3 className="card-title">Near or past breach</h3>
            {data.watchlist.length === 0 ? (
              <p className="empty-state">No open ticket is at risk or past its SLA.</p>
            ) : (
              <div className="table-wrap">
                <table className="tickets-table">
                  <thead>
                    <tr>
                      <th>Ticket</th>
                      <th>Priority</th>
                      <th>Status</th>
                      <th>Technician</th>
                      <th>First response</th>
                      <th>Resolution</th>
                      <th>Next due</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.watchlist.map((w) => (
                      <tr key={w.ticket_id}>
                        <td className="ticket-cell">
                          <span className="mono">{w.ticket_id}</span>
                          <span className="ticket-cell-subject" title={w.subject}>
                            {w.subject}
                          </span>
                        </td>
                        <td>
                          <span className={`priority-chip priority-${w.priority}`}>{PRIORITY_LABELS[w.priority]}</span>
                        </td>
                        <td>{statusLabel(w.status)}</td>
                        <td>{w.technician_name || <span className="hint">Unassigned</span>}</td>
                        <td>
                          <SlaStateChip state={w.sla.first_response.state} />
                        </td>
                        <td>
                          <SlaStateChip state={w.sla.resolution.state} />
                          <span className="hint"> {Math.round((w.sla.resolution.used_ratio || 0) * 100)}% used</span>
                        </td>
                        <td>
                          <time dateTime={w.next_due_at} title={absoluteTime(w.next_due_at)}>
                            {dueText(w.next_due_at)}
                          </time>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="hint">At risk: {data.at_risk_percent}% or more of a target's time used.</p>
          </section>
        </>
      )}
    </section>
  );
}

export default function SlaPanel() {
  const [toast, setToast] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const dismiss = useCallback(() => setToast(null), []);
  return (
    <div className="sla-panel">
      <SlaSettings
        onSaved={(message) => {
          setToast({ message });
          setRefreshKey((k) => k + 1);
        }}
      />
      <SlaReport refreshKey={refreshKey} />
      <Toast toast={toast} onDismiss={dismiss} />
    </div>
  );
}
