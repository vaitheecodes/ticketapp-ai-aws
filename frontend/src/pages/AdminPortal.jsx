import { useEffect, useId, useState } from "react";
import { api } from "../api";
import PortalHeader from "../components/PortalHeader";
import ChatReviews from "../components/ChatReviews";
import RoutingPolicyPanel from "../components/RoutingPolicyPanel";
import TeamPanel from "../components/TeamPanel";
import { RoutePill, StatusPill } from "../components/StatusPill";
import { parseTicketText } from "../utils/parseTicketText";

function TicketTextCell({ ticketText }) {
  const { original, summary } = parseTicketText(ticketText);
  if (!summary) return original;
  return (
    <>
      <div>{original}</div>
      <div className="hint" style={{ marginTop: "0.35rem" }}>
        <strong>AI summary:</strong> {summary}
      </div>
    </>
  );
}

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

function AllTickets() {
  const [tickets, setTickets] = useState([]);
  const [technicians, setTechnicians] = useState([]);
  const [error, setError] = useState("");

  function refresh() {
    api.adminTickets().then(setTickets).catch((err) => setError(err.message));
  }

  useEffect(() => {
    refresh();
    api.adminTechnicians().then(setTechnicians).catch((err) => setError(err.message));
  }, []);

  if (error) return <p className="error">{error}</p>;
  if (tickets.length === 0) return <p className="empty-state">No tickets have been raised yet.</p>;

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Ticket</th>
            <th>Customer</th>
            <th>Channel</th>
            <th>Category</th>
            <th>Confidence</th>
            <th>Route</th>
            <th>Status</th>
            <th>Technician</th>
            <th>Text</th>
          </tr>
        </thead>
        <tbody>
          {tickets.map((t) => (
            <tr key={t.ticket_id}>
              <td>{t.ticket_id}</td>
              <td>{t.customer_id}</td>
              <td>{t.channel}</td>
              <td>{t.category || "—"}</td>
              <td>{t.confidence != null ? `${(t.confidence * 100).toFixed(0)}%` : "—"}</td>
              <td><RoutePill route={t.route} /></td>
              <td><StatusPill status={t.status} /></td>
              <td>
                <TechnicianAssignCell ticket={t} technicians={technicians} onReassigned={refresh} />
              </td>
              <td className="wrap"><TicketTextCell ticketText={t.ticket_text} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
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
    <div className="page wide">
      <PortalHeader title="Admin Portal" role="admin" />
      <nav className="tabs">
        <button className={tab === "tickets" ? "active" : ""} onClick={() => setTab("tickets")}>
          All Tickets
        </button>
        <button className={tab === "team" ? "active" : ""} onClick={() => setTab("team")}>
          Team
        </button>
        <button className={tab === "reviews" ? "active" : ""} onClick={() => setTab("reviews")}>
          Chat Reviews
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
        {tab === "try" && <TryATicket />}
        {tab === "policy" && <RoutingPolicyPanel />}
      </div>
    </div>
  );
}
