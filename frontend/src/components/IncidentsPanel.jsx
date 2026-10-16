/* AI-detected incident clusters, for managers (their departments plus
 * unrouted ones) and the admin (everything).
 *
 * The AI proposes; people decide. Each cluster shows why the AI linked the
 * tickets, how confident it is and why, and keeps observed facts apart
 * from hypotheses. Managers confirm, investigate or dismiss with a note,
 * correct the departments, and open every linked ticket. Nothing here
 * changes the tickets themselves.
 */

import { useCallback, useEffect, useId, useState } from "react";
import { api } from "../api";
import { categoryLabel } from "../utils/categories";
import { absoluteTime, relativeTime } from "../utils/time";
import StaffTicketView from "./StaffTicketView";
import { IncidentStatusChip, StatusChip } from "./ui/StatusChip";

const ACTIONS = [
  ["confirm", "Confirm incident", "confirmed"],
  ["investigate", "Mark under investigation", "under_investigation"],
  ["dismiss", "Dismiss", "dismissed"],
];
const ACTION_LABEL = {
  confirm: "Confirmed",
  investigate: "Marked under investigation",
  dismiss: "Dismissed",
  departments_changed: "Departments set to",
};

function Confidence({ value }) {
  const pct = Math.round(value * 100);
  const tone = pct >= 75 ? "high" : pct >= 50 ? "medium" : "low";
  return (
    <span className={`confidence confidence-${tone}`} title="AI confidence that these tickets are related">
      <span className="confidence-bar" aria-hidden="true">
        <span style={{ width: `${pct}%` }} />
      </span>
      {pct}%
    </span>
  );
}

function ListSection({ title, items }) {
  if (!items?.length) return null;
  return (
    <div className="incident-section">
      <h4>{title}</h4>
      <ul>
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function DepartmentsEditor({ incident, onSaved }) {
  const [selected, setSelected] = useState(new Set(incident.departments.map((d) => d.department_id)));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function toggle(id) {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  }

  async function save() {
    setBusy(true);
    setError("");
    try {
      onSaved(await api.incidentDepartments(incident.incident_id, [...selected]));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="incident-departments-editor">
      <summary>Correct departments</summary>
      <fieldset className="checkbox-grid">
        <legend className="sr-only">Departments this incident concerns</legend>
        {incident.all_departments.map((d) => (
          <label key={d.department_id} className="checkbox-label">
            <input type="checkbox" checked={selected.has(d.department_id)} onChange={() => toggle(d.department_id)} />
            {d.name}
          </label>
        ))}
      </fieldset>
      <button type="button" onClick={save} disabled={busy}>
        {busy ? "Saving…" : "Save departments"}
      </button>
      {error && <p className="error">{error}</p>}
    </details>
  );
}

function ReviewForm({ incident, onSaved }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState("");
  const noteId = useId();

  async function act(action) {
    if (!note.trim()) {
      setError("Add a note explaining the decision.");
      return;
    }
    setBusy(action);
    setError("");
    try {
      onSaved(await api.incidentReview(incident.incident_id, action, note.trim()));
      setNote("");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="incident-review">
      <label className="field-label" htmlFor={noteId}>
        Review note
      </label>
      <textarea
        id={noteId}
        rows={3}
        maxLength={2000}
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="What did you check, and why this decision?"
      />
      <div className="row-actions">
        {ACTIONS.map(([action, label, status]) => (
          <button
            key={action}
            type="button"
            className={action === "confirm" ? "btn-primary" : action === "dismiss" ? "btn-danger-outline" : ""}
            disabled={busy !== null || incident.status === status}
            onClick={() => act(action)}
          >
            {busy === action ? "Saving…" : label}
          </button>
        ))}
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

function IncidentDetail({ incidentId, onChanged }) {
  const [incident, setIncident] = useState(null);
  const [error, setError] = useState("");
  const [openTicket, setOpenTicket] = useState(null);

  useEffect(() => {
    api.incident(incidentId).then(setIncident).catch((err) => setError(err.message));
  }, [incidentId]);

  function saved(next) {
    setIncident(next);
    onChanged();
  }

  if (error) return <p className="error">{error}</p>;
  if (!incident) return <p className="hint">Loading incident…</p>;
  const s = incident.summary;

  return (
    <article className="incident-detail" aria-labelledby={`incident-title-${incident.incident_id}`}>
      <header className="thread-header">
        <div className="thread-meta">
          <span className="thread-id">{incident.incident_id}</span>
          <IncidentStatusChip status={incident.status} />
          <Confidence value={incident.confidence} />
        </div>
        <h2 className="thread-title" id={`incident-title-${incident.incident_id}`}>
          {incident.title}
        </h2>
        <dl className="thread-facts">
          <div>
            <dt>Tickets</dt>
            <dd>{incident.ticket_count}</dd>
          </div>
          <div>
            <dt>Customers</dt>
            <dd>{incident.customer_count}</dd>
          </div>
          <div>
            <dt>Reports</dt>
            <dd>
              {absoluteTime(incident.first_report)} – {absoluteTime(incident.latest_report)}
            </dd>
          </div>
          <div>
            <dt>Departments</dt>
            <dd>
              {incident.needs_routing ? (
                <span className="chip chip-warn">Needs routing</span>
              ) : (
                incident.departments.map((d) => d.name + (d.source === "ai" ? " (AI)" : "")).join(", ")
              )}
            </dd>
          </div>
        </dl>
      </header>

      <section className="ai-panel" aria-label="AI analysis">
        <header className="ai-panel-head">
          <h3>AI analysis</h3>
          <span className="ai-panel-tag">Suggestion only · nothing has been verified</span>
        </header>
        {s.narrative && <p className="ai-panel-issue">{s.narrative}</p>}
        {s.why_related && (
          <div className="incident-section">
            <h4>Why these may be related</h4>
            <p>{s.why_related}</p>
          </div>
        )}
        <div className="facts-hypotheses">
          <ListSection title="Observed facts (from the tickets)" items={s.observed_facts} />
          <ListSection title="Hypotheses (not verified)" items={s.hypotheses} />
        </div>
        <ListSection title="Common symptoms" items={s.common_symptoms} />
        <ListSection title="Affected services" items={s.affected_services} />
        <div className="incident-section">
          <h4>Possible shared cause</h4>
          <p>{s.possible_shared_cause}</p>
        </div>
        <ListSection title="Confidence reasoning" items={s.confidence_reasons} />
        <ListSection title="Suggested next steps" items={s.suggested_next_steps} />
      </section>

      <section aria-labelledby={`incident-tickets-${incident.incident_id}`}>
        <h3 className="section-title" id={`incident-tickets-${incident.incident_id}`}>
          Related tickets
        </h3>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Ticket</th>
                <th>Customer</th>
                <th>Category</th>
                <th>Status</th>
                <th>Reported</th>
                <th>
                  <span className="sr-only">Open</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {incident.tickets.map((t) => (
                <tr key={t.ticket_id}>
                  <td className="wrap">
                    <span className="mono">{t.ticket_id}</span>
                    <br />
                    {t.subject}
                  </td>
                  <td>{t.customer_name}</td>
                  <td>{categoryLabel(t.category)}</td>
                  <td>
                    <StatusChip status={t.status} />
                  </td>
                  <td>
                    <time dateTime={t.created_at} title={absoluteTime(t.created_at)}>
                      {relativeTime(t.created_at)}
                    </time>
                  </td>
                  <td>
                    <button type="button" onClick={() => setOpenTicket(t.ticket_id)}>
                      Open
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {openTicket && (
        <div className="card incident-ticket">
          <div className="incident-ticket-head">
            <span className="hint">Original ticket (read-only)</span>
            <button type="button" onClick={() => setOpenTicket(null)}>
              Close
            </button>
          </div>
          <StaffTicketView key={openTicket} ticketId={openTicket} />
        </div>
      )}

      <section className="incident-actions" aria-label="Review">
        <DepartmentsEditor key={incident.departments.map((d) => d.department_id).join()} incident={incident} onSaved={saved} />
        <ReviewForm incident={incident} onSaved={saved} />
        {incident.reviews.length > 0 && (
          <ol className="incident-reviews">
            {incident.reviews.map((r, i) => (
              <li key={i}>
                <strong>{r.reviewer_name}</strong> · {ACTION_LABEL[r.action] || r.action}
                {r.action === "departments_changed" ? ` ${r.note}` : `: ${r.note}`}{" "}
                <time className="hint" dateTime={r.at} title={absoluteTime(r.at)}>
                  {relativeTime(r.at)}
                </time>
              </li>
            ))}
          </ol>
        )}
      </section>
    </article>
  );
}

export default function IncidentsPanel() {
  const [incidents, setIncidents] = useState(null);
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState(null);
  const [showDismissed, setShowDismissed] = useState(false);
  const [running, setRunning] = useState(false);
  const [notice, setNotice] = useState("");

  const load = useCallback(() => {
    api
      .incidents(showDismissed)
      .then((rows) => {
        setIncidents(rows);
        setError("");
      })
      .catch((err) => setError(err.message));
  }, [showDismissed]);

  useEffect(load, [load]);

  async function runNow() {
    setRunning(true);
    setNotice("");
    try {
      const { touched } = await api.incidentsRun();
      setNotice(touched.length ? `Updated ${touched.length} incident(s).` : "No new related reports found.");
      load();
    } catch (err) {
      setError(err.message);
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="portal-layout staff-layout">
      <aside className="my-tickets" aria-labelledby="incidents-heading">
        <div className="section-head">
          <h2 className="my-tickets-title" id="incidents-heading">
            Incidents
          </h2>
          <button type="button" onClick={runNow} disabled={running}>
            {running ? "Checking…" : "Check now"}
          </button>
        </div>
        <label className="checkbox-label">
          <input type="checkbox" checked={showDismissed} onChange={(e) => setShowDismissed(e.target.checked)} />
          Show dismissed
        </label>
        {notice && (
          <p className="hint" role="status">
            {notice}
          </p>
        )}
        {error && <p className="error">{error}</p>}
        {!error && !incidents && <p className="hint">Loading…</p>}
        {incidents && incidents.length === 0 && (
          <p className="empty-state">
            No incidents. When several customers report similar problems, the AI groups them here for review.
          </p>
        )}
        {incidents && incidents.length > 0 && (
          <ul className="my-tickets-list">
            {incidents.map((inc) => (
              <li key={inc.incident_id}>
                <button
                  type="button"
                  className={`my-ticket ${selectedId === inc.incident_id ? "is-selected" : ""}`}
                  aria-current={selectedId === inc.incident_id ? "true" : undefined}
                  onClick={() => setSelectedId(inc.incident_id)}
                >
                  <span className="my-ticket-top">
                    <span className="my-ticket-id">{inc.incident_id}</span>
                    <time className="my-ticket-time" dateTime={inc.latest_report}>
                      {relativeTime(inc.latest_report)}
                    </time>
                  </span>
                  <span className="my-ticket-subject">{inc.title}</span>
                  <span className="my-ticket-customer">
                    {inc.ticket_count} tickets · {inc.customer_count} customers
                  </span>
                  <span className="my-ticket-bottom">
                    <IncidentStatusChip status={inc.status} />
                    <Confidence value={inc.confidence} />
                    {inc.needs_routing && <span className="chip chip-warn">Needs routing</span>}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </aside>
      <div className="portal-main">
        {selectedId ? (
          <div className="card">
            <IncidentDetail key={selectedId} incidentId={selectedId} onChanged={load} />
          </div>
        ) : (
          <div className="empty">
            <p className="empty-title">Pick an incident to review</p>
            <p className="empty-body">
              The AI only suggests that reports are related. Confirm, investigate or dismiss each one after
              checking the tickets.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
