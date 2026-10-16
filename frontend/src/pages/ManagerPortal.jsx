/* Manager dashboard: incidents first (AI-detected clusters of related
 * reports, for review), then oversight of the tickets in the manager's
 * departments and each technician's workload on them. Read-only for
 * tickets: technicians claim their own work. */

import { useEffect, useState } from "react";
import { api } from "../api";
import IncidentsPanel from "../components/IncidentsPanel";
import PortalHeader from "../components/PortalHeader";
import StaffTicketView from "../components/StaffTicketView";
import { categoryLabel } from "../utils/categories";
import StaffTicketRow from "../components/StaffTicketRow";

export default function ManagerPortal() {
  const [tab, setTab] = useState("incidents");

  return (
    <div className="page page-portal">
      <PortalHeader title="Manager Dashboard" role="manager" />
      <nav className="tabs">
        <button className={tab === "incidents" ? "active" : ""} onClick={() => setTab("incidents")}>
          Incidents
        </button>
        <button className={tab === "tickets" ? "active" : ""} onClick={() => setTab("tickets")}>
          Department tickets
        </button>
      </nav>
      {tab === "incidents" ? <IncidentsPanel /> : <DepartmentOverview />}
    </div>
  );
}

function DepartmentOverview() {
  const [overview, setOverview] = useState(null);
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState(null);

  useEffect(() => {
    api.managerOverview().then(setOverview).catch((err) => setError(err.message));
  }, []);

  return (
    <div>
      {error && <p className="error">{error}</p>}
      {!error && !overview && <p className="hint">Loading…</p>}
      {overview && overview.categories.length === 0 && (
        <p className="empty-state">
          You don't own any departments yet. Ask your administrator to assign you one from the Team page.
        </p>
      )}
      {overview && overview.categories.length > 0 && (
        <>
          <section className="manager-summary" aria-label="Your scope">
            <div className="card stat-card">
              <span className="stat-value">{overview.unassigned}</span>
              <span className="stat-label">Unassigned in your departments</span>
            </div>
            <div className="card scope-card">
              <h2 className="section-title">Your categories</h2>
              <ul className="category-chips">
                {overview.categories.map((c) => (
                  <li key={c} className="chip chip-neutral">
                    {categoryLabel(c)}
                  </li>
                ))}
              </ul>
            </div>
          </section>

          <section aria-labelledby="workload-heading">
            <h2 className="section-title" id="workload-heading">
              Technician workload (your departments)
            </h2>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Technician</th>
                    <th>Open</th>
                    <th>In Progress</th>
                    <th>Pending Customer</th>
                    <th>Resolved</th>
                  </tr>
                </thead>
                <tbody>
                  {overview.workload.map((w) => (
                    <tr key={w.staff_id}>
                      <td>{w.name}</td>
                      <td>{w.open}</td>
                      <td>{w.in_progress}</td>
                      <td>{w.pending_customer}</td>
                      <td>{w.resolved}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <div className="portal-layout staff-layout manager-tickets">
            <aside className="my-tickets" aria-labelledby="dept-tickets-heading">
              <h2 className="my-tickets-title" id="dept-tickets-heading">
                Department tickets
              </h2>
              {overview.tickets.length === 0 ? (
                <p className="empty-state">No tickets in your departments yet.</p>
              ) : (
                <ul className="my-tickets-list">
                  {overview.tickets.map((t) => (
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
                  <StaffTicketView key={selectedId} ticketId={selectedId} />
                </div>
              ) : (
                <div className="empty">
                  <p className="empty-title">Pick a ticket to inspect it</p>
                  <p className="empty-body">You'll see the full conversation, internal notes and AI hand-off, read-only.</p>
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
