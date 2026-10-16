/* Staff view of one ticket (technicians work it, managers read it).
 *
 *   header      ticket #, subject, status, priority, customer, owner, age,
 *               Claim / status control (only what the backend's `can` allows)
 *   AI panel    hand-off summary, suggested steps, chat transcript — kept
 *               apart from the conversation so nobody mistakes it for
 *               something the customer saw
 *   thread      every entry, with internal notes and staff-only events
 *               marked as private
 *   composer    Public reply (email-style: subject + body, optional status)
 *               or Internal note — visually distinct modes
 *
 * Every mutation returns the whole workspace, so the view re-renders from
 * server state.
 */

import { useCallback, useEffect, useId, useState } from "react";
import { api } from "../api";
import { categoryLabel } from "../utils/categories";
import { Markdown } from "../utils/markdown";
import { absoluteTime, relativeTime } from "../utils/time";
import { STATUS_KEYS, statusLabel } from "../utils/statusLabels";
import { formatBytes } from "../utils/text";
import { PaperclipIcon } from "./AttachmentPicker";
import ChatMessage from "./ui/ChatMessage";
import { IncidentStatusChip, StatusChip } from "./ui/StatusChip";

const statusOptions = () => STATUS_KEYS.map((key) => [key, statusLabel(key)]);

function initials(name) {
  const parts = (name || "").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}

function AiPanel({ ai, chat }) {
  const summary = ai.summary;
  const suggestions = ai.suggestions?.length ? ai.suggestions : summary?.suggested_next_steps || [];
  const hasAnything = summary || ai.case_summary || suggestions.length || chat;
  if (!hasAnything) return null;

  const sections = summary
    ? [
        ["Already tried", summary.tried],
        ["Findings", summary.findings],
        ["Unresolved questions", summary.unresolved_questions],
      ].filter(([, items]) => items?.length)
    : [];

  return (
    <section className="ai-panel" aria-labelledby="ai-panel-title">
      <header className="ai-panel-head">
        <h3 id="ai-panel-title">AI hand-off</h3>
        <span className="ai-panel-tag">Staff only · not shown to the customer</span>
      </header>
      {summary?.source === "fallback" && (
        <p className="pending-text">The AI summary was unavailable — read the transcript.</p>
      )}
      {summary?.issue && <p className="ai-panel-issue">{summary.issue}</p>}
      {!summary && ai.case_summary && <p className="ai-panel-issue">{ai.case_summary}</p>}
      {sections.map(([title, items]) => (
        <div key={title} className="ai-panel-section">
          <h4>{title}</h4>
          <ul>
            {items.map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>
        </div>
      ))}
      {suggestions.length > 0 && (
        <div className="ai-panel-section">
          <h4>Suggested next steps</h4>
          <ol>
            {suggestions.map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ol>
          <p className="hint">Suggestions to check — not results. Nothing here has been verified.</p>
        </div>
      )}
      {chat && (
        <details className="chat-transcript">
          <summary>Chat transcript ({chat.transcript.length} messages)</summary>
          <div className="chat-scroll is-readonly">
            {chat.transcript.map((m) => (
              <ChatMessage key={m.message_id} sender={m.role} message={m.content} createdAt={m.created_at} />
            ))}
          </div>
        </details>
      )}
    </section>
  );
}

function StaffMessage({ ticketId, m }) {
  if (m.sender === "system") {
    return (
      <li className={`thread-system ${m.customer_visible ? "" : "is-internal"}`}>
        <span className="msg-system-rule" aria-hidden="true" />
        <span>
          {!m.customer_visible && <span className="private-tag">Staff only</span>} {m.message}{" "}
          <time dateTime={m.created_at}>· {relativeTime(m.created_at)}</time>
        </span>
        <span className="msg-system-rule" aria-hidden="true" />
      </li>
    );
  }
  const internal = m.visibility === "internal_note";
  const kind = internal ? "note" : m.sender;
  return (
    <li className={`email-card email-card-${kind}`}>
      <article aria-label={`${internal ? "Internal note" : "Message"} from ${m.author}`}>
        <header className="email-head">
          <span className="email-avatar" aria-hidden="true">
            {m.sender === "ai" ? "AI" : initials(m.author)}
          </span>
          <div className="email-from">
            <div className="email-from-line">
              <span className="email-sender">{m.author}</span>
              {internal ? (
                <span className="private-tag">Internal note · not visible to customer</span>
              ) : (
                m.sender !== "customer" && (
                  <span className={`email-badge email-badge-${m.sender}`}>
                    {m.sender === "ai" ? "AI Assistant" : "Support Agent"}
                  </span>
                )
              )}
            </div>
            {m.subject && <div className="email-to">Subject: {m.subject}</div>}
          </div>
          <time className="email-time" dateTime={m.created_at} title={absoluteTime(m.created_at)}>
            {relativeTime(m.created_at)}
          </time>
        </header>
        <div className="email-body">
          {m.sender === "customer" ? <p className="email-plain">{m.message}</p> : <Markdown breaks>{m.message}</Markdown>}
        </div>
        {m.attachments.length > 0 && (
          <ul className="file-chips" aria-label="Attachments">
            {m.attachments.map((a) => (
              <li key={a.attachment_id}>
                <button type="button" className="file-chip file-chip-link" onClick={() => api.downloadAttachment(ticketId, a)}>
                  <PaperclipIcon />
                  <span className="file-chip-name">{a.filename}</span>
                  <span className="file-chip-size">{formatBytes(a.size_bytes)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </article>
    </li>
  );
}

function Composer({ ws, onSaved }) {
  const { can, ticket } = ws;
  const [mode, setMode] = useState(can.reply ? "public_reply" : "internal_note");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [setStatus, setSetStatus] = useState("pending_customer");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const subjectId = useId();
  const bodyId = useId();
  const statusId = useId();
  const isNote = mode === "internal_note";

  if (!can.note && !can.reply) return null;

  async function handleSubmit(e) {
    e.preventDefault();
    if (!body.trim()) return;
    setBusy(true);
    setError("");
    try {
      const next = await api.staffMessage(ticket.ticket_id, {
        body: body.trim(),
        visibility: mode,
        subject: isNote ? null : subject.trim(),
        set_status: isNote ? null : setStatus,
      });
      setBody("");
      setSubject("");
      onSaved(next);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className={`staff-composer ${isNote ? "is-note" : ""}`} onSubmit={handleSubmit}>
      <div className="composer-modes" role="group" aria-label="Message type">
        <button
          type="button"
          className={mode === "public_reply" ? "active" : ""}
          aria-pressed={mode === "public_reply"}
          onClick={() => setMode("public_reply")}
          disabled={!can.reply}
          title={can.reply ? undefined : "Claim the ticket to reply to the customer"}
        >
          Public reply
        </button>
        <button
          type="button"
          className={isNote ? "active" : ""}
          aria-pressed={isNote}
          onClick={() => setMode("internal_note")}
        >
          Internal note
        </button>
      </div>
      {isNote ? (
        <p className="composer-hint">Only support staff will see this note.</p>
      ) : (
        <div className="field-group">
          <label className="field-label" htmlFor={subjectId}>
            Subject
          </label>
          <input
            id={subjectId}
            maxLength={200}
            placeholder={`Re: [${ticket.ticket_id}] ${ticket.subject}`}
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
          />
        </div>
      )}
      <div className="field-group">
        <label className="field-label" htmlFor={bodyId}>
          {isNote ? "Note" : `Message to ${ticket.customer.name}`}
        </label>
        <textarea
          id={bodyId}
          rows={6}
          maxLength={10000}
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder={isNote ? "What did you find? Next steps for the team…" : "Write your reply…"}
        />
        {!isNote && <p className="hint">Supports **bold**, `code` and numbered or bulleted lists.</p>}
      </div>
      <div className="composer-actions">
        {!isNote && (
          <div className="field-group composer-status">
            <label className="field-label" htmlFor={statusId}>
              Then set status to
            </label>
            <select id={statusId} value={setStatus} onChange={(e) => setSetStatus(e.target.value)}>
              {statusOptions().map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </div>
        )}
        <button type="submit" className="btn-primary" disabled={busy || !body.trim()}>
          {busy ? "Saving…" : isNote ? "Add internal note" : "Send reply"}
        </button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}

function History({ ws }) {
  const entries = [
    ...ws.status_history.map((h) => ({
      at: h.changed_at,
      text: h.from_status
        ? `${statusLabel(h.from_status)} → ${statusLabel(h.to_status)}`
        : `Created as ${statusLabel(h.to_status)}`,
      by: h.changed_by_name,
    })),
    ...ws.assignment_history.map((h) => ({
      at: h.changed_at,
      text: h.to_name ? `Owner → ${h.to_name}` : "Returned to the unassigned queue",
      by: h.changed_by_name,
    })),
  ].sort((a, b) => a.at.localeCompare(b.at));

  return (
    <details className="ticket-history">
      <summary>History ({entries.length})</summary>
      <ol>
        {entries.map((e, i) => (
          <li key={i}>
            <time dateTime={e.at} title={absoluteTime(e.at)}>
              {relativeTime(e.at)}
            </time>{" "}
            {e.text} <span className="hint">by {e.by}</span>
          </li>
        ))}
      </ol>
    </details>
  );
}

export default function StaffTicketView({ ticketId, onChanged }) {
  const [ws, setWs] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const apply = useCallback(
    (next) => {
      setWs(next);
      onChanged?.();
    },
    [onChanged],
  );

  // Parents key this component by ticket id, so it remounts per ticket.
  useEffect(() => {
    api.staffTicket(ticketId).then(setWs).catch((err) => setError(err.message));
  }, [ticketId]);

  async function run(call) {
    setBusy(true);
    setError("");
    try {
      apply(await call());
    } catch (err) {
      setError(err.message);
      // Someone else may have claimed it: show the current state.
      api.staffTicket(ticketId).then(setWs).catch(() => {});
    } finally {
      setBusy(false);
    }
  }

  if (error && !ws) return <p className="error">{error}</p>;
  if (!ws) {
    return (
      <div className="thread" aria-busy="true" aria-label="Loading ticket">
        <div className="skeleton skeleton-line" style={{ width: "40%" }} />
        <div className="skeleton skeleton-card" />
      </div>
    );
  }

  const { ticket, can } = ws;
  return (
    <section className="thread staff-ticket" aria-labelledby={`staff-title-${ticket.ticket_id}`}>
      <header className="thread-header">
        <div className="thread-meta">
          <span className="thread-id">{ticket.ticket_id}</span>
          <StatusChip status={ticket.status} />
          <span className={`priority-chip priority-${ticket.priority}`}>{ticket.priority}</span>
          {ticket.ticket_type === "chat" && <span className="type-badge">Chat</span>}
        </div>
        {ws.incidents.length > 0 && (
          <ul className="linked-incidents" aria-label="Linked incidents">
            {ws.incidents.map((inc) => (
              <li key={inc.incident_id}>
                Part of incident <span className="mono">{inc.incident_id}</span>: {inc.title}{" "}
                <IncidentStatusChip status={inc.status} />
              </li>
            ))}
          </ul>
        )}
        <h2 className="thread-title" id={`staff-title-${ticket.ticket_id}`}>
          {ticket.subject}
        </h2>
        <dl className="thread-facts">
          <div>
            <dt>Customer</dt>
            <dd>
              {ticket.customer.name} · {ticket.customer.email}
            </dd>
          </div>
          <div>
            <dt>Category</dt>
            <dd>{categoryLabel(ticket.category)}</dd>
          </div>
          <div>
            <dt>Owner</dt>
            <dd>{ticket.assignee?.name || "Unassigned"}</dd>
          </div>
          <div>
            <dt>Age</dt>
            <dd>
              <time dateTime={ticket.created_at} title={absoluteTime(ticket.created_at)}>
                {relativeTime(ticket.created_at)}
              </time>
            </dd>
          </div>
        </dl>
        <div className="staff-actions">
          {can.claim && (
            <button type="button" className="btn-primary" onClick={() => run(() => api.staffClaim(ticket.ticket_id))} disabled={busy}>
              {busy ? "Claiming…" : "Claim ticket"}
            </button>
          )}
          {can.change_status && (
            <label className="inline-select">
              <span>Status</span>
              <select
                value={ticket.status}
                disabled={busy}
                onChange={(e) => run(() => api.staffSetStatus(ticket.ticket_id, e.target.value))}
              >
                {statusOptions().map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          )}
          <History ws={ws} />
        </div>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </header>

      <AiPanel ai={ws.ai} chat={ws.chat} />

      <ol className="thread-messages">
        {ws.messages.map((m) => (
          <StaffMessage key={m.message_id} ticketId={ticket.ticket_id} m={m} />
        ))}
      </ol>

      <Composer key={`${ticket.ticket_id}-${can.reply}`} ws={ws} onSaved={apply} />
    </section>
  );
}
