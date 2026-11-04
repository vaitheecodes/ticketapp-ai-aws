import { useEffect, useState } from "react";
import { api } from "../api";
import { STATUS_KEYS, statusLabel } from "../utils/statusLabels";
import { formatMinutes } from "../utils/time";
import BarChart from "./BarChart";
import { defaultRange, PRIORITIES, PRIORITY_LABELS } from "../utils/priorities";
import ReportFilters from "./ReportFilters";

/* Admin ticket dashboard (GET /api/admin/dashboard): counts by status and
 * priority, tickets created/resolved per day, open tickets by priority and
 * technician, average first-response and resolution time per priority. */

function Stat({ label, value, hint }) {
  return (
    <div className="stat-tile">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}

function CountTable({ title, rows }) {
  const total = rows.reduce((sum, r) => sum + r.count, 0);
  return (
    <section className="card report-card">
      <h3 className="card-title">{title}</h3>
      <table className="report-table">
        <tbody>
          {rows.map((r) => (
            <tr key={r.key}>
              <th scope="row">{r.label}</th>
              <td className="num">{r.count}</td>
              <td className="bar-cell" aria-hidden="true">
                <span className="bar-track">
                  <span className="bar-fill" style={{ width: total ? `${(100 * r.count) / total}%` : 0 }} />
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function weightedAverage(times, key, countKey) {
  let sum = 0;
  let n = 0;
  for (const t of Object.values(times)) {
    if (t[key] !== null) {
      sum += t[key] * t[countKey];
      n += t[countKey];
    }
  }
  return n ? sum / n : null;
}

export default function DashboardPanel() {
  const [filters, setFilters] = useState(defaultRange);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let current = true;
    api
      .adminDashboard(filters)
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
  }, [filters]);

  const open = data ? Object.values(data.open_by_priority).reduce((a, b) => a + b, 0) : 0;
  return (
    <div className="dashboard-panel">
      <ReportFilters value={filters} onChange={setFilters} />
      {error && <p className="error">{error}</p>}
      {!data && !error && <p className="hint">Loading dashboard...</p>}
      {data && (
        <>
          <div className="stat-grid">
            <Stat label="Tickets" value={data.total} />
            <Stat label="Open" value={open} hint="not resolved or closed" />
            <Stat label="Resolved or closed" value={data.by_status.resolved + data.by_status.closed} />
            <Stat
              label="Avg first response"
              value={formatMinutes(weightedAverage(data.response_times, "avg_first_response_minutes", "first_responses"))}
            />
            <Stat
              label="Avg resolution"
              value={formatMinutes(weightedAverage(data.response_times, "avg_resolution_minutes", "resolved"))}
            />
          </div>

          <section className="card report-card">
            <BarChart
              title="Tickets per day"
              points={data.over_time}
              series={[
                { key: "created", label: "Created" },
                { key: "resolved", label: "Resolved" },
              ]}
              labelFor={(p) => p.date.slice(5)}
            />
          </section>

          <div className="report-grid">
            <CountTable
              title="By status"
              rows={STATUS_KEYS.map((s) => ({ key: s, label: statusLabel(s), count: data.by_status[s] }))}
            />
            <CountTable
              title="By priority"
              rows={PRIORITIES.map((p) => ({ key: p, label: PRIORITY_LABELS[p], count: data.by_priority[p] }))}
            />
            <CountTable
              title="Open by priority"
              rows={PRIORITIES.map((p) => ({ key: p, label: PRIORITY_LABELS[p], count: data.open_by_priority[p] }))}
            />
            <section className="card report-card">
              <h3 className="card-title">Open by technician</h3>
              {data.open_by_technician.length === 0 ? (
                <p className="hint">No open tickets.</p>
              ) : (
                <table className="report-table">
                  <tbody>
                    {data.open_by_technician.map((t) => (
                      <tr key={t.technician_id || t.name}>
                        <th scope="row">{t.name}</th>
                        <td className="num">{t.count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>
          </div>

          <section className="card report-card">
            <h3 className="card-title">Response times by priority</h3>
            <div className="table-wrap">
              <table className="report-table">
                <thead>
                  <tr>
                    <th>Priority</th>
                    <th className="num">Tickets</th>
                    <th className="num">Avg first response</th>
                    <th className="num">Avg resolution</th>
                  </tr>
                </thead>
                <tbody>
                  {PRIORITIES.map((p) => {
                    const t = data.response_times[p];
                    return (
                      <tr key={p}>
                        <th scope="row">
                          <span className={`priority-chip priority-${p}`}>{PRIORITY_LABELS[p]}</span>
                        </th>
                        <td className="num">{t.tickets}</td>
                        <td className="num" title={`${t.first_responses} with a first response`}>
                          {formatMinutes(t.avg_first_response_minutes)}
                        </td>
                        <td className="num" title={`${t.resolved} resolved`}>
                          {formatMinutes(t.avg_resolution_minutes)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="hint">
              First response: the first public reply by the support team (or the AI on tickets it handles).
              Resolution: creation to Resolved or Closed.
            </p>
          </section>
        </>
      )}
    </div>
  );
}
