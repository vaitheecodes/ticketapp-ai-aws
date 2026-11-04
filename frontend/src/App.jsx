import { useEffect } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { getMustChangePassword, getRole, getToken, HOME_FOR_ROLE, onSessionChange } from "./api";
import Login from "./pages/Login";
import ChangePassword from "./pages/ChangePassword";
import CustomerPortal from "./pages/CustomerPortal";
import TechnicianPortal from "./pages/TechnicianPortal";
import ManagerPortal from "./pages/ManagerPortal";
import AdminPortal from "./pages/AdminPortal";
import Docs from "./pages/Docs";
import IncidentHistory from "./pages/IncidentHistory";
import RoutingPolicyDoc from "./pages/RoutingPolicyDoc";
import TryATicket from "./pages/TryATicket";
import UiGallery from "./pages/UiGallery";

// Client-side gate only, for navigation. Every endpoint enforces the
// same roles (and the forced password change) on the backend.
function RequireRole({ role, roles = [role], children }) {
  if (!getToken()) return <Navigate to="/login" replace />;
  if (getMustChangePassword()) return <Navigate to="/change-password" replace />;
  if (!roles.includes(getRole())) return <Navigate to={HOME_FOR_ROLE[getRole()] || "/login"} replace />;
  return children;
}

function LoginRoute() {
  const role = getRole();
  if (getToken() && HOME_FOR_ROLE[role]) {
    return <Navigate to={getMustChangePassword() ? "/change-password" : HOME_FOR_ROLE[role]} replace />;
  }
  return <Login />;
}

function ChangePasswordRoute() {
  if (!getToken()) return <Navigate to="/login" replace />;
  return <ChangePassword />;
}

const PORTALS = [
  ["customer", CustomerPortal],
  ["technician", TechnicianPortal],
  ["manager", ManagerPortal],
  ["admin", AdminPortal],
];

export default function App() {
  // Another tab signed in as someone else, or out: reload, so the route
  // guards pick the portal for the token this tab now sends.
  useEffect(() => onSessionChange(() => window.location.reload()), []);

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<LoginRoute />} />
        <Route path="/login" element={<LoginRoute />} />
        <Route path="/change-password" element={<ChangePasswordRoute />} />
        {PORTALS.map(([role, Portal]) => (
          <Route
            key={role}
            path={`${HOME_FOR_ROLE[role]}/:section?`}
            element={
              <RequireRole role={role}>
                <Portal />
              </RequireRole>
            }
          />
        ))}
        {/* Pre-unified-login bookmarks. */}
        <Route path="/customer/login" element={<Navigate to="/login" replace />} />
        <Route path="/agent/login" element={<Navigate to="/login" replace />} />
        <Route path="/admin/login" element={<Navigate to="/login" replace />} />
        <Route path="/agent" element={<Navigate to="/technician" replace />} />
        {/* Technicians and managers: the pipeline trace, nothing saved. */}
        <Route
          path="/try-a-ticket"
          element={
            <RequireRole roles={["technician", "manager"]}>
              <TryATicket />
            </RequireRole>
          }
        />
        {/* Public, no login: visibility=public incidents only, and the docs. */}
        <Route path="/incidents" element={<IncidentHistory />} />
        <Route path="/docs" element={<Docs />} />
        <Route path="/docs/routing-policy" element={<RoutingPolicyDoc />} />
        <Route path="/debug" element={<Navigate to="/docs/routing-policy" replace />} />
        <Route path="/ui" element={<UiGallery />} />
      </Routes>
    </BrowserRouter>
  );
}
