/* One row in a staff ticket list (technician workspace, manager
 * dashboard): ticket #, age, subject, customer · owner, status, priority. */

import { absoluteTime, relativeTime } from "../utils/time";
import { StatusChip } from "./ui/StatusChip";

export default function StaffTicketRow({ t, selected, onSelect }) {
  return (
    <button
      type="button"
      className={`my-ticket ${selected ? "is-selected" : ""}`}
      aria-current={selected ? "true" : undefined}
      onClick={() => onSelect(t.ticket_id)}
    >
      <span className="my-ticket-top">
        <span className="my-ticket-id">{t.ticket_id}</span>
        {t.ticket_type === "chat" && <span className="type-badge">Chat</span>}
        <time className="my-ticket-time" dateTime={t.created_at} title={`Opened ${absoluteTime(t.created_at)}`}>
          {relativeTime(t.created_at)}
        </time>
      </span>
      <span className="my-ticket-subject">{t.subject}</span>
      <span className="my-ticket-customer">
        {t.customer_name}
        {t.assignee_name ? ` · ${t.assignee_name}` : ""}
      </span>
      <span className="my-ticket-bottom">
        <StatusChip status={t.status} />
        <span className={`priority-chip priority-${t.priority}`}>{t.priority}</span>
      </span>
    </button>
  );
}
