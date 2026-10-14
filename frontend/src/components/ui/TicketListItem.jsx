/* One ticket row, used by the customer's Past Tickets, the agent Queue and
 * My Tickets. The admin table has its own denser treatment.
 *
 * The thing this fixes: the queue used to print raw concatenated
 * ticket_text clipped by CSS ellipsis, so an escalated ticket read as
 * "...The customer reports h". A row now carries real structure — status,
 * mono ticket id, timestamp, the customer's own words, and (when a chat
 * escalated) the AI case summary as its own labelled block, because those
 * are two different kinds of fact and an agent scans them differently.
 *
 * `showClassification` is opt-in on purpose: agents only ever see
 * ticket_id + ticket_text (see CLAUDE.md), so category/confidence can't
 * leak into the queue by default.
 */

import { ChevronRightIcon } from "../Icons";
import { parseTicketText } from "../../utils/parseTicketText";
import { excerpt } from "../../utils/text";
import { relativeTime, absoluteTime } from "../../utils/time";
import { StatusChip, RouteChip, ConfidenceChip } from "./StatusChip";

export default function TicketListItem({
  ticket,
  expanded = false,
  onToggle,
  actions,
  showClassification = false,
  children,
}) {
  const { original, summary } = parseTicketText(ticket.ticket_text || "");
  const detailId = `ticket-detail-${ticket.ticket_id}`;
  const stamp = ticket.created_at ? relativeTime(ticket.created_at) : "";

  return (
    <li className={`row ${expanded ? "is-expanded" : ""}`}>
      <div className="row-main">
        <button
          type="button"
          className="row-trigger"
          aria-expanded={expanded}
          aria-controls={detailId}
          onClick={onToggle}
        >
          <ChevronRightIcon
            className={`row-chevron ${expanded ? "is-open" : ""}`}
            aria-hidden="true"
          />

          <span className="row-content">
            <span className="row-head">
              <StatusChip status={ticket.status} />
              {showClassification && <RouteChip route={ticket.route} />}
              {showClassification && ticket.category && (
                <span className="row-category">{ticket.category}</span>
              )}
              {showClassification && <ConfidenceChip confidence={ticket.confidence} />}
              <span className="row-id mono">{ticket.ticket_id}</span>
              {stamp && (
                <time className="row-time" title={absoluteTime(ticket.created_at)}>
                  {stamp}
                </time>
              )}
            </span>

            <span className="row-excerpt">{excerpt(original, 150)}</span>

            {summary && (
              <span className="row-summary">
                <span className="row-summary-label">AI case summary</span>
                <span className="row-summary-text">{excerpt(summary, 190)}</span>
              </span>
            )}
          </span>
        </button>

        {actions && <div className="row-actions">{actions}</div>}
      </div>

      {expanded && (
        <div className="row-detail" id={detailId}>
          {children}
        </div>
      )}
    </li>
  );
}
