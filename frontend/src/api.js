const TOKEN_KEY = "ticketapp_token";
const ROLE_KEY = "ticketapp_role";
const MUST_CHANGE_KEY = "ticketapp_must_change_password";

// Where each role lands after login. The backend enforces the same
// boundaries on every endpoint; this only picks the portal.
export const HOME_FOR_ROLE = {
  customer: "/customer",
  technician: "/technician",
  manager: "/manager",
  admin: "/admin",
};

// Local dev falls through to Vite's /api proxy (vite.config.js -> :8000).
// Static Web Apps has no such proxy, so production is built with
// VITE_API_URL pointing at the App Service origin, e.g.
//   VITE_API_URL=https://<your-app>.azurewebsites.net/api
// Vite inlines import.meta.env at build time, so this is a build-time
// setting, not a runtime one.
const API_BASE = (import.meta.env.VITE_API_URL || "/api").replace(/\/+$/, "");

export function saveSession(token, role, mustChangePassword = false) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(ROLE_KEY, role);
  setMustChangePassword(mustChangePassword);
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(ROLE_KEY);
  localStorage.removeItem(MUST_CHANGE_KEY);
}

export function setMustChangePassword(value) {
  if (value) localStorage.setItem(MUST_CHANGE_KEY, "1");
  else localStorage.removeItem(MUST_CHANGE_KEY);
}

export function getMustChangePassword() {
  return localStorage.getItem(MUST_CHANGE_KEY) === "1";
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function getRole() {
  return localStorage.getItem(ROLE_KEY);
}

async function request(path, { method = "GET", body, auth = false } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (auth) {
    const token = getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }

  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = data.detail || detail;
    } catch {
      /* no JSON body */
    }
    throw new Error(detail);
  }

  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  // Customer signup is the only public signup; staff are created by the admin.
  customerSignup: (name, email, password) =>
    request("/auth/customer/signup", { method: "POST", body: { name, email, password } }),
  login: (email, password) =>
    request("/auth/login", { method: "POST", body: { email, password } }),
  me: () => request("/auth/me", { auth: true }),
  changePassword: (current_password, new_password) =>
    request("/auth/change-password", {
      method: "POST",
      body: { current_password, new_password },
      auth: true,
    }),

  ticketsMine: () => request("/tickets/mine", { auth: true }),
  ticketsNew: (ticket_text, channel) =>
    request("/tickets/new", { method: "POST", body: { ticket_text, channel }, auth: true }),
  ticketMessages: (ticketId) => request(`/tickets/${ticketId}/messages`, { auth: true }),
  ticketChat: (ticketId, message) =>
    request(`/tickets/${ticketId}/chat`, { method: "POST", body: { message }, auth: true }),

  queue: () => request("/queue", { auth: true }),
  queueMine: () => request("/queue/mine", { auth: true }),
  queueAssign: (ticketId) =>
    request(`/queue/${ticketId}/assign`, { method: "POST", auth: true }),
  queueResolve: (ticketId) =>
    request(`/queue/${ticketId}/resolve`, { method: "POST", auth: true }),

  adminTickets: () => request("/admin/tickets", { auth: true }),
  adminTechnicians: () => request("/admin/technicians", { auth: true }),
  adminReassignTicket: (ticketId, technicianId) =>
    request(`/admin/tickets/${ticketId}/reassign`, {
      method: "POST",
      body: { technician_id: technicianId || null },
      auth: true,
    }),

  adminStaff: () => request("/admin/staff", { auth: true }),
  adminCreateStaff: (name, email, role, temporary_password) =>
    request("/admin/staff", {
      method: "POST",
      body: { name, email, role, temporary_password },
      auth: true,
    }),
  adminSetStaffActive: (staffId, active) =>
    request(`/admin/staff/${staffId}/${active ? "activate" : "deactivate"}`, {
      method: "POST",
      auth: true,
    }),
  adminResetStaffPassword: (staffId, temporary_password) =>
    request(`/admin/staff/${staffId}/reset-password`, {
      method: "POST",
      body: { temporary_password },
      auth: true,
    }),
  adminSetManagerDepartments: (staffId, department_ids) =>
    request(`/admin/staff/${staffId}/departments`, {
      method: "PUT",
      body: { department_ids },
      auth: true,
    }),

  adminDepartments: () => request("/admin/departments", { auth: true }),
  adminCreateDepartment: (name, categories) =>
    request("/admin/departments", { method: "POST", body: { name, categories }, auth: true }),
  adminUpdateDepartment: (departmentId, name, categories) =>
    request(`/admin/departments/${departmentId}`, {
      method: "PUT",
      body: { name, categories },
      auth: true,
    }),
  adminDeleteDepartment: (departmentId) =>
    request(`/admin/departments/${departmentId}`, { method: "DELETE", auth: true }),

  managerDepartments: () => request("/manager/departments", { auth: true }),
  adminSimulateTicket: (ticket_text, channel, customer_id) =>
    request("/admin/simulate-ticket", {
      method: "POST",
      body: { ticket_text, channel, customer_id: customer_id || undefined },
      auth: true,
    }),

  debugRoutingPolicy: () => request("/debug/routing-policy"),
};
