import { useEffect, useId, useState } from "react";
import { api } from "../api";

/* Admin Departments page. Departments map to KB categories (each category
 * to at most one department) and each manager belongs to one department:
 * that mapping routes incidents and escalations to the right manager.
 * Managers are assigned a department on the Users page. */

function DepartmentForm({ department, departments, categories, onSaved, onCancel }) {
  const [name, setName] = useState(department?.name || "");
  const [selected, setSelected] = useState(new Set(department?.categories || []));
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const nameId = useId();

  // A category belongs to at most one department; show who owns it.
  const ownerOf = {};
  for (const d of departments) {
    if (d.department_id === department?.department_id) continue;
    for (const c of d.categories) ownerOf[c] = d.name;
  }

  function toggle(c) {
    const next = new Set(selected);
    if (next.has(c)) next.delete(c);
    else next.add(c);
    setSelected(next);
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      if (department) {
        await api.adminUpdateDepartment(department.department_id, name.trim(), [...selected]);
      } else {
        await api.adminCreateDepartment(name.trim(), [...selected]);
      }
      onSaved(department ? `Updated ${name.trim()}.` : `Created ${name.trim()}.`);
    } catch (err) {
      setError(err.message);
      setSaving(false);
    }
  }

  return (
    <form className="inline-form" onSubmit={handleSubmit}>
      <div className="field-group" style={{ maxWidth: "24rem" }}>
        <label className="field-label" htmlFor={nameId}>
          Department name
        </label>
        <input id={nameId} value={name} onChange={(e) => setName(e.target.value)} required />
      </div>
      <fieldset className="checkbox-grid">
        <legend className="field-label">KB categories this department owns</legend>
        {categories.map((c) => (
          <label key={c} className={`checkbox-label ${ownerOf[c] ? "is-disabled" : ""}`}>
            <input
              type="checkbox"
              checked={selected.has(c)}
              disabled={Boolean(ownerOf[c])}
              onChange={() => toggle(c)}
            />
            <span className="mono">{c}</span>
            {ownerOf[c] && <span className="hint"> ({ownerOf[c]})</span>}
          </label>
        ))}
      </fieldset>
      <div className="row-actions">
        <button type="submit" className="btn-primary" disabled={saving}>
          {saving ? "Saving..." : department ? "Save department" : "Create department"}
        </button>
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
      {error && <p className="error">{error}</p>}
    </form>
  );
}

function DepartmentsSection({ departments, categories, staff, onChanged }) {
  // department_id being edited, "new", or null.
  const [editing, setEditing] = useState(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const managerName = Object.fromEntries(staff.map((p) => [p.staff_id, p.name]));
  const unowned = categories.filter((c) => !departments.some((d) => d.categories.includes(c)));

  function saved(message) {
    setEditing(null);
    setNotice(message);
    onChanged();
  }

  async function remove(d) {
    if (!window.confirm(`Delete the ${d.name} department? Its categories become unassigned.`)) return;
    setError("");
    try {
      await api.adminDeleteDepartment(d.department_id);
      saved(`Deleted ${d.name}.`);
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <section aria-labelledby="departments-heading">
      <div className="section-head">
        <h2 id="departments-heading" className="section-title">
          Departments
        </h2>
        {editing !== "new" && (
          <button className="btn-primary" onClick={() => setEditing("new")}>
            Add department
          </button>
        )}
      </div>
      {unowned.length > 0 && (
        <p className="pending-text">
          Not owned by any department (will need manual routing): {unowned.join(", ")}
        </p>
      )}
      {error && <p className="error">{error}</p>}
      {notice && (
        <p className="success-text" role="status">
          {notice}
        </p>
      )}
      {editing === "new" && (
        <div className="card">
          <DepartmentForm
            departments={departments}
            categories={categories}
            onSaved={saved}
            onCancel={() => setEditing(null)}
          />
        </div>
      )}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Department</th>
              <th>Categories</th>
              <th>Managers</th>
              <th>
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {departments.map((d) => [
              <tr key={d.department_id}>
                <td>{d.name}</td>
                <td className="wrap">
                  <ul className="category-chips">
                    {d.categories.map((c) => (
                      <li key={c} className="chip chip-neutral chip-mono">
                        {c}
                      </li>
                    ))}
                  </ul>
                </td>
                <td className="wrap">
                  {d.manager_ids.map((id) => managerName[id]).filter(Boolean).join(", ") || (
                    <span className="hint">No manager</span>
                  )}
                </td>
                <td>
                  <div className="row-actions">
                    <button
                      aria-expanded={editing === d.department_id}
                      onClick={() => setEditing(editing === d.department_id ? null : d.department_id)}
                    >
                      Edit
                    </button>
                    <button className="btn-danger-outline" onClick={() => remove(d)}>
                      Delete
                    </button>
                  </div>
                </td>
              </tr>,
              editing === d.department_id && (
                <tr key={`${d.department_id}-edit`} className="row-editor">
                  <td colSpan={4}>
                    <DepartmentForm
                      department={d}
                      departments={departments}
                      categories={categories}
                      onSaved={saved}
                      onCancel={() => setEditing(null)}
                    />
                  </td>
                </tr>
              ),
            ])}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function DepartmentsPanel() {
  const [departments, setDepartments] = useState(null);
  const [categories, setCategories] = useState([]);
  const [staff, setStaff] = useState([]);
  const [error, setError] = useState("");

  function refresh() {
    Promise.all([api.adminDepartments(), api.adminStaff()])
      .then(([deptRes, staffRows]) => {
        setDepartments(deptRes.departments);
        setCategories(deptRes.categories);
        setStaff(staffRows);
      })
      .catch((err) => setError(err.message));
  }

  useEffect(refresh, []);

  if (error) return <p className="error">{error}</p>;
  if (!departments) return <p className="hint">Loading departments...</p>;
  return (
    <DepartmentsSection departments={departments} categories={categories} staff={staff} onChanged={refresh} />
  );
}
