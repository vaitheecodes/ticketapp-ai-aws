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

async function errorDetail(res) {
  try {
    const data = await res.json();
    return typeof data.detail === "string" ? data.detail : res.statusText;
  } catch {
    return res.statusText;
  }
}

// Multipart upload; the browser sets the boundary Content-Type itself.
async function upload(path, formData) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${getToken()}` },
    body: formData,
  });
  if (!res.ok) throw new Error(await errorDetail(res));
  return res.json();
}

// Attachments need the bearer token, so a plain <a href> can't fetch them.
async function downloadFile(path, filename) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { Authorization: `Bearer ${getToken()}` },
  });
  if (!res.ok) throw new Error(await errorDetail(res));
  const url = URL.createObjectURL(await res.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
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

  categories: () => request("/categories"),
  ticketsMine: () => request("/tickets/mine", { auth: true }),
  // Raise a ticket: email-style, no chat.
  ticketsCreate: ({ subject, category, priority, description }) =>
    request("/tickets", {
      method: "POST",
      body: { subject, category: category || null, priority, description },
      auth: true,
    }),
  ticketDetail: (ticketId) => request(`/tickets/${ticketId}`, { auth: true }),
  ticketReply: (ticketId, message) =>
    request(`/tickets/${ticketId}/reply`, { method: "POST", body: { message }, auth: true }),
  ticketReopen: (ticketId) =>
    request(`/tickets/${ticketId}/reopen`, { method: "POST", auth: true }),
  uploadAttachments: (ticketId, messageId, files) => {
    const form = new FormData();
    form.append("message_id", messageId);
    for (const file of files) form.append("files", file);
    return upload(`/tickets/${ticketId}/attachments`, form);
  },
  downloadAttachment: (ticketId, attachment) =>
    downloadFile(
      `/tickets/${ticketId}/attachments/${attachment.attachment_id}`,
      attachment.filename,
    ),

  // AI chat sessions: separate from tickets until escalated.
  chatsList: () => request("/chats", { auth: true }),
  chatStart: ({ issue, category, contact_name }) =>
    request("/chats", {
      method: "POST",
      body: { issue, category: category || null, contact_name },
      auth: true,
    }),
  chatGet: (sessionId) => request(`/chats/${sessionId}`, { auth: true }),
  chatSend: (sessionId, message) =>
    request(`/chats/${sessionId}/messages`, { method: "POST", body: { message }, auth: true }),
  chatResolve: (sessionId) => request(`/chats/${sessionId}/resolve`, { method: "POST", auth: true }),
  chatEscalate: (sessionId, subject) =>
    request(`/chats/${sessionId}/escalate`, {
      method: "POST",
      body: { subject: subject || null },
      auth: true,
    }),

  chatReviews: (status) =>
    request(`/chat-reviews${status ? `?status=${status}` : ""}`, { auth: true }),
  chatReviewUpdate: (reviewId, status) =>
    request(`/chat-reviews/${reviewId}`, { method: "POST", body: { status }, auth: true }),

  // Technician workspace + manager oversight (backend/staff_tickets.py).
  staffTickets: ({ view, q, priority, status } = {}) => {
    const params = new URLSearchParams({ view: view || "unassigned" });
    if (q) params.set("q", q);
    if (priority) params.set("priority", priority);
    if (status) params.set("status", status);
    return request(`/staff/tickets?${params}`, { auth: true });
  },
  staffTicket: (ticketId) => request(`/staff/tickets/${ticketId}`, { auth: true }),
  staffClaim: (ticketId) =>
    request(`/staff/tickets/${ticketId}/claim`, { method: "POST", auth: true }),
  staffSetStatus: (ticketId, status) =>
    request(`/staff/tickets/${ticketId}/status`, { method: "POST", body: { status }, auth: true }),
  staffMessage: (ticketId, { body, visibility, subject, set_status }) =>
    request(`/staff/tickets/${ticketId}/messages`, {
      method: "POST",
      body: { body, visibility, subject: subject || null, set_status: set_status || null },
      auth: true,
    }),
  managerOverview: () => request("/manager/overview", { auth: true }),

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

  adminSimulateTicket: (ticket_text, channel, customer_id) =>
    request("/admin/simulate-ticket", {
      method: "POST",
      body: { ticket_text, channel, customer_id: customer_id || undefined },
      auth: true,
    }),

  debugRoutingPolicy: () => request("/debug/routing-policy"),
};
