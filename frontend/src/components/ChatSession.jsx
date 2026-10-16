/* A customer's AI support chat (backend/chats.py).
 *
 * The chat has its own lifecycle, separate from tickets:
 *   active     the customer and the AI are talking
 *   resolved   the customer confirmed it's solved — read-only from here
 *   escalated  handed to the support team as a ticket — read-only, with a
 *              link to that ticket
 *
 * Every response from the backend is the whole session, so the view just
 * re-renders from server state. If the AI is unavailable the backend keeps
 * the customer's message and says so; the customer can retry or raise a
 * ticket (which never depends on the AI).
 */

import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { api } from "../api";
import { ChatStatusChip } from "./ui/StatusChip";
import Button from "./ui/Button";
import ChatMessage from "./ui/ChatMessage";

function RaiseTicketConfirm({ issue, onConfirm, onCancel, busy }) {
  const [subject, setSubject] = useState(issue.slice(0, 200));
  const id = useId();
  return (
    <form
      className="chat-escalate"
      onSubmit={(e) => {
        e.preventDefault();
        onConfirm(subject.trim());
      }}
    >
      <p className="chat-escalate-text">
        We'll create a ticket for our support team with this whole conversation, so you won't need to
        repeat anything.
      </p>
      <div className="field-group">
        <label className="field-label" htmlFor={id}>
          Ticket subject
        </label>
        <input id={id} maxLength={200} value={subject} onChange={(e) => setSubject(e.target.value)} />
      </div>
      <div className="chat-escalate-actions">
        <button type="button" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button type="submit" className="btn-primary" disabled={busy}>
          {busy ? "Creating ticket…" : "Create ticket"}
        </button>
      </div>
    </form>
  );
}

export default function ChatSession({ initialSession, sessionId, onChanged, onOpenTicket }) {
  const [session, setSession] = useState(initialSession || null);
  const [loadError, setLoadError] = useState("");
  const [input, setInput] = useState("");
  const [pendingText, setPendingText] = useState(null);
  const [busy, setBusy] = useState(null); // null | "send" | "resolve" | "escalate"
  const [error, setError] = useState(initialSession?.ai_error || "");
  const [confirmingTicket, setConfirmingTicket] = useState(false);
  const scrollRef = useRef(null);
  const inputId = useId();

  useEffect(() => {
    if (initialSession) return;
    api
      .chatGet(sessionId)
      .then(setSession)
      .catch((err) => setLoadError(err.message));
  }, [sessionId, initialSession]);

  // Open on the newest message, and follow new ones.
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [session, pendingText]);

  async function act(kind, call) {
    setBusy(kind);
    setError("");
    try {
      const next = await call();
      setSession(next);
      if (next.ai_error) setError(next.ai_error);
      onChanged?.(next);
      return next;
    } catch (err) {
      setError(err.message);
      return null;
    } finally {
      setBusy(null);
    }
  }

  async function handleSend(e) {
    e.preventDefault();
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setPendingText(text);
    const next = await act("send", () => api.chatSend(session.session_id, text));
    setPendingText(null);
    if (!next) setInput(text);
  }

  if (loadError) return <p className="error">{loadError}</p>;
  if (!session) return <p className="hint">Loading chat…</p>;

  const active = session.status === "active";
  const hasAnswer = session.messages.some((m) => m.role === "ai");
  const messages = pendingText
    ? [...session.messages, { message_id: "pending", role: "customer", content: pendingText }]
    : session.messages;

  return (
    <section className="card live-chat" aria-labelledby={`chat-title-${session.session_id}`}>
      <header className="live-chat-head">
        <div className="live-chat-title">
          <h2 className="workspace-title" id={`chat-title-${session.session_id}`}>
            Chat
          </h2>
          <ChatStatusChip status={session.status} />
        </div>
        <span className="live-chat-issue">{session.issue}</span>
        {active && !confirmingTicket && (
          <div className="live-chat-actions">
            <button
              type="button"
              onClick={() => act("resolve", () => api.chatResolve(session.session_id))}
              disabled={!hasAnswer || busy !== null}
              title={hasAnswer ? undefined : "Available once the assistant has answered"}
            >
              {busy === "resolve" ? "Closing…" : "My issue is solved"}
            </button>
            <button type="button" onClick={() => setConfirmingTicket(true)} disabled={busy !== null}>
              Raise a ticket
            </button>
          </div>
        )}
      </header>

      {active && confirmingTicket && (
        <RaiseTicketConfirm
          issue={session.issue}
          busy={busy === "escalate"}
          onCancel={() => setConfirmingTicket(false)}
          onConfirm={async (subject) => {
            const next = await act("escalate", () => api.chatEscalate(session.session_id, subject));
            if (next) setConfirmingTicket(false);
          }}
        />
      )}

      <div className="chat">
        <div
          className={`chat-scroll ${active ? "" : "is-readonly"}`}
          ref={scrollRef}
          role="log"
          aria-live="polite"
          aria-label="Conversation"
        >
          {messages.map((m) => (
            <ChatMessage key={m.message_id} sender={m.role} message={m.content} createdAt={m.created_at} />
          ))}
          {busy === "send" && (
            <p className="chat-typing" role="status">
              Assistant is replying…
            </p>
          )}
        </div>

        {error && (
          <div className="chat-error" role="alert">
            <p>{error}</p>
            {active && !confirmingTicket && (
              <button type="button" onClick={() => setConfirmingTicket(true)}>
                Raise a ticket instead
              </button>
            )}
          </div>
        )}

        {active && (
          <form className="chat-composer" onSubmit={handleSend}>
            <div className="field">
              <label className="sr-only" htmlFor={inputId}>
                Message
              </label>
              <input
                id={inputId}
                className="control"
                placeholder={hasAnswer ? "Ask a follow-up…" : "Add more detail…"}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                disabled={busy !== null}
                autoComplete="off"
                maxLength={4000}
              />
            </div>
            <Button type="submit" variant="primary" loading={busy === "send"} disabled={!input.trim()}>
              Send
            </Button>
          </form>
        )}

        {session.status === "resolved" && (
          <p className="chat-closed-note">
            This chat is resolved and closed. Start a new chat any time.
          </p>
        )}
        {session.status === "escalated" && (
          <div className="chat-escalated-note">
            <p>
              Escalated to our support team as ticket{" "}
              <span className="mono">{session.ticket_id}</span>. They'll reply in the ticket.
            </p>
            {onOpenTicket && (
              <button type="button" onClick={() => onOpenTicket(session.ticket_id)}>
                Open ticket
              </button>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
