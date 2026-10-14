import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { getRole, getToken } from "./api";
import Landing from "./pages/Landing";
import CustomerLogin from "./pages/CustomerLogin";
import CustomerPortal from "./pages/CustomerPortal";
import AgentLogin from "./pages/AgentLogin";
import AgentPortal from "./pages/AgentPortal";
import AdminLogin from "./pages/AdminLogin";
import AdminPortal from "./pages/AdminPortal";
import DebugPanel from "./pages/DebugPanel";
import UiGallery from "./pages/UiGallery";

const LOGIN_PATHS = {
  customer: "/customer/login",
  agent: "/agent/login",
  admin: "/admin/login",
};

function RequireRole({ role, children }) {
  if (getToken() && getRole() === role) return children;
  return <Navigate to={LOGIN_PATHS[role]} replace />;
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/customer/login" element={<CustomerLogin />} />
        <Route
          path="/customer"
          element={
            <RequireRole role="customer">
              <CustomerPortal />
            </RequireRole>
          }
        />
        <Route path="/agent/login" element={<AgentLogin />} />
        <Route
          path="/agent"
          element={
            <RequireRole role="agent">
              <AgentPortal />
            </RequireRole>
          }
        />
        <Route path="/admin/login" element={<AdminLogin />} />
        <Route
          path="/admin"
          element={
            <RequireRole role="admin">
              <AdminPortal />
            </RequireRole>
          }
        />
        <Route path="/debug" element={<DebugPanel />} />
        <Route path="/ui" element={<UiGallery />} />
      </Routes>
    </BrowserRouter>
  );
}
