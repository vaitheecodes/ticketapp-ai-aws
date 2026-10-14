/* The conversation panel, shared by the customer's Raise Ticket view (live)
 * and Past Tickets (read-only transcript).
 *
 * Panel height: the scroll area sizes to its content up to min(68vh, 44rem)
 * instead of a fixed 360px, so a single multi-paragraph answer is readable
 * in one view and only a genuinely long thread scrolls. When it does
 * scroll, it still lands on the newest message.
 *
 * The backend's four reply shapes all land here (backend/main.py):
 *   ai / clarify  -> another AI turn, chat stays open
 *   closed        -> bare "thanks" closed the ticket, chat locks
 *   human         -> escalated; may carry a final AI message explaining why
 *                    (e.g. it admitted it can't access billing), which is
 *                    shown before the escalation notice
 */

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { api } from "../api";
import Button from "./ui/Button";
import ChatMessage from "./ui/ChatMessage";

const ESCALATED_NOTE = "This conversation has been escalated to a human agent.";

export default function TicketChat({ ticketId, initialMessages, readOnly = false }) {
  const [messages, setMessages] = useState(initialMessages);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [lockedReason, setLockedReason] = useState(null);
  const scrollRef = useRef(null);
  const inputRef = useRef(null);

  // Open on the newest message rather than the top of the history.
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [messages]);

  async function handleSend(e) {
    e.preventDefault();
    const text = input.trim();
    if (!text || sending) return;

    setSending(true);
    setError("");
    setMessages((prev) => [...prev, { sender: "customer", message: text }]);
    setInput("");

    try {
      const res = await api.ticketChat(ticketId, text);
      const incoming = [];

      if (res.message && res.route !== "closed") {
        incoming.push({ sender: "ai", message: res.message });
      }
      if (res.route === "closed") {
        incoming.push({ sender: "system", message: res.message });
        setLockedReason("closed");
      } else if (res.route === "human") {
        incoming.push({ sender: "system", message: ESCALATED_NOTE });
        setLockedReason("escalated");
      }

      setMessages((prev) => [...prev, ...incoming]);
    } catch (err) {
      setError(err.message);
      inputRef.current?.focus();
    } finally {
      setSending(false);
    }
  }

  const locked = readOnly || lockedReason !== null;

  return (
    <div className="chat">
      <div
        className={`chat-scroll ${readOnly ? "is-readonly" : ""}`}
        ref={scrollRef}
        role="log"
        aria-live="polite"
        aria-label="Conversation"
      >
        {messages.map((m, i) => (
          <ChatMessage
            key={i}
            sender={m.sender}
            message={m.message}
            createdAt={m.created_at}
          />
        ))}
      </div>

      {!locked && (
        <form className="chat-composer" onSubmit={handleSend}>
          <div className="field">
            <label className="sr-only" htmlFor={`chat-input-${ticketId}`}>
              Message
            </label>
            <input
              id={`chat-input-${ticketId}`}
              ref={inputRef}
              className="control"
              placeholder="Ask a follow-up…"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={sending}
              autoComplete="off"
            />
          </div>
          <Button type="submit" variant="primary" loading={sending} disabled={!input.trim()}>
            Send
          </Button>
        </form>
      )}

      {lockedReason === "closed" && (
        <p className="chat-closed-note">
          This ticket is closed. Raise a new one if you need more help.
        </p>
      )}
      {lockedReason === "escalated" && (
        <p className="chat-closed-note">
          A support agent is picking this up. They have the full conversation.
        </p>
      )}

      {error && (
        <p className="chat-closed-note" role="alert" style={{ color: "var(--status-danger)" }}>
          {error}
        </p>
      )}
    </div>
  );
}
