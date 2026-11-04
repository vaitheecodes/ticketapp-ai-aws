import {
  Activity,
  BookOpen,
  Building2,
  ChartColumn,
  FlaskConical,
  Inbox,
  LifeBuoy,
  MessageSquareText,
  MessagesSquare,
  Radar,
  Settings2,
  Siren,
  Ticket,
  Timer,
  Users,
} from "lucide-react";

/* The sidebar for each role: groups of links to whole screens (never
 * anchors into one page). The backend enforces the same boundaries: a
 * link missing here is a 403 there (tests/test_role_scope.py). */

const RESOURCES = {
  label: "Resources",
  items: [
    { to: "/docs", label: "Docs", icon: BookOpen },
    { to: "/incidents", label: "Service history", icon: Radar },
  ],
};

export const NAV = {
  customer: {
    scope: "Customer support",
    groups: [
      {
        label: "Support",
        items: [
          { to: "/customer", label: "Get help", icon: LifeBuoy, end: true },
          { to: "/customer/tickets", label: "My tickets", icon: Ticket },
          { to: "/customer/chats", label: "Chats", icon: MessagesSquare },
        ],
      },
      { label: "Resources", items: [{ to: "/incidents", label: "Service history", icon: Radar }] },
    ],
  },
  technician: {
    scope: "Technician workspace",
    groups: [
      {
        label: "Work",
        items: [
          { to: "/technician", label: "Tickets", icon: Inbox, end: true },
          { to: "/technician/chat-reviews", label: "Chat reviews", icon: MessageSquareText },
          { to: "/technician/incidents", label: "Assigned incidents", icon: Siren },
        ],
      },
      { label: "Tools", items: [{ to: "/try-a-ticket", label: "Try a ticket", icon: FlaskConical }] },
      RESOURCES,
    ],
  },
  manager: {
    scope: "Manager",
    groups: [
      {
        label: "Oversight",
        items: [
          { to: "/manager", label: "Incidents", icon: Siren, end: true },
          { to: "/manager/tickets", label: "Department tickets", icon: Inbox },
          { to: "/manager/chat-reviews", label: "Chat reviews", icon: MessageSquareText },
        ],
      },
      { label: "Tools", items: [{ to: "/try-a-ticket", label: "Try a ticket", icon: FlaskConical }] },
      RESOURCES,
    ],
  },
  admin: {
    scope: "Admin console",
    groups: [
      {
        label: "Overview",
        items: [
          { to: "/admin", label: "Dashboard", icon: ChartColumn, end: true },
          { to: "/admin/tickets", label: "All tickets", icon: Ticket },
          { to: "/admin/sla", label: "SLA", icon: Timer },
        ],
      },
      {
        label: "Organization",
        items: [
          { to: "/admin/users", label: "Users", icon: Users },
          { to: "/admin/departments", label: "Departments", icon: Building2 },
        ],
      },
      {
        label: "System",
        items: [
          { to: "/admin/settings", label: "Settings", icon: Settings2 },
          { to: "/admin/activity", label: "Activity", icon: Activity },
        ],
      },
      RESOURCES,
    ],
  },
};

export const ROLE_LABEL = {
  customer: "Customer",
  technician: "Technician",
  manager: "Manager",
  admin: "Admin",
};

