/* Public incident history — no login. Only incidents staff made public
 * (backend: GET /api/public/incidents), with their approved summaries;
 * nothing about customers or tickets. Times in IST. */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { categoryLabel } from "../utils/categories";
import { istTime } from "../utils/time";
import { IncidentStatusChip } from "../components/ui/StatusChip";

export default function IncidentHistory() {
  const [items, setItems] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.publicIncidents().then(setItems).catch((err) => setError(err.message));
  }, []);

  return (
    <main className="page incident-history">
      <header>
        <h1 className="hero-title">Incident history</h1>
        <p className="hint">Service issues from the last few months and how they were fixed. Times are in IST.</p>
      </header>
      {error && <p className="error">{error}</p>}
      {!error && !items && <p className="hint">Loading…</p>}
      {items && items.length === 0 && <p className="empty-state">No public incidents to show.</p>}
      {items?.map((item) => (
        <article key={item.reference} className="card incident-history-item" aria-labelledby={`h-${item.reference}`}>
          <div className="incident-history-meta">
            <IncidentStatusChip status={item.status === "confirmed" ? "under_investigation" : item.status} />
            {item.category && <span>{categoryLabel(item.category)}</span>}
            <span>
              {istTime(item.started_at)}
              {item.resolved_at ? ` – ${istTime(item.resolved_at)}` : " – ongoing"}
            </span>
          </div>
          <h2 className="section-title" id={`h-${item.reference}`}>
            {item.reference}
          </h2>
          <div className="incident-update-body">{item.summary}</div>
        </article>
      ))}
      <p className="hint">
        <Link to="/login">Sign in</Link> to see updates on your own tickets.
      </p>
    </main>
  );
}
