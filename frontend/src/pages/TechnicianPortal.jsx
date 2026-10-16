import { useEffect, useState } from "react";
import { api } from "../api";
import { ChevronRightIcon } from "../components/Icons";
import PortalHeader from "../components/PortalHeader";
import { StatusPill } from "../components/StatusPill";
import TicketTextDetail from "../components/TicketTextDetail";
import { parseTicketText } from "../utils/parseTicketText";

// The case summary (when present) is what a technician scanning the queue
// actually needs — not the start of the raw "original + summary" string.
function previewFor(ticketText) {
  const { original, summary } = parseTicketText(ticketText);
  return summary || original;
}

function Queue({ onAssigned }) {
  const [tickets, setTickets] = useState([]);
  const [expandedId, setExpandedId] = useState(null);
  const [error, setError] = useState("");

  function refresh() {
    api.queue().then(setTickets).catch((err) => setError(err.message));
  }

  useEffect(refresh, []);

  async function assign(ticketId) {
    try {
      await api.queueAssign(ticketId);
      refresh();
      onAssigned();
    } catch (err) {
      setError(err.message);
    }
  }

  if (error) return <p className="error">{error}</p>;
  if (tickets.length === 0) return <p className="empty-state">Queue is empty — nothing waiting on a human right now.</p>;

  return (
    <ul className="ticket-list">
      {tickets.map((t) => {
        const expanded = expandedId === t.ticket_id;
        return (
          <li key={t.ticket_id} className="ticket-item">
            <div className="ticket-item-row">
              <button
                className="ticket-summary"
                style={{ flex: 1 }}
                aria-expanded={expanded}
                aria-controls={`ticket-detail-${t.ticket_id}`}
                onClick={() => setExpandedId(expanded ? null : t.ticket_id)}
              >
                <ChevronRightIcon className={`disclosure-icon ${expanded ? "expanded" : ""}`} />
                <span className="ticket-text-preview">{previewFor(t.ticket_text)}</span>
              </button>
              <button className="btn-primary" onClick={() => assign(t.ticket_id)}>
                Assign to me
              </button>
            </div>
            {expanded && <TicketTextDetail id={`ticket-detail-${t.ticket_id}`} ticketText={t.ticket_text} />}
          </li>
        );
      })}
    </ul>
  );
}

function MyTickets() {
  const [tickets, setTickets] = useState([]);
  const [expandedId, setExpandedId] = useState(null);
  const [error, setError] = useState("");

  function refresh() {
    api.queueMine().then(setTickets).catch((err) => setError(err.message));
  }

  useEffect(refresh, []);

  async function resolve(ticketId) {
    try {
      await api.queueResolve(ticketId);
      refresh();
    } catch (err) {
      setError(err.message);
    }
  }

  if (error) return <p className="error">{error}</p>;
  if (tickets.length === 0) return <p className="empty-state">No tickets assigned to you yet.</p>;

  return (
    <ul className="ticket-list">
      {tickets.map((t) => {
        const expanded = expandedId === t.ticket_id;
        return (
          <li key={t.ticket_id} className="ticket-item">
            <div className="ticket-item-row">
              <button
                className="ticket-summary"
                style={{ flex: 1 }}
                aria-expanded={expanded}
                aria-controls={`ticket-detail-${t.ticket_id}`}
                onClick={() => setExpandedId(expanded ? null : t.ticket_id)}
              >
                <ChevronRightIcon className={`disclosure-icon ${expanded ? "expanded" : ""}`} />
                <StatusPill status={t.status} />
                <span className="ticket-text-preview">{previewFor(t.ticket_text)}</span>
              </button>
              {t.status !== "resolved" && (
                <button className="btn-primary" onClick={() => resolve(t.ticket_id)}>
                  Mark Resolved
                </button>
              )}
            </div>
            {expanded && <TicketTextDetail id={`ticket-detail-${t.ticket_id}`} ticketText={t.ticket_text} />}
          </li>
        );
      })}
    </ul>
  );
}

export default function TechnicianPortal() {
  const [tab, setTab] = useState("queue");
  const [refreshKey, setRefreshKey] = useState(0);

  return (
    <div className="page">
      <PortalHeader title="Technician Portal" role="technician" />
      <nav className="tabs">
        <button className={tab === "queue" ? "active" : ""} onClick={() => setTab("queue")}>
          Queue
        </button>
        <button className={tab === "mine" ? "active" : ""} onClick={() => setTab("mine")}>
          My Tickets
        </button>
      </nav>
      <div className="tab-content">
        {tab === "queue" ? (
          <Queue onAssigned={() => setRefreshKey((k) => k + 1)} />
        ) : (
          <MyTickets key={refreshKey} />
        )}
      </div>
    </div>
  );
}
