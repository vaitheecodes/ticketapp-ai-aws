/* Customer support portal, help-desk style.
 *
 *   ┌──────────────────────────────┬──────────────────┐
 *   │ [Start a chat] [Raise ticket]│  My tickets      │
 *   │                              │  filters, search │
 *   │  workspace: pre-chat form →  │  rows…           │
 *   │  live chat, ticket form, or  │                  │
 *   │  the selected ticket thread  │                  │
 *   └──────────────────────────────┴──────────────────┘
 *
 * Stacks to one column on narrow screens (workspace first, then the list).
 * A chat only shows up in My tickets once it has been handed to the
 * support team; chats the AI resolves aren't tickets.
 */

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { api } from "../api";
import AttachmentPicker from "../components/AttachmentPicker";
import PortalHeader from "../components/PortalHeader";
import TicketChat from "../components/TicketChat";
import TicketThread from "../components/TicketThread";
import Toast from "../components/Toast";
import { StatusChip } from "../components/ui/StatusChip";
import { categoryLabel } from "../utils/categories";
import { absoluteTime, relativeTime } from "../utils/time";

const PRIORITIES = ["low", "medium", "high", "urgent"];

const FILTERS = [
  { key: "all", label: "All", match: () => true },
  {
    key: "open",
    label: "Open",
    match: (t) => ["open", "in_progress", "pending_customer"].includes(t.status),
  },
  { key: "resolved", label: "Resolved", match: (t) => ["resolved", "closed"].includes(t.status) },
  {
    key: "escalated",
    label: "Escalated",
    match: (t) => t.escalated && !["resolved", "closed"].includes(t.status),
  },
];

function ChatIcon() {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" className="request-card-icon">
      <path
        d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-4.5 4v-4H6.5A2.5 2.5 0 0 1 4 13.5v-8Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
      <path d="M8.5 9.5h7M8.5 12.5h4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

function EnvelopeIcon() {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" className="request-card-icon">
      <rect x="3" y="5" width="18" height="14" rx="2.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="m4 7 8 6 8-6" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}

function CategorySelect({ id, value, onChange, categories }) {
  return (
    <select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">Not sure</option>
      {categories.map((c) => (
        <option key={c} value={c}>
          {categoryLabel(c)}
        </option>
      ))}
    </select>
  );
}

// ---------------------------------------------------------------------------
// Start a chat
// ---------------------------------------------------------------------------
function PreChatForm({ defaultName, categories, onStarted }) {
  // null until the customer edits it, so the name from /auth/me (which
  // may arrive after first render) prefills without an effect.
  const [nameEdit, setName] = useState(null);
  const name = nameEdit ?? defaultName;
  const [category, setCategory] = useState("");
  const [issue, setIssue] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const nameId = useId();
  const categoryId = useId();
  const issueId = useId();

  async function handleSubmit(e) {
    e.preventDefault();
    const text = issue.trim();
    if (!text) return;
    setSubmitting(true);
    setError("");
    try {
      const res = await api.ticketsNew(text, "chat", {
        subject: text,
        category,
        contact_name: name.trim(),
      });
      onStarted({ ...res, issue: text });
    } catch (err) {
      setError(err.message);
      setSubmitting(false);
    }
  }

  return (
    <form className="card request-form" onSubmit={handleSubmit}>
      <h2 className="workspace-title">Start a chat</h2>
      <p className="hint">Our AI assistant answers right away and brings in the support team if needed.</p>
      <div className="field-row">
        <div className="field-group" style={{ flex: 1 }}>
          <label className="field-label" htmlFor={nameId}>
            Your name
          </label>
          <input id={nameId} autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} required />
        </div>
        <div className="field-group" style={{ flex: 1 }}>
          <label className="field-label" htmlFor={categoryId}>
            Category
          </label>
          <CategorySelect id={categoryId} value={category} onChange={setCategory} categories={categories} />
        </div>
      </div>
      <div className="field-group">
        <label className="field-label" htmlFor={issueId}>
          What do you need help with?
        </label>
        <input
          id={issueId}
          maxLength={200}
          placeholder="e.g. My Linux server monitor stopped reporting data"
          value={issue}
          onChange={(e) => setIssue(e.target.value)}
          required
        />
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="form-actions">
        <button type="submit" className="btn-primary" disabled={submitting || !issue.trim()}>
          {submitting ? "Connecting…" : "Start chat"}
        </button>
      </div>
    </form>
  );
}

function LiveChat({ chat, onEscalated }) {
  const escalatedAtStart = chat.route === "human";
  return (
    <div className="card live-chat">
      <header className="live-chat-head">
        <h2 className="workspace-title">Chat</h2>
        <span className="live-chat-issue">{chat.issue}</span>
      </header>
      {escalatedAtStart ? (
        <div className="result-box">
          <p className="pending-text">This needs our support team.</p>
          <p>
            We've created ticket <span className="mono">{chat.ticket_id}</span> for you — it's in My
            tickets, and an agent will reply there.
          </p>
        </div>
      ) : (
        <TicketChat
          ticketId={chat.ticket_id}
          initialMessages={[
            { sender: "customer", message: chat.issue },
            { sender: "ai", message: chat.ai_response },
          ]}
          onRoute={(route) => route === "human" && onEscalated()}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Raise a ticket
// ---------------------------------------------------------------------------
function RaiseTicketForm({ categories, onCreated }) {
  const [subject, setSubject] = useState("");
  const [category, setCategory] = useState("");
  const [priority, setPriority] = useState("medium");
  const [description, setDescription] = useState("");
  const [files, setFiles] = useState([]);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const subjectId = useId();
  const categoryId = useId();
  const priorityId = useId();
  const descriptionId = useId();

  async function handleSubmit(e) {
    e.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const res = await api.ticketsCreate({
        subject: subject.trim(),
        category,
        priority,
        description: description.trim(),
      });
      let attachmentError = "";
      if (files.length) {
        try {
          await api.uploadAttachments(res.ticket_id, res.message_id, files);
        } catch (err) {
          attachmentError = err.message;
        }
      }
      onCreated(res.ticket_id, attachmentError);
    } catch (err) {
      setError(err.message);
      setSubmitting(false);
    }
  }

  return (
    <form className="card request-form" onSubmit={handleSubmit}>
      <h2 className="workspace-title">Raise a ticket</h2>
      <p className="hint">We'll reply in the ticket thread and by email.</p>
      <div className="field-group">
        <label className="field-label" htmlFor={subjectId}>
          Subject
        </label>
        <input
          id={subjectId}
          maxLength={200}
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          required
        />
      </div>
      <div className="field-row">
        <div className="field-group" style={{ flex: 1 }}>
          <label className="field-label" htmlFor={categoryId}>
            Category
          </label>
          <CategorySelect id={categoryId} value={category} onChange={setCategory} categories={categories} />
        </div>
        <div className="field-group" style={{ flex: 1 }}>
          <label className="field-label" htmlFor={priorityId}>
            Priority
          </label>
          <select id={priorityId} value={priority} onChange={(e) => setPriority(e.target.value)}>
            {PRIORITIES.map((p) => (
              <option key={p} value={p}>
                {p[0].toUpperCase() + p.slice(1)}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="field-group">
        <label className="field-label" htmlFor={descriptionId}>
          Description
        </label>
        <textarea
          id={descriptionId}
          rows={6}
          placeholder="What happened, which monitor or server, and what you've tried so far."
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          required
        />
      </div>
      <div className="field-group">
        <span className="field-label">Attachments</span>
        <AttachmentPicker files={files} onChange={setFiles} disabled={submitting} />
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="form-actions">
        <button
          type="submit"
          className="btn-primary"
          disabled={submitting || !subject.trim() || !description.trim()}
        >
          {submitting ? "Submitting…" : "Submit ticket"}
        </button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// My tickets
// ---------------------------------------------------------------------------
function TicketListSkeleton() {
  return (
    <ul className="my-tickets-list" aria-busy="true" aria-label="Loading tickets">
      {[0, 1, 2].map((i) => (
        <li key={i} className="my-ticket-skeleton">
          <div className="skeleton skeleton-line" style={{ width: "35%" }} />
          <div className="skeleton skeleton-line" style={{ width: "85%" }} />
        </li>
      ))}
    </ul>
  );
}

function MyTickets({ tickets, error, selectedId, onSelect }) {
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const searchId = useId();

  const visible = useMemo(() => {
    if (!tickets) return [];
    const { match } = FILTERS.find((f) => f.key === filter);
    const q = query.trim().toLowerCase();
    return tickets.filter(
      (t) =>
        match(t) &&
        (!q || t.subject.toLowerCase().includes(q) || t.ticket_id.toLowerCase().includes(q)),
    );
  }, [tickets, filter, query]);

  return (
    <aside className="my-tickets" aria-labelledby="my-tickets-heading">
      <h2 id="my-tickets-heading" className="my-tickets-title">
        My tickets
      </h2>
      <div className="filter-tabs" role="group" aria-label="Filter tickets">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            className={filter === f.key ? "active" : ""}
            aria-pressed={filter === f.key}
            onClick={() => setFilter(f.key)}
          >
            {f.label}
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
        placeholder="Search by subject or ticket #"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />

      {error && <p className="error">{error}</p>}
      {!error && !tickets && <TicketListSkeleton />}
      {tickets && tickets.length === 0 && (
        <div className="empty empty-compact">
          <p className="empty-title">No tickets yet</p>
          <p className="empty-body">Raise a ticket, or start a chat — we'll track it here.</p>
        </div>
      )}
      {tickets && tickets.length > 0 && visible.length === 0 && (
        <p className="empty-state">No tickets match.</p>
      )}
      {visible.length > 0 && (
        <ul className="my-tickets-list">
          {visible.map((t) => (
            <li key={t.ticket_id}>
              <button
                type="button"
                className={`my-ticket ${selectedId === t.ticket_id ? "is-selected" : ""}`}
                aria-current={selectedId === t.ticket_id ? "true" : undefined}
                onClick={() => onSelect(t.ticket_id)}
              >
                <span className="my-ticket-top">
                  <span className="my-ticket-id">
                    {t.unread && (
                      <>
                        <span className="unread-dot" aria-hidden="true" />
                        <span className="sr-only">New reply. </span>
                      </>
                    )}
                    {t.ticket_id}
                  </span>
                  {t.ticket_type === "chat" && <span className="type-badge">Chat</span>}
                  <time
                    className="my-ticket-time"
                    dateTime={t.last_updated}
                    title={`Last updated ${absoluteTime(t.last_updated)}`}
                  >
                    {relativeTime(t.last_updated)}
                  </time>
                </span>
                <span className={`my-ticket-subject ${t.unread ? "is-unread" : ""}`}>{t.subject}</span>
                <span className="my-ticket-bottom">
                  <StatusChip status={t.status} />
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
export default function CustomerPortal() {
  // null | "chat-form" | "ticket-form" | { chat } | { ticketId }
  const [view, setView] = useState(null);
  const [tickets, setTickets] = useState(null);
  const [listError, setListError] = useState("");
  const [categories, setCategories] = useState([]);
  const [name, setName] = useState("");
  const [toast, setToast] = useState(null);
  const workspaceRef = useRef(null);

  const refreshTickets = useCallback(() => {
    api
      .ticketsMine()
      .then((rows) => {
        setTickets(rows);
        setListError("");
      })
      .catch((err) => setListError(err.message));
  }, []);

  useEffect(() => {
    refreshTickets();
    api.categories().then(setCategories).catch(() => setCategories([]));
    api.me().then((me) => setName(me.name)).catch(() => {});
  }, [refreshTickets]);

  const dismissToast = useCallback(() => setToast(null), []);

  function show(next) {
    setView(next);
    // On a phone the workspace sits above a long list; bring it into view.
    requestAnimationFrame(() =>
      workspaceRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }),
    );
  }

  const selectedId = view && typeof view === "object" ? view.ticketId : null;

  return (
    <div className="page page-portal">
      <PortalHeader title="Support Portal" role="customer" />
      <div className="portal-layout">
        <div className="portal-main">
          <div className="request-cards" role="group" aria-label="New request">
            <button
              type="button"
              className={`request-card ${view === "chat-form" || view?.chat ? "is-active" : ""}`}
              aria-pressed={view === "chat-form" || Boolean(view?.chat)}
              onClick={() => show("chat-form")}
            >
              <ChatIcon />
              <span className="request-card-title">Start a chat</span>
              <span className="request-card-desc">Get an answer now from our AI assistant</span>
            </button>
            <button
              type="button"
              className={`request-card ${view === "ticket-form" ? "is-active" : ""}`}
              aria-pressed={view === "ticket-form"}
              onClick={() => show("ticket-form")}
            >
              <EnvelopeIcon />
              <span className="request-card-title">Raise a ticket</span>
              <span className="request-card-desc">Send details and files; we reply by email</span>
            </button>
          </div>

          <div className="workspace" ref={workspaceRef}>
            {view === null && (
              <div className="empty">
                <p className="empty-title">How can we help?</p>
                <p className="empty-body">
                  Start a chat for a quick answer, raise a ticket for anything that needs details or
                  files, or pick a ticket from My tickets to see its replies.
                </p>
              </div>
            )}
            {view === "chat-form" && (
              <PreChatForm
                defaultName={name}
                categories={categories}
                onStarted={(chat) => {
                  setView({ chat });
                  if (chat.route === "human") refreshTickets();
                }}
              />
            )}
            {view?.chat && (
              <LiveChat key={view.chat.ticket_id} chat={view.chat} onEscalated={refreshTickets} />
            )}
            {view === "ticket-form" && (
              <RaiseTicketForm
                categories={categories}
                onCreated={(ticketId, attachmentError) => {
                  setToast(
                    attachmentError
                      ? { tone: "error", message: `Ticket ${ticketId} created, but attachments failed: ${attachmentError}` }
                      : { tone: "ok", message: `Ticket ${ticketId} created.` },
                  );
                  refreshTickets();
                  show({ ticketId });
                }}
              />
            )}
            {selectedId && (
              <div className="card">
                <TicketThread key={selectedId} ticketId={selectedId} onChanged={refreshTickets} />
              </div>
            )}
          </div>
        </div>

        <MyTickets
          tickets={tickets}
          error={listError}
          selectedId={selectedId}
          onSelect={(ticketId) => show({ ticketId })}
        />
      </div>
      <Toast toast={toast} onDismiss={dismissToast} />
    </div>
  );
}
