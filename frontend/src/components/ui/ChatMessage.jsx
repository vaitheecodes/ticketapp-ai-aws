/* A chat turn.
 *
 * Three senders with genuinely different jobs, so they get genuinely
 * different shapes rather than the same rounded rectangle in three colors:
 *
 *   customer  right-aligned, accent fill — the person's own words
 *   ai        left-aligned on the raised surface, labelled, markdown-rendered
 *             so numbered steps render as steps instead of "1." typed inline
 *   system    full-width centered rail — not a speaker, a state change
 *             ("escalated to a human", "ticket marked resolved")
 */

import { Markdown } from "../../utils/markdown";
import { relativeTime, absoluteTime } from "../../utils/time";

function Avatar({ sender }) {
  if (sender === "customer") return null;
  return (
    <span className="msg-avatar" aria-hidden="true">
      <svg viewBox="0 0 16 16" width="13" height="13">
        <path
          d="M8 1.5 13.5 4v4.2c0 3-2.3 5.4-5.5 6.3-3.2-.9-5.5-3.3-5.5-6.3V4L8 1.5Z"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.3"
          strokeLinejoin="round"
        />
        <path
          d="M5.6 8.1 7.3 9.8l3.1-3.3"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </span>
  );
}

export default function ChatMessage({ sender, message, createdAt }) {
  if (sender === "system") {
    return (
      <div className="msg-system">
        <span className="msg-system-rule" aria-hidden="true" />
        <span className="msg-system-text">{message}</span>
        <span className="msg-system-rule" aria-hidden="true" />
      </div>
    );
  }

  const isCustomer = sender === "customer";
  const stamp = createdAt ? relativeTime(createdAt) : "";

  return (
    <div className={`msg msg-${isCustomer ? "customer" : "ai"}`}>
      {!isCustomer && (
        <div className="msg-meta">
          <Avatar sender={sender} />
          <span className="msg-author">Site24x7 AI</span>
          {stamp && (
            <time className="msg-time" title={absoluteTime(createdAt)}>
              {stamp}
            </time>
          )}
        </div>
      )}
      <div className="msg-body">
        {isCustomer ? <p className="msg-plain">{message}</p> : <Markdown>{message}</Markdown>}
      </div>
      {isCustomer && stamp && (
        <time className="msg-time msg-time-right" title={absoluteTime(createdAt)}>
          {stamp}
        </time>
      )}
    </div>
  );
}
