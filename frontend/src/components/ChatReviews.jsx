/* Internal review of AI-resolved chats (technicians and admin).
 *
 * Every chat the customer confirmed as solved gets a review record with the
 * AI's summary (problem / troubleshooting / solution). Staff read it next
 * to the transcript and mark it verified, then closed — separately from
 * the customer's chat, which closed when they confirmed. A summary marked
 * "fallback" was built without the AI (it was unavailable), so the
 * transcript is the source of truth for that one.
 */

import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { absoluteTime, relativeTime } from "../utils/time";
import ChatMessage from "./ui/ChatMessage";

const FILTERS = [
  ["pending_review", "Pending review"],
  ["verified", "Verified"],
  ["closed", "Closed"],
];

function parseSummary(raw) {
  try {
    return JSON.parse(raw) || {};
  } catch {
    return {};
  }
}

function ReviewCard({ review, onUpdated }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const summary = parseSummary(review.summary);

  async function update(status) {
    setBusy(true);
    setError("");
    try {
      await api.chatReviewUpdate(review.review_id, status);
      onUpdated();
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <li className="card review-card">
      <header className="review-head">
        <div>
          <p className="review-issue">{review.issue}</p>
          <p className="hint">
            {review.customer_name} · resolved{" "}
            <time dateTime={review.resolved_at} title={absoluteTime(review.resolved_at)}>
              {relativeTime(review.resolved_at)}
            </time>{" "}
            · <span className="mono">{review.chat_id}</span>
          </p>
        </div>
        <div className="row-actions">
          {review.status === "pending_review" && (
            <button type="button" className="btn-primary" onClick={() => update("verified")} disabled={busy}>
              Mark verified
            </button>
          )}
          {review.status !== "closed" && (
            <button type="button" onClick={() => update("closed")} disabled={busy}>
              Close review
            </button>
          )}
        </div>
      </header>

      <dl className="review-summary">
        {summary.source === "fallback" && (
          <p className="pending-text">AI summary unavailable — read the transcript.</p>
        )}
        {summary.note && <p className="hint">{summary.note}</p>}
        <dt>Problem</dt>
        <dd>{summary.problem || "—"}</dd>
        <dt>Troubleshooting</dt>
        <dd>
          {summary.troubleshooting?.length ? (
            <ul>
              {summary.troubleshooting.map((step, i) => (
                <li key={i}>{step}</li>
              ))}
            </ul>
          ) : (
            "—"
          )}
        </dd>
        <dt>Solution</dt>
        <dd>{summary.solution || "—"}</dd>
      </dl>

      <details className="chat-transcript">
        <summary>Transcript ({review.transcript.length} messages)</summary>
        <div className="chat-scroll is-readonly">
          {review.transcript.map((m) => (
            <ChatMessage key={m.message_id} sender={m.role} message={m.content} createdAt={m.created_at} />
          ))}
        </div>
      </details>
      {error && <p className="error">{error}</p>}
    </li>
  );
}

export default function ChatReviews() {
  const [status, setStatus] = useState("pending_review");
  const [reviews, setReviews] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api
      .chatReviews(status)
      .then((rows) => {
        setReviews(rows);
        setError("");
      })
      .catch((err) => setError(err.message));
  }, [status]);

  useEffect(load, [load]);

  return (
    <div className="reviews">
      <div className="filter-tabs reviews-filter" role="group" aria-label="Filter reviews">
        {FILTERS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            className={status === key ? "active" : ""}
            aria-pressed={status === key}
            onClick={() => {
              setReviews(null);
              setStatus(key);
            }}
          >
            {label}
          </button>
        ))}
      </div>
      {error && <p className="error">{error}</p>}
      {!error && !reviews && <p className="hint">Loading reviews…</p>}
      {reviews && reviews.length === 0 && (
        <p className="empty-state">Nothing here. AI-resolved chats appear for review as customers close them.</p>
      )}
      {reviews && reviews.length > 0 && (
        <ul className="review-list">
          {reviews.map((r) => (
            <ReviewCard key={r.review_id} review={r} onUpdated={load} />
          ))}
        </ul>
      )}
    </div>
  );
}
