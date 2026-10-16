import { useEffect, useId, useState } from "react";
import { api } from "../api";
import { generateTempPassword } from "../utils/password";
import TempPasswordInput from "./TempPasswordInput";

/* Admin Team page: technician/manager accounts and departments.
 *
 * Accounts are created with a temporary password the admin passes on
 * out of band; the backend forces a change on first sign-in. Departments
 * map to KB categories (each category to at most one department) and each
 * manager belongs to one department — that mapping is what routes incidents
 * and escalations to the right manager. */

const ROLE_LABELS = { technician: "Technician", manager: "Manager" };

function DepartmentSelect({ id, value, onChange, departments }) {
  return (
    <select id={id} value={value || ""} onChange={(e) => onChange(e.target.value)}>
      <option value="">No department yet</option>
      {departments.map((d) => (
        <option key={d.department_id} value={d.department_id}>
          {d.name}
        </option>
      ))}
    </select>
  );
}

function AddStaffForm({ departments, onCreated }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("technician");
  const [departmentId, setDepartmentId] = useState("");
  const [tempPassword, setTempPassword] = useState(generateTempPassword);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [saving, setSaving] = useState(false);
  const nameId = useId();
  const emailId = useId();
  const roleId = useId();
  const departmentFieldId = useId();
  const passwordId = useId();

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setNotice("");
    setSaving(true);
    try {
      const created = await api.adminCreateStaff(
        name.trim(),
        email.trim(),
        role,
        tempPassword,
        role === "manager" ? departmentId : null,
      );
      setNotice(
        `Created ${created.name} (${ROLE_LABELS[created.role]}) with temporary password ` +
          `${tempPassword}. Share it with them securely — they'll set their own on first sign-in.`,
      );
      setName("");
      setEmail("");
      setTempPassword(generateTempPassword());
      onCreated();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="card team-form" onSubmit={handleSubmit}>
      <h3>Add team member</h3>
      <div className="field-row">
        <div className="field-group" style={{ flex: 1 }}>
          <label className="field-label" htmlFor={nameId}>
            Name
          </label>
          <input id={nameId} value={name} onChange={(e) => setName(e.target.value)} required />
        </div>
        <div className="field-group" style={{ flex: 1 }}>
          <label className="field-label" htmlFor={emailId}>
            Email
          </label>
          <input
            id={emailId}
            type="email"
            autoComplete="off"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </div>
      </div>
      <div className="field-row">
        <div className="field-group">
          <label className="field-label" htmlFor={roleId}>
            Role
          </label>
          <select id={roleId} value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="technician">Technician</option>
            <option value="manager">Manager</option>
          </select>
        </div>
        {role === "manager" && (
          <div className="field-group">
            <label className="field-label" htmlFor={departmentFieldId}>
              Department
            </label>
            <DepartmentSelect
              id={departmentFieldId}
              value={departmentId}
              onChange={setDepartmentId}
              departments={departments}
            />
          </div>
        )}
        <div className="field-group" style={{ flex: 1 }}>
          <label className="field-label" htmlFor={passwordId}>
            Temporary password
          </label>
          <TempPasswordInput id={passwordId} value={tempPassword} onChange={setTempPassword} />
        </div>
        <button type="submit" className="btn-primary" disabled={saving}>
          {saving ? "Creating..." : "Create account"}
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

function ResetPasswordForm({ person, onDone, onCancel }) {
  const [tempPassword, setTempPassword] = useState(generateTempPassword);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const id = useId();

  async function handleSubmit(e) {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      await api.adminResetStaffPassword(person.staff_id, tempPassword);
      onDone(
        `${person.name}'s password was reset to ${tempPassword}. They'll be asked to change it on next sign-in.`,
      );
    } catch (err) {
      setError(err.message);
      setSaving(false);
    }
  }

  return (
    <form className="inline-form" onSubmit={handleSubmit}>
      <div className="field-row">
        <div className="field-group" style={{ flex: 1 }}>
          <label className="field-label" htmlFor={id}>
            New temporary password for {person.name}
          </label>
          <TempPasswordInput id={id} value={tempPassword} onChange={setTempPassword} />
        </div>
        <button type="submit" className="btn-primary" disabled={saving}>
          {saving ? "Saving..." : "Reset password"}
        </button>
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
      {error && <p className="error">{error}</p>}
    </form>
  );
}

function EditStaffForm({ person, departments, onDone, onCancel }) {
  const [name, setName] = useState(person.name);
  const [email, setEmail] = useState(person.email);
  const [role, setRole] = useState(person.role);
  const [departmentId, setDepartmentId] = useState(person.department_id || "");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const nameId = useId();
  const emailId = useId();
  const roleId = useId();
  const departmentFieldId = useId();

  async function handleSubmit(e) {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      const changes = { name: name.trim(), email: email.trim(), role };
      if (role === "manager") changes.department_id = departmentId || null;
      await api.adminUpdateStaff(person.staff_id, changes);
      onDone(
        role !== person.role
          ? `${name.trim()} is now a ${ROLE_LABELS[role].toLowerCase()} and has been signed out.`
          : `Updated ${name.trim()}.`,
      );
    } catch (err) {
      setError(err.message);
      setSaving(false);
    }
  }

  return (
    <form className="inline-form" onSubmit={handleSubmit}>
      <div className="field-row">
        <div className="field-group" style={{ flex: 1 }}>
          <label className="field-label" htmlFor={nameId}>
            Name
          </label>
          <input id={nameId} value={name} onChange={(e) => setName(e.target.value)} required />
        </div>
        <div className="field-group" style={{ flex: 1 }}>
          <label className="field-label" htmlFor={emailId}>
            Email
          </label>
          <input id={emailId} type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </div>
      </div>
      <div className="field-row">
        <div className="field-group">
          <label className="field-label" htmlFor={roleId}>
            Role
          </label>
          <select id={roleId} value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="technician">Technician</option>
            <option value="manager">Manager</option>
          </select>
        </div>
        {role === "manager" && (
          <div className="field-group">
            <label className="field-label" htmlFor={departmentFieldId}>
              Department
            </label>
            <DepartmentSelect
              id={departmentFieldId}
              value={departmentId}
              onChange={setDepartmentId}
              departments={departments}
            />
          </div>
        )}
      </div>
      {role !== person.role && (
        <p className="hint" style={{ margin: 0 }}>
          Changing the role signs {person.name} out. Technicians don't belong to a department.
        </p>
      )}
      <div className="row-actions">
        <button type="submit" className="btn-primary" disabled={saving}>
          {saving ? "Saving..." : "Save changes"}
        </button>
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
      {error && <p className="error">{error}</p>}
    </form>
  );
}

function StaffTable({ staff, departments, workload, onChanged }) {
  // { staffId, kind: "reset" | "edit" } — at most one inline editor open.
  const [editing, setEditing] = useState(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  async function toggleActive(person) {
    setError("");
    setNotice("");
    try {
      await api.adminSetStaffActive(person.staff_id, !person.active);
      onChanged();
    } catch (err) {
      setError(err.message);
    }
  }

  function done(message) {
    setEditing(null);
    setNotice(message);
    onChanged();
  }

  if (staff.length === 0) {
    return <p className="empty-state">No technicians or managers yet — add one above.</p>;
  }

  return (
    <>
      {error && <p className="error">{error}</p>}
      {notice && (
        <p className="success-text" role="status">
          {notice}
        </p>
      )}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Email</th>
              <th>Role</th>
              <th>Status</th>
              <th>Department / workload</th>
              <th>
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {staff.map((p) => {
              const open = editing?.staffId === p.staff_id ? editing.kind : null;
              const load = workload[p.staff_id];
              return [
                <tr key={p.staff_id} className={p.active ? "" : "row-muted"}>
                  <td>{p.name}</td>
                  <td>{p.email}</td>
                  <td>
                    <span className={`chip ${p.role === "manager" ? "chip-accent" : "chip-info"}`}>
                      {ROLE_LABELS[p.role]}
                    </span>
                  </td>
                  <td>
                    <div className="chip-stack">
                      <span className={`chip ${p.active ? "chip-ok" : "chip-neutral"}`}>
                        {p.active ? "Active" : "Deactivated"}
                      </span>
                      {p.must_change_password && (
                        <span className="chip chip-warn">Password change pending</span>
                      )}
                    </div>
                  </td>
                  <td className="wrap">
                    {p.role === "manager"
                      ? p.department_name || "No department"
                      : load
                        ? `${load.active_count || 0} active · ${load.resolved_count || 0} resolved`
                        : "—"}
                  </td>
                  <td>
                    <div className="row-actions">
                      <button
                        aria-expanded={open === "edit"}
                        onClick={() => setEditing(open === "edit" ? null : { staffId: p.staff_id, kind: "edit" })}
                      >
                        Edit
                      </button>
                      <button
                        aria-expanded={open === "reset"}
                        onClick={() =>
                          setEditing(open === "reset" ? null : { staffId: p.staff_id, kind: "reset" })
                        }
                      >
                        Reset password
                      </button>
                      <button
                        className={p.active ? "btn-danger-outline" : ""}
                        onClick={() => toggleActive(p)}
                      >
                        {p.active ? "Deactivate" : "Reactivate"}
                      </button>
                    </div>
                  </td>
                </tr>,
                open && (
                  <tr key={`${p.staff_id}-edit`} className="row-editor">
                    <td colSpan={6}>
                      {open === "reset" ? (
                        <ResetPasswordForm person={p} onDone={done} onCancel={() => setEditing(null)} />
                      ) : (
                        <EditStaffForm
                          person={p}
                          departments={departments}
                          onDone={done}
                          onCancel={() => setEditing(null)}
                        />
                      )}
                    </td>
                  </tr>
                ),
              ];
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

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

export default function TeamPanel() {
  const [staff, setStaff] = useState(null);
  const [departments, setDepartments] = useState([]);
  const [categories, setCategories] = useState([]);
  const [workload, setWorkload] = useState({});
  const [error, setError] = useState("");

  function refresh() {
    Promise.all([api.adminStaff(), api.adminDepartments(), api.adminTechnicians()])
      .then(([staffRows, deptRes, technicians]) => {
        setStaff(staffRows);
        setDepartments(deptRes.departments);
        setCategories(deptRes.categories);
        setWorkload(Object.fromEntries(technicians.map((t) => [t.staff_id, t])));
      })
      .catch((err) => setError(err.message));
  }

  useEffect(refresh, []);

  if (error) return <p className="error">{error}</p>;
  if (!staff) return <p className="hint">Loading team...</p>;

  return (
    <div className="team-panel">
      <section aria-labelledby="team-heading">
        <h2 id="team-heading" className="section-title">
          Team members
        </h2>
        <AddStaffForm departments={departments} onCreated={refresh} />
        <StaffTable staff={staff} departments={departments} workload={workload} onChanged={refresh} />
      </section>
      <DepartmentsSection
        departments={departments}
        categories={categories}
        staff={staff}
        onChanged={refresh}
      />
    </div>
  );
}
