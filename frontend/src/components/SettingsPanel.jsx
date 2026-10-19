/* Admin: application settings (backend/app_settings.py). Each section is
 * saved as a whole and validated by the backend; "Reset" returns it to the
 * environment/default values. Changes apply to the next message,
 * correlation run or page load. */

import { useEffect, useId, useState } from "react";
import { api } from "../api";
import { categoryLabel } from "../utils/categories";
import { setStatusLabels } from "../utils/statusLabels";

const TAG_LABELS = {
  offensive_language: "Offensive language",
  negation: "Negation (\"not\", \"no\", \"still broken\")",
  coordinated_syntax: "Long compound messages",
};

function NumberField({ label, hint, value, onChange, step = 0.05, min = 0, max = 1 }) {
  const id = useId();
  return (
    <div className="field-group">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      <input id={id} type="number" step={step} min={min} max={max} value={value}
        onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))} required />
      {hint && <p className="hint" style={{ margin: 0 }}>{hint}</p>}
    </div>
  );
}

function Section({ title, description, section, data, onSaved, children }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function save(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      onSaved(await api.adminSaveSettings(section, data));
      setNotice("Saved.");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function reset() {
    setBusy(true);
    setError("");
    try {
      onSaved(await api.adminResetSettings(section));
      setNotice("Reset to defaults.");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card settings-section" onSubmit={save}>
      <h2 className="section-title">{title}</h2>
      <p className="hint">{description}</p>
      {children}
      <div className="row-actions">
        <button type="submit" className="btn-primary" disabled={busy}>
          {busy ? "Saving…" : "Save"}
        </button>
        <button type="button" onClick={reset} disabled={busy}>
          Reset to defaults
        </button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="success-text" role="status">
          {notice}
        </p>
      )}
    </form>
  );
}

export default function SettingsPanel() {
  const [payload, setPayload] = useState(null);
  const [routing, setRouting] = useState(null);
  const [incidents, setIncidents] = useState(null);
  const [statuses, setStatuses] = useState(null);
  const [error, setError] = useState("");

  function apply(next) {
    setPayload(next);
    setRouting(next.sections.routing);
    setIncidents(next.sections.incidents);
    setStatuses(next.sections.statuses);
    setStatusLabels(next.sections.statuses);
  }

  useEffect(() => {
    api.adminSettings().then(apply).catch((err) => setError(err.message));
  }, []);

  if (error) return <p className="error">{error}</p>;
  if (!payload) return <p className="hint">Loading settings…</p>;

  function toggle(list, value) {
    return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
  }

  return (
    <div className="settings-grid">
      <Section
        title="Escalation conditions"
        description="How the AI decides between answering, asking a clarifying question and handing a request to a person."
        section="routing"
        data={routing}
        onSaved={apply}
      >
        <div className="field-row">
          <NumberField label="Confidence needed to answer" value={routing.confidence_threshold}
            onChange={(v) => setRouting({ ...routing, confidence_threshold: v })} />
          <NumberField label="Confidence needed for high-risk categories"
            value={routing.high_risk_confidence_threshold}
            onChange={(v) => setRouting({ ...routing, high_risk_confidence_threshold: v })} />
          <NumberField label="Clarifying questions before a hand-off" step={1} min={0} max={10}
            value={routing.max_clarification_attempts}
            onChange={(v) => setRouting({ ...routing, max_clarification_attempts: v })} />
        </div>
        <fieldset className="checkbox-grid">
          <legend className="field-label">High-risk categories (escalate below the high-risk bar, no clarifying)</legend>
          {payload.categories.map((c) => (
            <label key={c} className="checkbox-label">
              <input type="checkbox" checked={routing.high_risk_categories.includes(c)}
                onChange={() => setRouting({ ...routing, high_risk_categories: toggle(routing.high_risk_categories, c) })} />
              {categoryLabel(c)}
            </label>
          ))}
        </fieldset>
        <fieldset className="checkbox-grid">
          <legend className="field-label">Language that always goes to a person</legend>
          {payload.language_tags.map((t) => (
            <label key={t} className="checkbox-label">
              <input type="checkbox" checked={routing.escalation_language_tags.includes(t)}
                onChange={() =>
                  setRouting({ ...routing, escalation_language_tags: toggle(routing.escalation_language_tags, t) })
                } />
              {TAG_LABELS[t] || t}
            </label>
          ))}
        </fieldset>
      </Section>

      <Section
        title="Incident correlation"
        description="Which reports are considered together before the AI judges whether they're one incident."
        section="incidents"
        data={incidents}
        onSaved={apply}
      >
        <div className="field-row">
          <NumberField label="Time window (hours)" step={1} min={1} max={336} value={incidents.window_hours}
            onChange={(v) => setIncidents({ ...incidents, window_hours: v })} />
          <NumberField label="Minimum reports before asking the AI" step={1} min={2} max={40}
            value={incidents.min_tickets} onChange={(v) => setIncidents({ ...incidents, min_tickets: v })} />
          <NumberField label="Text similarity (with embeddings)" value={incidents.similarity}
            onChange={(v) => setIncidents({ ...incidents, similarity: v })} />
          <NumberField label="AI confidence needed to route to a department"
            value={incidents.routing_confidence}
            onChange={(v) => setIncidents({ ...incidents, routing_confidence: v })} />
          <NumberField label="Remember incidents for (days)" step={1} min={1} max={3650}
            value={incidents.memory_days} onChange={(v) => setIncidents({ ...incidents, memory_days: v })} />
        </div>
      </Section>

      <Section
        title="Ticket status labels"
        description="What customers and staff see for each status. The five statuses themselves are fixed — the workflow depends on each one."
        section="statuses"
        data={statuses}
        onSaved={apply}
      >
        <div className="field-row status-labels">
          {Object.keys(statuses).map((key) => (
            <StatusLabelField key={key} statusKey={key} value={statuses[key]}
              onChange={(v) => setStatuses({ ...statuses, [key]: v })} />
          ))}
        </div>
      </Section>
    </div>
  );
}

function StatusLabelField({ statusKey, value, onChange }) {
  const id = useId();
  return (
    <div className="field-group">
      <label className="field-label" htmlFor={id}>
        <span className="mono">{statusKey}</span>
      </label>
      <input id={id} maxLength={40} value={value} onChange={(e) => onChange(e.target.value)} required />
    </div>
  );
}
