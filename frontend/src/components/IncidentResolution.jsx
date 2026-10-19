/* Resolve a confirmed incident and send the post-incident summary
 * (backend/incident_resolution.py).
 *
 * 1. Staff enter the resolution note, when the fix was applied (IST) and
 *    an optional root cause; the AI drafts a customer summary and an
 *    internal one from the incident's facts and that note only.
 * 2. Staff edit the draft, check the preview, pick who may see it.
 * 3. "Approve & send" (with a confirm step) posts it on every linked
 *    ticket. Never automatic. Per-ticket delivery status is shown in the
 *    tickets table; failed ones can be retried.
 */

import { useId, useState } from "react";
import { api } from "../api";
import { istInputNow, istTime } from "../utils/time";

const VISIBILITY = {
  affected_only: "Affected customers only",
  public: "Public — anyone who asks, and the public incident history",
};

function ResolveForm({ incident, onSaved }) {
  const r = incident.resolution;
  const [note, setNote] = useState(r.resolution_note || "");
  const [fixedAt, setFixedAt] = useState(istInputNow());
  const [rootCause, setRootCause] = useState(r.root_cause || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const ids = { note: useId(), fixed: useId(), cause: useId() };

  async function draft(e) {
    e.preventDefault();
    if (!note.trim()) {
      setError("Describe what was done to fix it.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      onSaved(
        await api.incidentDraft(incident.incident_id, {
          resolution_note: note.trim(),
          fixed_at: fixedAt,
          root_cause: rootCause.trim(),
        }),
      );
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="resolve-form" onSubmit={draft}>
      <div className="field-group">
        <label className="field-label" htmlFor={ids.note}>
          Resolution note
        </label>
        <textarea
          id={ids.note}
          rows={3}
          maxLength={4000}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="What did the team do to fix it?"
        />
      </div>
      <div className="resolve-form-row">
        <div className="field-group">
          <label className="field-label" htmlFor={ids.fixed}>
            Fix applied at (IST)
          </label>
          <input
            id={ids.fixed}
            type="datetime-local"
            value={fixedAt}
            max={istInputNow()}
            onChange={(e) => setFixedAt(e.target.value)}
            required
          />
        </div>
        <div className="field-group">
          <label className="field-label" htmlFor={ids.cause}>
            Root cause <span className="hint">(optional)</span>
          </label>
          <input
            id={ids.cause}
            type="text"
            maxLength={2000}
            value={rootCause}
            onChange={(e) => setRootCause(e.target.value)}
            placeholder="Leave empty if it's still being reviewed"
          />
        </div>
      </div>
      <p className="hint">
        The AI drafts the summary from this incident&apos;s confirmed facts and your note only. Times and the cause
        line are filled in from what you enter here. Nothing is sent until you approve it.
      </p>
      <button type="submit" className="btn-primary" disabled={busy}>
        {busy ? "Drafting…" : r.customer_summary ? "Redraft summary" : "Draft summary"}
      </button>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}

function SummaryEditor({ incident, aiError, onSaved }) {
  const r = incident.resolution;
  const [customer, setCustomer] = useState(r.customer_summary || "");
  const [internal, setInternal] = useState(r.internal_summary || "");
  const [visibility, setVisibility] = useState(incident.visibility);
  const [markResolved, setMarkResolved] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState("");
  const ids = { customer: useId(), internal: useId(), vis: useId() };

  const dirty =
    customer.trim() !== (r.customer_summary || "").trim() ||
    internal.trim() !== (r.internal_summary || "").trim() ||
    visibility !== incident.visibility;

  async function save() {
    setBusy("save");
    setError("");
    try {
      onSaved(
        await api.incidentSaveSummary(incident.incident_id, {
          customer_summary: customer.trim(),
          internal_summary: internal.trim(),
          visibility: incident.can.visibility ? visibility : undefined,
        }),
      );
      return true;
    } catch (err) {
      setError(err.message);
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function send() {
    setBusy("send");
    setError("");
    try {
      onSaved(await api.incidentSend(incident.incident_id, customer.trim(), markResolved));
      setConfirming(false);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="summary-editor">
      {aiError && (
        <p className="error" role="alert">
          {aiError}
        </p>
      )}
      <div className="summary-columns">
        <div className="field-group">
          <label className="field-label" htmlFor={ids.customer}>
            Customer summary
          </label>
          <textarea
            id={ids.customer}
            rows={14}
            maxLength={6000}
            value={customer}
            onChange={(e) => {
              setCustomer(e.target.value);
              setConfirming(false);
            }}
          />
        </div>
        <div className="summary-preview" aria-label="Preview of the customer email">
          <p className="field-label">Preview</p>
          <div className="message-card incident-update-card">
            <p className="incident-update-from">From Site24x7 Support Team</p>
            <p className="incident-update-subject">Update on a service issue you reported</p>
            <div className="incident-update-body">{customer || "—"}</div>
          </div>
        </div>
      </div>
      <div className="field-group">
        <label className="field-label" htmlFor={ids.internal}>
          Internal summary <span className="hint">(staff only, never sent)</span>
        </label>
        <textarea
          id={ids.internal}
          rows={4}
          maxLength={6000}
          value={internal}
          onChange={(e) => setInternal(e.target.value)}
        />
      </div>
      {incident.can.visibility && (
        <div className="field-group">
          <label className="field-label" htmlFor={ids.vis}>
            Who can see this summary
          </label>
          <select id={ids.vis} value={visibility} onChange={(e) => setVisibility(e.target.value)}>
            {Object.entries(VISIBILITY).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
      )}
      <label className="checkbox-label">
        <input type="checkbox" checked={markResolved} onChange={(e) => setMarkResolved(e.target.checked)} />
        Also mark the linked tickets Resolved
      </label>
      <div className="row-actions">
        <button type="button" onClick={save} disabled={busy !== null || !customer.trim() || !dirty}>
          {busy === "save" ? "Saving…" : "Save draft"}
        </button>
        {!confirming ? (
          <button
            type="button"
            className="btn-primary"
            disabled={busy !== null || !customer.trim() || dirty}
            title={dirty ? "Save your changes first" : undefined}
            onClick={() => setConfirming(true)}
          >
            Approve &amp; send
          </button>
        ) : (
          <>
            <span className="hint" role="status">
              Send this to {incident.ticket_count} linked ticket{incident.ticket_count === 1 ? "" : "s"}
              {markResolved ? " and mark them Resolved" : ""}?
            </span>
            <button type="button" className="btn-primary" onClick={send} disabled={busy !== null}>
              {busy === "send" ? "Sending…" : "Yes, send"}
            </button>
            <button type="button" onClick={() => setConfirming(false)} disabled={busy !== null}>
              Cancel
            </button>
          </>
        )}
      </div>
      {dirty && <p className="hint">Save your changes before approving.</p>}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

function SentSummary({ incident, onSaved }) {
  const r = incident.resolution;
  const failed = incident.tickets.filter((t) => t.delivery?.status === "failed");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function retry() {
    setBusy(true);
    setError("");
    try {
      onSaved(await api.incidentSend(incident.incident_id, r.customer_summary, false));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function changeVisibility(value) {
    setError("");
    try {
      onSaved(await api.incidentVisibility(incident.incident_id, value));
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div className="summary-sent">
      <dl className="thread-facts">
        <div>
          <dt>Resolved</dt>
          <dd>{istTime(incident.resolved_at)}</dd>
        </div>
        <div>
          <dt>By</dt>
          <dd>{r.resolved_by || "—"}</dd>
        </div>
        <div>
          <dt>Root cause</dt>
          <dd>{r.root_cause || "Still being reviewed"}</dd>
        </div>
      </dl>
      <div className="message-card incident-update-card">
        <p className="incident-update-from">Sent by Site24x7 Support Team</p>
        <div className="incident-update-body">{r.customer_summary}</div>
      </div>
      {r.internal_summary && (
        <div className="incident-section">
          <h4>Internal summary</h4>
          <p className="pre-wrap">{r.internal_summary}</p>
        </div>
      )}
      {incident.can.visibility && (
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={incident.visibility === "public"}
            onChange={(e) => changeVisibility(e.target.checked ? "public" : "affected_only")}
          />
          Public (show on the incident history page and to anyone who asks)
        </label>
      )}
      {failed.length > 0 && (
        <div className="row-actions">
          <span className="error">
            {failed.length} ticket{failed.length === 1 ? "" : "s"} didn&apos;t get the update.
          </span>
          {incident.can.resolve && (
            <button type="button" onClick={retry} disabled={busy}>
              {busy ? "Retrying…" : "Retry failed"}
            </button>
          )}
        </div>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

export function DeliveryStatus({ delivery }) {
  if (!delivery) return <span className="hint">—</span>;
  if (delivery.status === "sent") return <span className="chip chip-ok">Sent</span>;
  return (
    <span className="chip chip-danger" title={delivery.error || undefined}>
      Failed
    </span>
  );
}

export default function IncidentResolution({ incident, onSaved, aiError }) {
  const resolved = incident.status === "resolved";
  if (!resolved && (incident.status !== "confirmed" || !incident.can.resolve)) {
    return incident.status === "confirmed" ? (
      <p className="hint">The assigned technician or a manager resolves this incident.</p>
    ) : null;
  }
  const hasDraft = Boolean(incident.resolution.resolution_note);
  return (
    <section className="incident-resolution" aria-labelledby={`resolve-${incident.incident_id}`}>
      <h3 className="section-title" id={`resolve-${incident.incident_id}`}>
        {resolved ? "Post-incident summary" : "Resolve & write summary"}
      </h3>
      {resolved ? (
        <SentSummary incident={incident} onSaved={onSaved} />
      ) : (
        <>
          <ResolveForm incident={incident} onSaved={onSaved} />
          {hasDraft && (
            <SummaryEditor
              key={`${incident.resolution.customer_summary}|${incident.updated_at}`}
              incident={incident}
              aiError={aiError}
              onSaved={onSaved}
            />
          )}
        </>
      )}
    </section>
  );
}
