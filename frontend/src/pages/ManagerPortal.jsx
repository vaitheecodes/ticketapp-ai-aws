import { useEffect, useState } from "react";
import { api } from "../api";
import PortalHeader from "../components/PortalHeader";

/* Manager dashboard. Its scope is the departments the admin assigned on
 * the Team page: every incident and ticket a manager sees will be
 * filtered to the KB categories those departments own. */
export default function ManagerPortal() {
  const [departments, setDepartments] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.managerDepartments().then(setDepartments).catch((err) => setError(err.message));
  }, []);

  return (
    <div className="page">
      <PortalHeader title="Manager Dashboard" role="manager" />
      <h2 className="section-title">My departments</h2>
      {error && <p className="error">{error}</p>}
      {!error && !departments && <p className="hint">Loading...</p>}
      {departments && departments.length === 0 && (
        <p className="empty-state">
          You don't own any departments yet. Ask your administrator to assign you one from the
          Team page.
        </p>
      )}
      {departments && departments.length > 0 && (
        <ul className="dept-cards">
          {departments.map((d) => (
            <li key={d.department_id} className="card dept-card">
              <h3>{d.name}</h3>
              {d.categories.length ? (
                <ul className="category-chips" aria-label={`Categories owned by ${d.name}`}>
                  {d.categories.map((c) => (
                    <li key={c} className="chip chip-neutral chip-mono">
                      {c}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="hint" style={{ margin: 0 }}>
                  No categories mapped yet.
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
