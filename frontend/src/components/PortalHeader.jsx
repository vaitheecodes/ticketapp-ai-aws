import { useNavigate } from "react-router-dom";
import { clearSession } from "../api";
import { TicketIcon } from "./Icons";
import ThemeToggle from "./ThemeToggle";

/* The app bar: product mark and name, the portal, the role tag; on the
 * right the theme switch and Log out. */
export default function PortalHeader({ title, role }) {
  const navigate = useNavigate();

  function logout() {
    clearSession();
    navigate("/login");
  }

  return (
    <header className="portal-header">
      <div className="portal-header-title">
        <TicketIcon className="portal-mark" width="20" height="20" />
        <span className="portal-product">Site24x7 Help Desk</span>
        <span className="portal-divider" aria-hidden="true" />
        <h1>{title}</h1>
        {role && <span className="role-badge">{role}</span>}
      </div>
      <div className="portal-header-actions">
        <ThemeToggle />
        <button className="btn btn-ghost btn-sm" onClick={logout}>
          Log out
        </button>
      </div>
    </header>
  );
}
