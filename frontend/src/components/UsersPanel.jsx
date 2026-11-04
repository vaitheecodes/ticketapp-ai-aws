import { useCallback, useEffect, useId, useState } from "react";
import { api } from "../api";
import OneTimePassword from "./OneTimePassword";
import Toast from "./Toast";

/* Admin Users page: customers, technicians and managers in one list
 * (backend/admin_users.py). The admin account itself is never listed: it
 * comes from the environment.
 *
 * New accounts and resets get a server-generated temporary password,
 * shown once in a dialog (OneTimePassword) and then gone; the user must
 * change it at first sign-in. Deactivating blocks sign-in at once and
 * keeps the user's tickets. */

const ROLES = { customer: "Customer", technician: "Technician", manager: "Manager" };

function DepartmentSelect({ id, value, onChange, departments }) {
  return (
    <select id={id} value={value || ""} onChange={(e) => onChange(e.target.value)}>
      <option value="">No department</option>
      {departments.map((d) => (
        <option key={d.department_id} value={d.department_id}>
          {d.name}
        </option>
      ))}
    </select>
  );
}

function AddUserForm({ departments, onCreated, onCancel }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("technician");
  const [departmentId, setDepartmentId] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const ids = { name: useId(), email: useId(), role: useId(), dept: useId() };

  async function handleSubmit(e) {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      const created = await api.adminCreateUser({
        name: name.trim(),
        email: email.trim(),
        role,
        department_id: role === "manager" ? departmentId : null,
      });
      onCreated(created);
    } catch (err) {
      setError(err.message);
      setSaving(false);
    }
  }

  return (
    <form className="inline-form card" onSubmit={handleSubmit}>
      <div className="field-row">
        <div className="field-group">
          <label className="field-label" htmlFor={ids.name}>
            Name
          </label>
          <input id={ids.name} value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} />
        </div>
        <div className="field-group">
          <label className="field-label" htmlFor={ids.email}>
            Email
          </label>
          <input id={ids.email} type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </div>
        <div className="field-group">
          <label className="field-label" htmlFor={ids.role}>
            Role
          </label>
          <select id={ids.role} value={role} onChange={(e) => setRole(e.target.value)}>
            {Object.entries(ROLES).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </div>
        {role === "manager" && (
          <div className="field-group">
            <label className="field-label" htmlFor={ids.dept}>
              Department
            </label>
            <DepartmentSelect id={ids.dept} value={departmentId} onChange={setDepartmentId} departments={departments} />
          </div>
        )}
      </div>
      <p className="hint">A temporary password is generated and shown once; they change it at first sign-in.</p>
      <div className="row-actions">
        <button type="submit" className="btn-primary" disabled={saving}>
          {saving ? "Creating..." : "Create user"}
        </button>
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
      {error && <p className="error">{error}</p>}
    </form>
  );
}

function EditUserForm({ user, departments, onSaved, onCancel }) {
  const [name, setName] = useState(user.name);
  const [email, setEmail] = useState(user.email);
  const [role, setRole] = useState(user.role);
  const [departmentId, setDepartmentId] = useState(user.department_id || "");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const ids = { name: useId(), email: useId(), role: useId(), dept: useId() };
  const isStaff = user.role !== "customer";

  async function handleSubmit(e) {
    e.preventDefault();
    const changes = {};
    if (name.trim() !== user.name) changes.name = name.trim();
    if (email.trim() !== user.email) changes.email = email.trim();
    if (isStaff && role !== user.role) changes.role = role;
    if (role === "manager" && (departmentId || null) !== (user.department_id || null)) {
      changes.department_id = departmentId || null;
    }
    if (Object.keys(changes).length === 0) return onCancel();
    setSaving(true);
    setError("");
    try {
      await api.adminUpdateUser(user.user_id, changes);
      onSaved(`Saved ${name.trim()}.`);
    } catch (err) {
      setError(err.message);
      setSaving(false);
    }
  }

  return (
    <form className="inline-form" onSubmit={handleSubmit}>
      <div className="field-row">
        <div className="field-group">
          <label className="field-label" htmlFor={ids.name}>
            Name
          </label>
          <input id={ids.name} value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} />
        </div>
        <div className="field-group">
          <label className="field-label" htmlFor={ids.email}>
            Email
          </label>
          <input id={ids.email} type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </div>
        {isStaff && (
          <div className="field-group">
            <label className="field-label" htmlFor={ids.role}>
              Role
            </label>
            <select id={ids.role} value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="technician">Technician</option>
              <option value="manager">Manager</option>
            </select>
          </div>
        )}
        {role === "manager" && (
          <div className="field-group">
            <label className="field-label" htmlFor={ids.dept}>
              Department
            </label>
            <DepartmentSelect id={ids.dept} value={departmentId} onChange={setDepartmentId} departments={departments} />
          </div>
        )}
      </div>
      {isStaff && role !== user.role && (
        <p className="hint">Changing the role signs them out; they sign in again with the new role.</p>
      )}
      <div className="row-actions">
        <button type="submit" className="btn-primary" disabled={saving}>
          {saving ? "Saving..." : "Save"}
        </button>
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
      {error && <p className="error">{error}</p>}
    </form>
  );
}

export default function UsersPanel() {
  const [users, setUsers] = useState(null);
  const [departments, setDepartments] = useState([]);
  const [role, setRole] = useState("");
  const [status, setStatus] = useState("");
  const [query, setQuery] = useState("");
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState(null);
  const [reveal, setReveal] = useState(null);
  const [toast, setToast] = useState(null);
  const [error, setError] = useState("");
  const ids = { q: useId(), role: useId(), status: useId() };
  const dismissToast = useCallback(() => setToast(null), []);

  const refresh = useCallback(() => {
    api.adminUsers({ role, status }).then(setUsers).catch((err) => setError(err.message));
  }, [role, status]);

  useEffect(refresh, [refresh]);
  useEffect(() => {
    api.adminDepartments().then((res) => setDepartments(res.departments)).catch(() => {});
  }, []);

  function showPassword(title, user) {
    setReveal({ title, name: user.name, email: user.email, password: user.temporary_password });
  }

  async function toggleActive(user) {
    const verb = user.active ? "Deactivate" : "Reactivate";
    if (user.active && !window.confirm(`Deactivate ${user.name}? They can't sign in until reactivated; their tickets stay.`)) {
      return;
    }
    try {
      await api.adminSetUserActive(user.user_id, !user.active);
      setToast({ message: `${verb}d ${user.name}.` });
      refresh();
    } catch (err) {
      setToast({ message: err.message, tone: "error" });
    }
  }

  async function resetPassword(user) {
    if (!window.confirm(`Reset the password of ${user.name}? Their current password stops working.`)) return;
    try {
      showPassword("Password reset", await api.adminResetUserPassword(user.user_id));
      refresh();
    } catch (err) {
      setToast({ message: err.message, tone: "error" });
    }
  }

  const q = query.trim().toLowerCase();
  const visible = (users || []).filter(
    (u) => !q || [u.name, u.email, u.user_id].some((v) => v.toLowerCase().includes(q)),
  );

  return (
    <div className="users-panel">
      <div className="section-head">
        <h2 className="section-title">Users</h2>
        {!adding && (
          <button className="btn-primary" onClick={() => setAdding(true)}>
            Add user
          </button>
        )}
      </div>
      {adding && (
        <AddUserForm
          departments={departments}
          onCancel={() => setAdding(false)}
          onCreated={(user) => {
            setAdding(false);
            showPassword("User created", user);
            refresh();
          }}
        />
      )}

      <div className="list-filters">
        <label className="sr-only" htmlFor={ids.q}>
          Search users
        </label>
        <input
          id={ids.q}
          type="search"
          className="table-search"
          placeholder="Search name, email or ID"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <label className="inline-select" htmlFor={ids.role}>
          <span>Role</span>
          <select id={ids.role} value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="">Any</option>
            {Object.entries(ROLES).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="inline-select" htmlFor={ids.status}>
          <span>Status</span>
          <select id={ids.status} value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Any</option>
            <option value="active">Active</option>
            <option value="inactive">Deactivated</option>
          </select>
        </label>
        {users && <span className="hint">{visible.length} users</span>}
      </div>

      {error && <p className="error">{error}</p>}
      {!users && !error && <p className="hint">Loading users...</p>}
      {users && visible.length === 0 && <p className="empty-state">No users match.</p>}
      {users && visible.length > 0 && (
        <div className="table-wrap">
          <table className="users-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Role</th>
                <th>Department</th>
                <th className="num">Tickets</th>
                <th>Status</th>
                <th>
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {visible.map((u) => [
                <tr key={u.user_id} className={u.active ? "" : "is-muted"}>
                  <td className="customer-cell">
                    <span>{u.name}</span>
                    <span className="hint">{u.email}</span>
                  </td>
                  <td>{ROLES[u.role]}</td>
                  <td>{u.department_name || <span className="hint">—</span>}</td>
                  <td className="num">{u.ticket_count ?? 0}</td>
                  <td>
                    <span className="chip-row">
                      <span className={`chip chip-${u.active ? "ok" : "neutral"}`}>
                        <span className="chip-dot" aria-hidden="true" />
                        {u.active ? "Active" : "Deactivated"}
                      </span>
                      {u.must_change_password && <span className="chip chip-warn">Temporary password</span>}
                    </span>
                  </td>
                  <td>
                    <div className="row-actions">
                      <button
                        aria-expanded={editing === u.user_id}
                        onClick={() => setEditing(editing === u.user_id ? null : u.user_id)}
                      >
                        Edit
                      </button>
                      <button onClick={() => resetPassword(u)}>Reset password</button>
                      <button className={u.active ? "btn-danger-outline" : ""} onClick={() => toggleActive(u)}>
                        {u.active ? "Deactivate" : "Reactivate"}
                      </button>
                    </div>
                  </td>
                </tr>,
                editing === u.user_id && (
                  <tr key={`${u.user_id}-edit`} className="row-editor">
                    <td colSpan={6}>
                      <EditUserForm
                        user={u}
                        departments={departments}
                        onCancel={() => setEditing(null)}
                        onSaved={(message) => {
                          setEditing(null);
                          setToast({ message });
                          refresh();
                        }}
                      />
                    </td>
                  </tr>
                ),
              ])}
            </tbody>
          </table>
        </div>
      )}

      <OneTimePassword reveal={reveal} onClose={() => setReveal(null)} />
      <Toast toast={toast} onDismiss={dismissToast} />
    </div>
  );
}
