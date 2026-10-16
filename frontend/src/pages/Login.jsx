import { useId, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, HOME_FOR_ROLE, saveSession } from "../api";
import { TicketIcon } from "../components/Icons";

/* One sign-in page for every role. The backend works out who the email
 * belongs to and returns the role; we only pick the portal. Signup here
 * is customer-only: technicians and managers are created by the admin,
 * and the admin account comes from server config. */
export default function Login() {
  const [mode, setMode] = useState("login");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();
  const nameId = useId();
  const emailId = useId();
  const passwordId = useId();
  const isSignup = mode === "signup";

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      const result = isSignup
        ? await api.customerSignup(name, email, password)
        : await api.login(email, password);
      saveSession(result.token, result.role, result.must_change_password);
      navigate(result.must_change_password ? "/change-password" : HOME_FOR_ROLE[result.role]);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="page centered">
      <div className="hero-brand">
        <TicketIcon className="hero-brand-icon" />
        <h1 className="hero-title">Site24x7 Support</h1>
      </div>
      <p className="hero-subtitle">
        {isSignup ? "Create a customer account to raise tickets." : "Sign in to your portal."}
      </p>
      <div className="card auth-card">
        <h2>{isSignup ? "Create account" : "Sign in"}</h2>
        <form className="auth-form" onSubmit={handleSubmit}>
          {isSignup && (
            <div className="field-group">
              <label className="field-label" htmlFor={nameId}>
                Name
              </label>
              <input
                id={nameId}
                autoComplete="name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
              />
            </div>
          )}
          <div className="field-group">
            <label className="field-label" htmlFor={emailId}>
              Email
            </label>
            <input
              id={emailId}
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </div>
          <div className="field-group">
            <label className="field-label" htmlFor={passwordId}>
              Password
            </label>
            <input
              id={passwordId}
              type="password"
              autoComplete={isSignup ? "new-password" : "current-password"}
              minLength={isSignup ? 8 : undefined}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <button type="submit" className="btn-primary btn-block" disabled={submitting}>
            {submitting ? "Please wait..." : isSignup ? "Create account" : "Sign in"}
          </button>
        </form>
        <button
          className="link-button"
          onClick={() => {
            setError("");
            setMode(isSignup ? "login" : "signup");
          }}
        >
          {isSignup ? "Already have an account? Sign in" : "New customer? Create an account"}
        </button>
        {!isSignup && (
          <p className="hint" style={{ marginBottom: 0 }}>
            Staff accounts are created by your administrator.
          </p>
        )}
      </div>
    </div>
  );
}
