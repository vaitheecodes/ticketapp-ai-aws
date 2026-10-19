/* Technician workspace.
 *
 * Left: ticket lists (Unassigned, My tickets, Recently updated, Open &
 * pending) with search and priority/status filters; each row shows
 * priority, age, customer and the AI hand-off preview. Right: the selected
 * ticket (StaffTicketView) — claim, reply, notes, status.
 */

import { useCallback, useEffect, useId, useState } from "react";
import { api } from "../api";
import ChatReviews from "../components/ChatReviews";
import IncidentsPanel from "../components/IncidentsPanel";
import PortalHeader from "../components/PortalHeader";
import StaffTicketRow from "../components/StaffTicketRow";
import StaffTicketView from "../components/StaffTicketView";
import { STATUS_KEYS, statusLabel } from "../utils/statusLabels";

const VIEWS = [
  ["unassigned", "Unassigned"],
  ["mine", "My tickets"],
  ["recent", "Recently updated"],
  ["open_pending", "Open & pending"],
];

function Workspace() {
  const [view, setView] = useState("unassigned");
  const [q, setQ] = useState("");
  const [priority, setPriority] = useState("");
  const [status, setStatus] = useState("");
  const [tickets, setTickets] = useState(null);
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState(null);
  const searchId = useId();
  const priorityId = useId();
  const statusId = useId();

  const load = useCallback(() => {
    api
      .staffTickets({ view, q: q.trim(), priority, status })
      .then((rows) => {
        setTickets(rows);
        setError("");
      })
      .catch((err) => setError(err.message));
  }, [view, q, priority, status]);

  // Debounce typing in the search box; filter/view changes load at once too.
  useEffect(() => {
    const timer = setTimeout(load, 250);
    return () => clearTimeout(timer);
  }, [load]);

  return (
    <div className="portal-layout staff-layout">
      <aside className="my-tickets" aria-label="Ticket lists">
        <div className="view-tabs" role="group" aria-label="Ticket list">
          {VIEWS.map(([key, label]) => (
            <button
              key={key}
              type="button"
              className={view === key ? "active" : ""}
              aria-pressed={view === key}
              onClick={() => {
                setTickets(null);
                setView(key);
              }}
            >
              {label}
            </button>
          ))}
        </div>
        <label className="sr-only" htmlFor={searchId}>
          Search tickets
        </label>
        <input
          id={searchId}
          type="search"
          className="my-tickets-search"
          placeholder="Search ticket #, subject, customer"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <div className="list-filters">
          <label className="inline-select" htmlFor={priorityId}>
            <span>Priority</span>
            <select id={priorityId} value={priority} onChange={(e) => setPriority(e.target.value)}>
              <option value="">Any</option>
              {["urgent", "high", "medium", "low"].map((p) => (
                <option key={p} value={p}>
                  {p[0].toUpperCase() + p.slice(1)}
                </option>
              ))}
            </select>
          </label>
          <label className="inline-select" htmlFor={statusId}>
            <span>Status</span>
            <select id={statusId} value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">Any</option>
              {STATUS_KEYS.map((key) => (
                <option key={key} value={key}>
                  {statusLabel(key)}
                </option>
              ))}
            </select>
          </label>
        </div>
        {error && <p className="error">{error}</p>}
        {!error && !tickets && <p className="hint">Loading…</p>}
        {tickets && tickets.length === 0 && (
          <p className="empty-state">
            {view === "unassigned" ? "Nothing waiting to be claimed." : "No tickets match."}
          </p>
        )}
        {tickets && tickets.length > 0 && (
          <ul className="my-tickets-list">
            {tickets.map((t) => (
              <li key={t.ticket_id}>
                <StaffTicketRow t={t} selected={selectedId === t.ticket_id} onSelect={setSelectedId} />
              </li>
            ))}
          </ul>
        )}
      </aside>
      <div className="portal-main">
        {selectedId ? (
          <div className="card">
            <StaffTicketView key={selectedId} ticketId={selectedId} onChanged={load} />
          </div>
        ) : (
          <div className="empty">
            <p className="empty-title">Pick a ticket</p>
            <p className="empty-body">
              Claim one from Unassigned to become its owner, then reply to the customer or add internal
              notes for the team.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

export default function TechnicianPortal() {
  const [tab, setTab] = useState("workspace");

  return (
    <div className="page page-portal">
      <PortalHeader title="Technician Workspace" role="technician" />
      <nav className="tabs">
        <button className={tab === "workspace" ? "active" : ""} onClick={() => setTab("workspace")}>
          Tickets
        </button>
        <button className={tab === "reviews" ? "active" : ""} onClick={() => setTab("reviews")}>
          Chat Reviews
        </button>
        <button className={tab === "incidents" ? "active" : ""} onClick={() => setTab("incidents")}>
          Incidents
        </button>
      </nav>
      {tab === "workspace" && <Workspace />}
      {tab === "reviews" && <ChatReviews />}
      {/* Only incidents a manager assigned to this technician. */}
      {tab === "incidents" && <IncidentsPanel canRun={false} />}
    </div>
  );
}
