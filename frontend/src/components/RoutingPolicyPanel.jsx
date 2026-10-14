import { useEffect, useState } from "react";
import { api } from "../api";

export default function RoutingPolicyPanel() {
  const [policy, setPolicy] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.debugRoutingPolicy().then(setPolicy).catch((err) => setError(err.message));
  }, []);

  if (error) return <p className="error">{error}</p>;
  if (!policy) return <p className="hint">Loading...</p>;

  return (
    <div className="card">
      <p className="hint" style={{ marginTop: 0 }}>
        Read live from backend/graph.py — never shown to customers or agents.
      </p>
      <ul className="policy-list">
        <li>
          <span className="policy-label">Base confidence threshold</span>
          <strong>{(policy.confidence_threshold * 100).toFixed(0)}%</strong>
        </li>
        <li>
          <span className="policy-label">High-risk confidence threshold</span>
          <strong>{(policy.high_risk_confidence_threshold * 100).toFixed(0)}%</strong>
        </li>
        <li>
          <span className="policy-label">High-risk categories</span>
          <strong>{policy.high_risk_categories.join(", ")}</strong>
        </li>
        <li>
          <span className="policy-label">Escalation language tags</span>
          <strong>{policy.escalation_language_tags.join(", ")}</strong>
        </li>
        <li>
          <span className="policy-label">Max clarification attempts</span>
          <strong>{policy.max_clarification_attempts}</strong>
        </li>
        <li>
          <span className="policy-label">Categories</span>
          <strong style={{ textAlign: "right" }}>{policy.categories.join(", ")}</strong>
        </li>
      </ul>
      <h3>Decision order</h3>
      <ol>
        {policy.decision_order.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>
    </div>
  );
}
