/* Admin: customer accounts. Customers can also sign up themselves; the
 * admin can create accounts (temporary password, changed at first
 * sign-in), edit name/email, deactivate (blocks sign-in and live
 * sessions; tickets and chats are kept) and reset passwords. */

import { useCallback, useEffect, useId, useState } from "react";
import { api } from "../api";
import { generateTempPassword } from "../utils/password";
import TempPasswordInput from "./TempPasswordInput";

function AddCustomerForm({ onCreated }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [tempPassword, setTempPassword] = useState(generateTempPassword);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const nameId = useId();
  const emailId = useId();
  const passwordId = useId();

  async function handleSubmit(e) {
    e.preventDefault();
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const created = await api.adminCreateCustomer(name.trim(), email.trim(), tempPassword);
      setNotice(
        `Created ${created.name} with temporary password ${tempPassword}. Share it securely — they'll set their own on first sign-in.`,
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
      <h3>Add customer</h3>
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
        <div className="field-group" style={{ flex: 1 }}>
          <label className="field-label" htmlFor={passwordId}>
            Temporary password
          </label>
          <TempPasswordInput id={passwordId} value={tempPassword} onChange={setTempPassword} />
        </div>
        <button type="submit" className="btn-primary" disabled={saving}>
          {saving ? "Creating..." : "Create customer"}
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {notice && (
        <p className="success-text" role="status">
          {notice}
        </p>
      )}
    </form>
  );
}

function CustomerEditor({ customer, onDone, onCancel }) {
  const [name, setName] = useState(customer.name);
  const [email, setEmail] = useState(customer.email);
  const [tempPassword, setTempPassword] = useState(generateTempPassword);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const nameId = useId();
  const emailId = useId();
  const passwordId = useId();

  async function run(call, message) {
    setSaving(true);
    setError("");
    try {
      await call();
      onDone(message);
    } catch (err) {
      setError(err.message);
      setSaving(false);
    }
  }

  return (
    <div className="inline-form">
      <form
        className="field-row"
        onSubmit={(e) => {
          e.preventDefault();
          run(() => api.adminUpdateCustomer(customer.customer_id, { name: name.trim(), email: email.trim() }),
            `Updated ${name.trim()}.`);
        }}
      >
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
        <button type="submit" className="btn-primary" disabled={saving}>
          Save
        </button>
      </form>
      <form
        className="field-row"
        onSubmit={(e) => {
          e.preventDefault();
          run(() => api.adminResetCustomerPassword(customer.customer_id, tempPassword),
            `${customer.name}'s password was reset to ${tempPassword}. They'll change it at next sign-in.`);
        }}
      >
        <div className="field-group" style={{ flex: 1 }}>
          <label className="field-label" htmlFor={passwordId}>
            New temporary password
          </label>
          <TempPasswordInput id={passwordId} value={tempPassword} onChange={setTempPassword} />
        </div>
        <button type="submit" disabled={saving}>
          Reset password
        </button>
        <button type="button" onClick={onCancel}>
          Close
        </button>
      </form>
      {error && <p className="error">{error}</p>}
    </div>
  );
}

export default function CustomersPanel() {
  const [customers, setCustomers] = useState(null);
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const searchId = useId();

  const load = useCallback(() => {
    api.adminCustomers(query.trim()).then(setCustomers).catch((err) => setError(err.message));
  }, [query]);

  useEffect(() => {
    const timer = setTimeout(load, 250);
    return () => clearTimeout(timer);
  }, [load]);

  async function toggleActive(c) {
    setError("");
    try {
      await api.adminSetCustomerActive(c.customer_id, !c.active);
      setNotice(`${c.name} ${c.active ? "deactivated" : "reactivated"}.`);
      load();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div className="team-panel">
      <section aria-labelledby="customers-heading">
        <h2 id="customers-heading" className="section-title">
          Customers
        </h2>
        <AddCustomerForm onCreated={load} />
        <label className="sr-only" htmlFor={searchId}>
          Search customers
        </label>
        <input
          id={searchId}
          type="search"
          className="table-search"
          placeholder="Search by name, email or customer ID"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {error && <p className="error">{error}</p>}
        {notice && (
          <p className="success-text" role="status">
            {notice}
          </p>
        )}
        {!customers && !error && <p className="hint">Loading…</p>}
        {customers && customers.length === 0 && <p className="empty-state">No customers match.</p>}
        {customers && customers.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Email</th>
                  <th>Status</th>
                  <th>Tickets</th>
                  <th>Chats</th>
                  <th>
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {customers.map((c) => [
                  <tr key={c.customer_id} className={c.active ? "" : "row-muted"}>
                    <td>{c.name}</td>
                    <td>{c.email}</td>
                    <td>
                      <div className="chip-stack">
                        <span className={`chip ${c.active ? "chip-ok" : "chip-neutral"}`}>
                          {c.active ? "Active" : "Deactivated"}
                        </span>
                        {c.must_change_password && <span className="chip chip-warn">Password change pending</span>}
                      </div>
                    </td>
                    <td>{c.ticket_count}</td>
                    <td>{c.chat_count}</td>
                    <td>
                      <div className="row-actions">
                        <button
                          aria-expanded={editing === c.customer_id}
                          onClick={() => setEditing(editing === c.customer_id ? null : c.customer_id)}
                        >
                          Edit
                        </button>
                        <button className={c.active ? "btn-danger-outline" : ""} onClick={() => toggleActive(c)}>
                          {c.active ? "Deactivate" : "Reactivate"}
                        </button>
                      </div>
                    </td>
                  </tr>,
                  editing === c.customer_id && (
                    <tr key={`${c.customer_id}-edit`} className="row-editor">
                      <td colSpan={6}>
                        <CustomerEditor
                          customer={c}
                          onCancel={() => setEditing(null)}
                          onDone={(message) => {
                            setEditing(null);
                            setNotice(message);
                            load();
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
      </section>
    </div>
  );
}
