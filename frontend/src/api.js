const TOKEN_KEY = "ticketapp_token";
const ROLE_KEY = "ticketapp_role";

// Local dev falls through to Vite's /api proxy (vite.config.js -> :8000).
// Static Web Apps has no such proxy, so production is built with
// VITE_API_URL pointing at the App Service origin, e.g.
//   VITE_API_URL=https://<your-app>.azurewebsites.net/api
// Vite inlines import.meta.env at build time, so this is a build-time
// setting, not a runtime one.
const API_BASE = (import.meta.env.VITE_API_URL || "/api").replace(/\/+$/, "");

export function saveSession(token, role) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(ROLE_KEY, role);
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(ROLE_KEY);
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
  customerSignup: (name, email, password) =>
    request("/auth/customer/signup", { method: "POST", body: { name, email, password } }),
  customerLogin: (email, password) =>
    request("/auth/customer/login", { method: "POST", body: { email, password } }),
  agentSignup: (name, email, password) =>
    request("/auth/agent/signup", { method: "POST", body: { name, email, password } }),
  agentLogin: (email, password) =>
    request("/auth/agent/login", { method: "POST", body: { email, password } }),
  adminSignup: (name, email, password) =>
    request("/auth/admin/signup", { method: "POST", body: { name, email, password } }),
  adminLogin: (email, password) =>
    request("/auth/admin/login", { method: "POST", body: { email, password } }),

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
  adminAgents: () => request("/admin/agents", { auth: true }),
  adminReassignTicket: (ticketId, agentId) =>
    request(`/admin/tickets/${ticketId}/reassign`, {
      method: "POST",
      body: { agent_id: agentId || null },
      auth: true,
    }),
  adminSimulateTicket: (ticket_text, channel, customer_id) =>
    request("/admin/simulate-ticket", {
      method: "POST",
      body: { ticket_text, channel, customer_id: customer_id || undefined },
      auth: true,
    }),

  debugRoutingPolicy: () => request("/debug/routing-policy"),
};
