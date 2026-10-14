import { useId, useState } from "react";
import { useNavigate } from "react-router-dom";
import { saveSession } from "../api";

export default function AuthForm({ title, role, onSignup, onLogin, redirectTo }) {
  const [mode, setMode] = useState("login");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const navigate = useNavigate();
  const nameId = useId();
  const emailId = useId();
  const passwordId = useId();
  const isSignup = mode === "signup";

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    try {
      const result =
        mode === "signup" ? await onSignup(name, email, password) : await onLogin(email, password);
      saveSession(result.token, role);
      navigate(redirectTo);
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div className="page centered">
      <div className="card auth-card">
        <h1>{title}</h1>
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
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>
          {error && <p className="error">{error}</p>}
          <button type="submit" className="btn-primary btn-block">
            {mode === "signup" ? "Sign up" : "Log in"}
          </button>
        </form>
        <button
          className="link-button"
          onClick={() => {
            setError("");
            setMode(mode === "signup" ? "login" : "signup");
          }}
        >
          {mode === "signup" ? "Already have an account? Log in" : "New here? Sign up"}
        </button>
      </div>
    </div>
  );
}
