import { useEffect, useId, useState } from "react";
import { api } from "../api";
import { ChevronRightIcon } from "../components/Icons";
import PortalHeader from "../components/PortalHeader";
import { StatusPill } from "../components/StatusPill";
import TicketChat from "../components/TicketChat";

function PastTicketTranscript({ ticketId }) {
  const [messages, setMessages] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.ticketMessages(ticketId).then(setMessages).catch((err) => setError(err.message));
  }, [ticketId]);

  if (error) return <p className="error">{error}</p>;
  if (!messages) return <p className="hint">Loading conversation...</p>;

  return <TicketChat ticketId={ticketId} initialMessages={messages} readOnly />;
}

function PastTickets() {
  const [tickets, setTickets] = useState([]);
  const [expandedId, setExpandedId] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.ticketsMine().then(setTickets).catch((err) => setError(err.message));
  }, []);

  if (error) return <p className="error">{error}</p>;
  if (tickets.length === 0) return <p className="empty-state">No tickets yet — raise one from the other tab.</p>;

  return (
    <ul className="ticket-list">
      {tickets.map((t) => {
        const expanded = expandedId === t.ticket_id;
        return (
        <li key={t.ticket_id} className="ticket-item">
          <button
            className="ticket-summary"
            aria-expanded={expanded}
            aria-controls={`past-ticket-detail-${t.ticket_id}`}
            onClick={() => setExpandedId(expanded ? null : t.ticket_id)}
          >
            <ChevronRightIcon className={`disclosure-icon ${expanded ? "expanded" : ""}`} />
            <StatusPill status={t.status} />
            <span className="ticket-text-preview">{t.ticket_text.split("\n")[0]}</span>
          </button>
          {expanded && (
            <div className="ticket-detail" id={`past-ticket-detail-${t.ticket_id}`}>
              <dl style={{ margin: 0 }}>
                <dt>Category</dt>
                <dd>{t.category || "—"}</dd>
                <dt>Route</dt>
                <dd>
                  {
                    {
                      ai: "Resolved by AI",
                      clarify: "AI is asking a clarifying question",
                      human: "Escalated to a human agent",
                    }[t.route]
                  }
                </dd>
              </dl>
              <PastTicketTranscript ticketId={t.ticket_id} />
            </div>
          )}
        </li>
        );
      })}
    </ul>
  );
}

function RaiseTicket() {
  const [ticketText, setTicketText] = useState("");
  const [channel, setChannel] = useState("chat");
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const textId = useId();
  const channelId = useId();

  async function handleSubmit(e) {
    e.preventDefault();
    const text = ticketText.trim();
    if (!text) return;
    setSubmitting(true);
    setError("");
    setResult(null);
    try {
      const res = await api.ticketsNew(text, channel);
      setResult({ ...res, submittedText: text });
      setTicketText("");
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="card">
      <form className="ticket-form" onSubmit={handleSubmit}>
        <div className="field-group">
          <label className="field-label" htmlFor={textId}>
            Describe your issue
          </label>
          <textarea
            id={textId}
            rows={5}
            placeholder="e.g. I want to track my order ORD-123456"
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
          <button type="submit" className="btn-primary" disabled={submitting}>
            {submitting ? "Submitting..." : "Submit ticket"}
          </button>
        </div>
      </form>
      {error && <p className="error">{error}</p>}
      {result && (result.route === "ai" || result.route === "clarify") && (
        <TicketChat
          key={result.ticket_id}
          ticketId={result.ticket_id}
          initialMessages={[
            { sender: "customer", message: result.submittedText },
            { sender: "ai", message: result.ai_response },
          ]}
        />
      )}
      {result && result.route === "human" && (
        <div className="result-box">
          <p className="pending-text">Your ticket has been routed to a human agent.</p>
        </div>
      )}
    </div>
  );
}

export default function CustomerPortal() {
  const [tab, setTab] = useState("past");

  return (
    <div className="page">
      <PortalHeader title="Customer Portal" role="customer" />
      <nav className="tabs">
        <button className={tab === "past" ? "active" : ""} onClick={() => setTab("past")}>
          Past Tickets
        </button>
        <button className={tab === "raise" ? "active" : ""} onClick={() => setTab("raise")}>
          Raise Ticket
        </button>
      </nav>
      <div className="tab-content">{tab === "past" ? <PastTickets /> : <RaiseTicket />}</div>
    </div>
  );
}
