import { useNavigate } from "react-router-dom";
import { clearSession } from "../api";

export default function PortalHeader({ title, role }) {
  const navigate = useNavigate();

  function logout() {
    clearSession();
    navigate("/");
  }

  return (
    <header className="portal-header">
      <div className="portal-header-title">
        <h1>{title}</h1>
        {role && <span className="role-badge">{role}</span>}
      </div>
      <button onClick={logout}>Log out</button>
    </header>
  );
}
