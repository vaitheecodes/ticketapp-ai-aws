import { parseTicketText } from "../utils/parseTicketText";

export default function TicketTextDetail({ ticketText, id }) {
  const { original, summary } = parseTicketText(ticketText);
  return (
    <dl className="ticket-detail" id={id}>
      <dt>Customer's original message</dt>
      <dd>{original}</dd>
      {summary && (
        <>
          <dt>AI summary (escalated mid-chat)</dt>
          <dd>{summary}</dd>
        </>
      )}
    </dl>
  );
}
