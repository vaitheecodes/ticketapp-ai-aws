import { useEffect, useId, useRef, useState } from "react";
import { api } from "../api";
import PortalHeader from "../components/PortalHeader";
import AuditPanel from "../components/AuditPanel";
import ChatReviews from "../components/ChatReviews";
import CustomersPanel from "../components/CustomersPanel";
import SettingsPanel from "../components/SettingsPanel";
import IncidentsPanel from "../components/IncidentsPanel";
import RoutingPolicyPanel from "../components/RoutingPolicyPanel";
import TeamPanel from "../components/TeamPanel";
import StaffTicketView from "../components/StaffTicketView";
import { RoutePill } from "../components/StatusPill";
import { ConfidenceChip, RouteChip, StatusChip } from "../components/ui/StatusChip";
import { categoryLabel } from "../utils/categories";
import { parseTicketText } from "../utils/parseTicketText";
import { STATUS_KEYS, statusLabel } from "../utils/statusLabels";
import { absoluteTime, relativeTime } from "../utils/time";

function TechnicianAssignCell({ ticket, technicians, onReassigned }) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  if (ticket.route !== "human") return <span className="hint">—</span>;

  async function handleChange(e) {
    const technicianId = e.target.value;
    setSaving(true);
    setError("");
    try {
      await api.adminReassignTicket(ticket.ticket_id, technicianId);
      onReassigned();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <select
        className="select-compact"
        aria-label={`Handling technician for ticket ${ticket.ticket_id}`}
        value={ticket.assigned_technician_id || ""}
        onChange={handleChange}
        disabled={saving}
      >
        <option value="">Unassigned</option>
        {technicians.map((t) => (
          <option key={t.staff_id} value={t.staff_id}>
            {t.name}
          </option>
        ))}
      </select>
      {error && <div className="error" style={{ marginTop: "0.25rem" }}>{error}</div>}
    </div>
  );
}

/* Every ticket, one compact row each. The long text (opening message, AI
 * hand-off summary, thread, notes, history) lives in the detail panel
 * opened from the row, so rows stay one line tall. */
function AllTickets() {
  const [tickets, setTickets] = useState(null);
  const [technicians, setTechnicians] = useState([]);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [openId, setOpenId] = useState(null);
  const detailRef = useRef(null);
  const searchId = useId();
  const statusId = useId();

  function refresh() {
    api.adminTickets().then(setTickets).catch((err) => setError(err.message));
  }

  useEffect(() => {
    refresh();
    api.adminTechnicians().then(setTechnicians).catch((err) => setError(err.message));
  }, []);

  function open(ticketId) {
    setOpenId(ticketId);
    requestAnimationFrame(() => detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
  }

  if (error) return <p className="error">{error}</p>;
  if (!tickets) return <p className="hint">Loading tickets…</p>;
  if (tickets.length === 0) return <p className="empty-state">No tickets have been raised yet.</p>;

  const q = query.trim().toLowerCase();
  const visible = tickets.filter(
    (t) =>
      (!status || t.status === status) &&
      (!q ||
        [t.ticket_id, subjectOf(t), t.customer_name, t.customer_email, t.category]
          .filter(Boolean)
          .some((v) => v.toLowerCase().includes(q))),
  );

  return (
    <div className="admin-tickets">
      <div className="list-filters">
        <label className="sr-only" htmlFor={searchId}>
          Search tickets
        </label>
        <input
          id={searchId}
          type="search"
          className="table-search"
          placeholder="Search ticket #, subject, customer"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
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
        <span className="hint">
          {visible.length} of {tickets.length}
        </span>
      </div>

      <div className="table-wrap">
        <table className="tickets-table">
          <thead>
            <tr>
              <th>Ticket</th>
              <th>Customer</th>
              <th>Category</th>
              <th>AI route</th>
              <th>Status</th>
              <th>Technician</th>
              <th>Created</th>
              <th>
                <span className="sr-only">Open</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {visible.map((t) => (
              <tr key={t.ticket_id} className={openId === t.ticket_id ? "is-selected" : ""}>
                <td className="ticket-cell">
                  <span className="ticket-cell-id">
                    <span className="mono">{t.ticket_id}</span>
                    {t.ticket_type === "chat" && <span className="type-badge">Chat</span>}
                  </span>
                  <span className="ticket-cell-subject" title={subjectOf(t)}>
                    {subjectOf(t)}
                  </span>
                </td>
                <td className="customer-cell">
                  <span>{t.customer_name}</span>
                  <span className="hint">{t.customer_email}</span>
                </td>
                <td>{categoryLabel(t.category || t.requested_category)}</td>
                <td>
                  <span className="route-cell">
                    <RouteChip route={t.route} />
                    <ConfidenceChip confidence={t.confidence} />
                  </span>
                </td>
                <td>
                  <StatusChip status={t.status} />
                </td>
                <td>
                  <TechnicianAssignCell ticket={t} technicians={technicians} onReassigned={refresh} />
                </td>
                <td>
                  <time dateTime={t.created_at} title={absoluteTime(t.created_at)}>
                    {relativeTime(t.created_at)}
                  </time>
                </td>
                <td>
                  <button
                    type="button"
                    aria-expanded={openId === t.ticket_id}
                    onClick={() => (openId === t.ticket_id ? setOpenId(null) : open(t.ticket_id))}
                  >
                    {openId === t.ticket_id ? "Close" : "Open"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div ref={detailRef} className="admin-ticket-detail">
        {openId && (
          <div className="card">
            <StaffTicketView key={openId} ticketId={openId} />
          </div>
        )}
      </div>
    </div>
  );
}

// Older tickets have no subject: fall back to the first line of the text.
function subjectOf(ticket) {
  if (ticket.subject) return ticket.subject;
  return parseTicketText(ticket.ticket_text).original.split("\n")[0];
}

function TryATicket() {
  const [ticketText, setTicketText] = useState("");
  const [channel, setChannel] = useState("chat");
  const [customerId, setCustomerId] = useState("");
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [maxAttempts, setMaxAttempts] = useState(null);
  const textId = useId();
  const channelId = useId();
  const customerIdFieldId = useId();

  useEffect(() => {
    api.debugRoutingPolicy().then((p) => setMaxAttempts(p.max_clarification_attempts));
  }, []);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!ticketText.trim()) return;
    setSubmitting(true);
    setError("");
    setResult(null);
    try {
      const res = await api.adminSimulateTicket(ticketText.trim(), channel, customerId.trim());
      setResult(res);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div>
      <p className="hint">
        Runs a ticket through the live pipeline (real Azure OpenAI calls) without saving it —
        for inspecting how classify_ticket / decide_route reason about a given message.
      </p>
      <div className="card">
        <form className="ticket-form" onSubmit={handleSubmit}>
          <div className="field-group">
            <label className="field-label" htmlFor={textId}>
              Ticket text to classify
            </label>
            <textarea
              id={textId}
              rows={4}
              placeholder="e.g. I want a refund for my last order"
              value={ticketText}
              onChange={(e) => setTicketText(e.target.value)}
              required
            />
          </div>
          <div className="field-row">
            <div className="field-group">
              <label className="field-label" htmlFor={channelId}>
                Channel
              </label>
              <select id={channelId} value={channel} onChange={(e) => setChannel(e.target.value)}>
                <option value="chat">Chat</option>
                <option value="email">Email</option>
              </select>
            </div>
            <div className="field-group" style={{ flex: 1 }}>
              <label className="field-label" htmlFor={customerIdFieldId}>
                Customer ID (optional)
              </label>
              <input
                id={customerIdFieldId}
                value={customerId}
                onChange={(e) => setCustomerId(e.target.value)}
              />
            </div>
            <button type="submit" className="btn-primary" disabled={submitting}>
              {submitting ? "Running..." : "Run pipeline"}
            </button>
          </div>
        </form>
        {error && <p className="error">{error}</p>}
      </div>

      {result && (
        <div className="card" style={{ marginTop: "1rem" }}>
          <div className="trace-grid">
            <div className="trace-stat">
              <div className="trace-stat-label">Category</div>
              <div className="trace-stat-value">{result.category}</div>
            </div>
            <div className="trace-stat">
              <div className="trace-stat-label">Confidence</div>
              <div className="trace-stat-value">{(result.confidence * 100).toFixed(0)}%</div>
            </div>
            <div className="trace-stat">
              <div className="trace-stat-label">Route</div>
              <div className="trace-stat-value">
                <RoutePill route={result.route} />
              </div>
            </div>
          </div>

          <p>
            <strong>Language tags:</strong>{" "}
            {result.language_tags?.length ? result.language_tags.join(", ") : "none detected"}
          </p>
          <p>
            <strong>Route reason:</strong> {result.route_reason}
          </p>

          {result.route === "ai" && (
            <div className="result-box">
              <p className="success-text">Would auto-resolve</p>
              <p>{result.ai_response}</p>
            </div>
          )}
          {result.route === "clarify" && (
            <div className="result-box">
              <p className="pending-text">
                Would ask a clarifying question (attempt {result.clarification_attempts}
                {maxAttempts != null ? `/${maxAttempts}` : ""})
              </p>
              <p>{result.ai_response}</p>
              <p className="hint" style={{ margin: 0 }}>
                Re-invoking the graph with this question + a reply would re-run
                classify_ticket/decide_route on the fuller transcript.
              </p>
            </div>
          )}
          {result.route === "human" && (
            <div className="result-box">
              <p className="pending-text">Would escalate to a human agent</p>
              <p className="hint" style={{ margin: 0 }}>
                Technicians would only ever see the ticket text — not this category/confidence/reasoning.
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function AdminPortal() {
  const [tab, setTab] = useState("tickets");

  return (
    <div className="page page-portal">
      <PortalHeader title="Admin Portal" role="admin" />
      <nav className="tabs">
        <button className={tab === "tickets" ? "active" : ""} onClick={() => setTab("tickets")}>
          All Tickets
        </button>
        <button className={tab === "team" ? "active" : ""} onClick={() => setTab("team")}>
          Team
        </button>
        <button className={tab === "customers" ? "active" : ""} onClick={() => setTab("customers")}>
          Customers
        </button>
        <button className={tab === "reviews" ? "active" : ""} onClick={() => setTab("reviews")}>
          Chat Reviews
        </button>
        <button className={tab === "incidents" ? "active" : ""} onClick={() => setTab("incidents")}>
          Incidents
        </button>
        <button className={tab === "activity" ? "active" : ""} onClick={() => setTab("activity")}>
          Activity
        </button>
        <button className={tab === "settings" ? "active" : ""} onClick={() => setTab("settings")}>
          Settings
        </button>
        <button className={tab === "try" ? "active" : ""} onClick={() => setTab("try")}>
          Try a Ticket
        </button>
        <button className={tab === "policy" ? "active" : ""} onClick={() => setTab("policy")}>
          Routing Policy
        </button>
      </nav>
      <div className="tab-content">
        {tab === "tickets" && <AllTickets />}
        {tab === "team" && <TeamPanel />}
        {tab === "reviews" && <ChatReviews />}
        {tab === "incidents" && <IncidentsPanel />}
        {tab === "activity" && <AuditPanel />}
        {tab === "settings" && <SettingsPanel />}
        {tab === "customers" && <CustomersPanel />}
        {tab === "try" && <TryATicket />}
        {tab === "policy" && <RoutingPolicyPanel />}
      </div>
    </div>
  );
}
