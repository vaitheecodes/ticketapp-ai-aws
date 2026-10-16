/* Ticket detail as an email thread (not chat bubbles).
 *
 * Header: ticket #, subject, status, category, created date. Then one
 * email-style card per message — customer, AI and staff share a single
 * layout and differ only by an accent bar and a role badge, so the thread
 * reads like a mailbox conversation. Long threads collapse their middle
 * (first message + the two latest stay open). An email-style reply
 * composer sits at the bottom, with Reopen once the ticket is resolved.
 */

import { useCallback, useEffect, useId, useState } from "react";
import { api } from "../api";
import { categoryLabel } from "../utils/categories";
import { Markdown } from "../utils/markdown";
import { absoluteTime, relativeTime } from "../utils/time";
import { formatBytes } from "../utils/text";
import AttachmentPicker, { PaperclipIcon } from "./AttachmentPicker";
import ChatMessage from "./ui/ChatMessage";
import { StatusChip } from "./ui/StatusChip";

// Collapse once a thread is longer than this; keep the first message and
// the latest COLLAPSE_KEEP_LATEST open.
const COLLAPSE_AFTER = 4;
const COLLAPSE_KEEP_LATEST = 2;

function initials(name) {
  const parts = (name || "").trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}

function AttachmentChips({ ticketId, attachments }) {
  const [error, setError] = useState("");
  if (!attachments.length) return null;
  return (
    <>
      <ul className="file-chips" aria-label="Attachments">
        {attachments.map((a) => (
          <li key={a.attachment_id}>
            <button
              type="button"
              className="file-chip file-chip-link"
              onClick={() => api.downloadAttachment(ticketId, a).catch((err) => setError(err.message))}
            >
              <PaperclipIcon />
              <span className="file-chip-name">{a.filename}</span>
              <span className="file-chip-size">{formatBytes(a.size_bytes)}</span>
            </button>
          </li>
        ))}
      </ul>
      {error && <p className="field-error">{error}</p>}
    </>
  );
}

function ThreadMessage({ ticketId, message }) {
  if (message.sender === "system") {
    return (
      <li className="thread-system">
        <span className="msg-system-rule" aria-hidden="true" />
        <span>
          {message.message} <time dateTime={message.created_at}>· {relativeTime(message.created_at)}</time>
        </span>
        <span className="msg-system-rule" aria-hidden="true" />
      </li>
    );
  }

  const isAi = message.sender === "ai";
  return (
    <li className={`email-card email-card-${message.sender}`}>
      <article aria-label={`${message.sender_name}, ${relativeTime(message.created_at)}`}>
        <header className="email-head">
          <span className="email-avatar" aria-hidden="true">
            {isAi ? "AI" : initials(message.sender_name)}
          </span>
          <div className="email-from">
            <div className="email-from-line">
              <span className="email-sender">{message.sender_name}</span>
              {message.sender !== "customer" && (
                <span className={`email-badge email-badge-${message.sender}`}>{message.sender_role}</span>
              )}
            </div>
            {message.to && <div className="email-to">to {message.to}</div>}
          </div>
          <time
            className="email-time"
            dateTime={message.created_at}
            title={absoluteTime(message.created_at)}
          >
            {relativeTime(message.created_at)}
          </time>
        </header>
        <div className="email-body">
          {message.sender === "customer" ? (
            <p className="email-plain">{message.message}</p>
          ) : (
            <Markdown breaks>{message.message}</Markdown>
          )}
        </div>
        <AttachmentChips ticketId={ticketId} attachments={message.attachments} />
      </article>
    </li>
  );
}

/* Tickets escalated from an AI chat: the conversation that led here, so
 * nobody (customer or technician) has to repeat it. Collapsed by default
 * to keep the thread itself in focus. */
function ChatTranscript({ chat }) {
  return (
    <details className="chat-transcript">
      <summary>
        Chat transcript <span className="hint">({chat.transcript.length} messages, before escalation)</span>
      </summary>
      <div className="chat-scroll is-readonly">
        {chat.transcript.map((m) => (
          <ChatMessage key={m.message_id} sender={m.role} message={m.content} createdAt={m.created_at} />
        ))}
      </div>
    </details>
  );
}

function ReplyComposer({ ticketId, onSent, disabled }) {
  const [text, setText] = useState("");
  const [files, setFiles] = useState([]);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const textId = useId();

  async function handleSubmit(e) {
    e.preventDefault();
    const message = text.trim();
    if (!message) return;
    setSending(true);
    setError("");
    try {
      const res = await api.ticketReply(ticketId, message);
      setText("");
      if (files.length) {
        try {
          await api.uploadAttachments(ticketId, res.message_id, files);
          setFiles([]);
        } catch (err) {
          setError(`Your reply was sent, but the attachments failed: ${err.message}`);
        }
      }
      onSent();
    } catch (err) {
      setError(err.message);
    } finally {
      setSending(false);
    }
  }

  return (
    <form className="reply-composer" onSubmit={handleSubmit}>
      <label className="reply-label" htmlFor={textId}>
        Reply
      </label>
      <textarea
        id={textId}
        className="control control-textarea"
        rows={4}
        placeholder="Write your reply…"
        value={text}
        onChange={(e) => setText(e.target.value)}
        disabled={disabled || sending}
      />
      <div className="reply-actions">
        <AttachmentPicker files={files} onChange={setFiles} disabled={disabled || sending} compact />
        <button type="submit" className="btn-primary" disabled={disabled || sending || !text.trim()}>
          {sending ? "Sending…" : "Send"}
        </button>
      </div>
      {sending && (
        <p className="hint" role="status">
          Sending — if our AI assistant is handling this ticket it will reply in a few seconds.
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}

export function ThreadSkeleton() {
  return (
    <div className="thread" aria-busy="true" aria-label="Loading ticket">
      <div className="skeleton skeleton-line" style={{ width: "40%" }} />
      <div className="skeleton skeleton-line" style={{ width: "70%", height: "1.5rem" }} />
      <div className="skeleton skeleton-card" />
      <div className="skeleton skeleton-card" />
    </div>
  );
}

export default function TicketThread({ ticketId, onChanged }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState(false);
  const [reopening, setReopening] = useState(false);

  const load = useCallback(() => {
    return api
      .ticketDetail(ticketId)
      .then((d) => {
        setDetail(d);
        setError("");
      })
      .catch((err) => setError(err.message));
  }, [ticketId]);

  useEffect(() => {
    setDetail(null);
    setExpanded(false);
    load().then(() => onChanged?.());
    // onChanged only refreshes the list's unread dot; don't reload on its identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load]);

  async function reopen() {
    setReopening(true);
    try {
      await api.ticketReopen(ticketId);
      await load();
      onChanged?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setReopening(false);
    }
  }

  if (error && !detail) return <p className="error">{error}</p>;
  if (!detail) return <ThreadSkeleton />;

  const { ticket, messages, chat } = detail;
  const collapsible = messages.length > COLLAPSE_AFTER && !expanded;
  const hiddenCount = collapsible ? messages.length - 1 - COLLAPSE_KEEP_LATEST : 0;
  const visible = collapsible
    ? [messages[0], null, ...messages.slice(-COLLAPSE_KEEP_LATEST)]
    : messages;

  return (
    <section className="thread" aria-labelledby={`thread-title-${ticket.ticket_id}`}>
      <header className="thread-header">
        <div className="thread-meta">
          <span className="thread-id">{ticket.ticket_id}</span>
          <StatusChip status={ticket.status} />
          {ticket.ticket_type === "chat" && <span className="type-badge">Chat</span>}
        </div>
        <h2 className="thread-title" id={`thread-title-${ticket.ticket_id}`}>
          {ticket.subject}
        </h2>
        <dl className="thread-facts">
          <div>
            <dt>Category</dt>
            <dd>{categoryLabel(ticket.category)}</dd>
          </div>
          <div>
            <dt>Priority</dt>
            <dd className="capitalize">{ticket.priority}</dd>
          </div>
          <div>
            <dt>Created</dt>
            <dd>
              <time dateTime={ticket.created_at}>{absoluteTime(ticket.created_at)}</time>
            </dd>
          </div>
        </dl>
      </header>

      {detail.incident_notice && (
        <p className="incident-banner" role="status">
          {detail.incident_notice}
        </p>
      )}

      {chat && <ChatTranscript chat={chat} />}

      <ol className="thread-messages">
        {visible.map((m) =>
          m === null ? (
            <li key="collapsed" className="thread-collapsed">
              <button type="button" className="thread-expand" onClick={() => setExpanded(true)}>
                Show {hiddenCount} earlier {hiddenCount === 1 ? "message" : "messages"}
              </button>
            </li>
          ) : (
            <ThreadMessage key={m.message_id} ticketId={ticket.ticket_id} message={m} />
          ),
        )}
      </ol>

      {error && <p className="error">{error}</p>}

      {ticket.status === "resolved" && (
        <div className="reopen-bar">
          <p>Not solved after all? Reopen the ticket and our support team will take it from here.</p>
          <button type="button" onClick={reopen} disabled={reopening}>
            {reopening ? "Reopening…" : "Reopen ticket"}
          </button>
        </div>
      )}

      {ticket.status === "closed" ? (
        <p className="chat-closed-note">This ticket is closed. Raise a new one if you need more help.</p>
      ) : (
        <ReplyComposer
          ticketId={ticket.ticket_id}
          onSent={() => {
            load();
            onChanged?.();
          }}
        />
      )}
    </section>
  );
}
