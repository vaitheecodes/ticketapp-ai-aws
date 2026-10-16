import { useId, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, clearSession, getRole, HOME_FOR_ROLE, setMustChangePassword } from "../api";

/* Shown after signing in with a temporary password set by the admin.
 * The backend refuses every other staff endpoint until this succeeds. */
export default function ChangePassword() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();
  const currentId = useId();
  const nextId = useId();
  const confirmId = useId();

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    if (next !== confirm) {
      setError("New passwords don't match.");
      return;
    }
    setSubmitting(true);
    try {
      await api.changePassword(current, next);
      setMustChangePassword(false);
      navigate(HOME_FOR_ROLE[getRole()] || "/login");
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="page centered">
      <div className="card auth-card">
        <h1>Set a new password</h1>
        <p className="hint">
          Your account was created with a temporary password. Choose your own to continue.
        </p>
        <form className="auth-form" onSubmit={handleSubmit}>
          <div className="field-group">
            <label className="field-label" htmlFor={currentId}>
              Temporary password
            </label>
            <input
              id={currentId}
              type="password"
              autoComplete="current-password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
              required
            />
          </div>
          <div className="field-group">
            <label className="field-label" htmlFor={nextId}>
              New password
            </label>
            <input
              id={nextId}
              type="password"
              autoComplete="new-password"
              minLength={8}
              value={next}
              onChange={(e) => setNext(e.target.value)}
              required
            />
          </div>
          <div className="field-group">
            <label className="field-label" htmlFor={confirmId}>
              Confirm new password
            </label>
            <input
              id={confirmId}
              type="password"
              autoComplete="new-password"
              minLength={8}
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              required
            />
          </div>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <button type="submit" className="btn-primary btn-block" disabled={submitting}>
            {submitting ? "Saving..." : "Save and continue"}
          </button>
        </form>
        <button
          className="link-button"
          onClick={() => {
            clearSession();
            navigate("/login");
          }}
        >
          Sign out
        </button>
      </div>
    </div>
  );
}
