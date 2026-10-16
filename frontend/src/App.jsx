import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { getMustChangePassword, getRole, getToken, HOME_FOR_ROLE } from "./api";
import Login from "./pages/Login";
import ChangePassword from "./pages/ChangePassword";
import CustomerPortal from "./pages/CustomerPortal";
import TechnicianPortal from "./pages/TechnicianPortal";
import ManagerPortal from "./pages/ManagerPortal";
import AdminPortal from "./pages/AdminPortal";
import DebugPanel from "./pages/DebugPanel";
import UiGallery from "./pages/UiGallery";

// Client-side gate only, for navigation. Every endpoint enforces the
// same role (and the forced password change) on the backend.
function RequireRole({ role, children }) {
  if (!getToken()) return <Navigate to="/login" replace />;
  if (getMustChangePassword()) return <Navigate to="/change-password" replace />;
  if (getRole() !== role) return <Navigate to={HOME_FOR_ROLE[getRole()] || "/login"} replace />;
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
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<LoginRoute />} />
        <Route path="/login" element={<LoginRoute />} />
        <Route path="/change-password" element={<ChangePasswordRoute />} />
        {PORTALS.map(([role, Portal]) => (
          <Route
            key={role}
            path={HOME_FOR_ROLE[role]}
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
        <Route path="/debug" element={<DebugPanel />} />
        <Route path="/ui" element={<UiGallery />} />
      </Routes>
    </BrowserRouter>
  );
}
