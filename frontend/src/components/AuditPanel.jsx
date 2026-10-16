/* Admin: system-wide activity and audit log (backend/audit.py). Ticket
 * status/owner changes and claims, incident reviews, account, department
 * and settings changes, and failed sign-ins — filterable. Details are ids,
 * statuses and field names, never message bodies or passwords. */

import { useCallback, useEffect, useId, useState } from "react";
import { api } from "../api";
import { absoluteTime, relativeTime } from "../utils/time";

const ENTITIES = [
  ["", "Everything"],
  ["ticket", "Tickets"],
  ["incident", "Incidents"],
  ["staff", "Team"],
  ["customer", "Customers"],
  ["department", "Departments"],
  ["settings", "Settings"],
  ["account", "Sign-ins & passwords"],
  ["chat_review", "Chat reviews"],
];
const PAGE = 50;

function describe(detail) {
  return Object.entries(detail)
    .filter(([, v]) => v !== null && v !== undefined && !(Array.isArray(v) && v.length === 0))
    .map(([k, v]) => `${k.replace(/_/g, " ")}: ${Array.isArray(v) ? v.join(", ") : v}`)
    .join(" · ");
}

export default function AuditPanel() {
  const [entity, setEntity] = useState("ticket");
  const [action, setAction] = useState("");
  const [entityId, setEntityId] = useState("");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const entityFieldId = useId();
  const actionFieldId = useId();
  const idFieldId = useId();

  const load = useCallback(() => {
    api
      .adminAudit({ entity, action: action.trim(), entity_id: entityId.trim(), limit: PAGE, offset })
      .then((res) => {
        setData(res);
        setError("");
      })
      .catch((err) => setError(err.message));
  }, [entity, action, entityId, offset]);

  useEffect(() => {
    const timer = setTimeout(load, 250);
    return () => clearTimeout(timer);
  }, [load]);

  function filter(setter) {
    return (e) => {
      setOffset(0);
      setter(e.target.value);
    };
  }

  return (
    <section aria-labelledby="audit-heading">
      <h2 id="audit-heading" className="section-title">
        Activity &amp; audit log
      </h2>
      <div className="field-row audit-filters">
        <div className="field-group">
          <label className="field-label" htmlFor={entityFieldId}>
            Show
          </label>
          <select id={entityFieldId} value={entity} onChange={filter(setEntity)}>
            {ENTITIES.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <div className="field-group">
          <label className="field-label" htmlFor={actionFieldId}>
            Action starts with
          </label>
          <input id={actionFieldId} placeholder="e.g. ticket.status" value={action} onChange={filter(setAction)} />
        </div>
        <div className="field-group">
          <label className="field-label" htmlFor={idFieldId}>
            Ticket / record ID
          </label>
          <input id={idFieldId} placeholder="e.g. TCK-1a2b3c4d" value={entityId} onChange={filter(setEntityId)} />
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      {!data && !error && <p className="hint">Loading…</p>}
      {data && data.events.length === 0 && <p className="empty-state">No matching activity.</p>}
      {data && data.events.length > 0 && (
        <>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Who</th>
                  <th>Action</th>
                  <th>Record</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody>
                {data.events.map((e) => (
                  <tr key={e.id}>
                    <td>
                      <time dateTime={e.at} title={absoluteTime(e.at)}>
                        {relativeTime(e.at)}
                      </time>
                    </td>
                    <td>{e.actor_name}</td>
                    <td className="mono">{e.action}</td>
                    <td className="mono">{e.entity_id || "—"}</td>
                    <td className="wrap">{describe(e.detail) || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="pager">
            <span className="hint">
              {offset + 1}–{offset + data.events.length} of {data.total}
            </span>
            <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
              Newer
            </button>
            <button type="button" disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)}>
              Older
            </button>
          </div>
        </>
      )}
    </section>
  );
}
