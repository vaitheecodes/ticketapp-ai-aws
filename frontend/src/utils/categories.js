/* Customer-facing labels for the 17 KB categories (backend graph.CATEGORIES).
 * Acronyms stay acronyms; everything else reads as a phrase. */
const LABELS = {
  ACCOUNT: "Account & licensing",
  AGENT: "Monitoring agent",
  ALERTS: "Alerts & notifications",
  APM: "APM",
  APPLICATION_MONITORING: "Application monitoring",
  CLOUD: "Cloud monitoring",
  CONTAINERS: "Containers",
  DATA: "Data & retention",
  INTEGRATION: "Integrations",
  LOGS: "Logs",
  MONITORING: "Monitors (general)",
  NETFLOW: "NetFlow",
  REPORTING: "Reports",
  SERVER_MONITORING: "Server monitoring",
  SNMP: "SNMP",
  SUPPORT: "General support",
  WEB_MONITORING: "Website monitoring",
};

export function categoryLabel(value) {
  if (!value) return "—";
  return LABELS[value] || value.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
}
