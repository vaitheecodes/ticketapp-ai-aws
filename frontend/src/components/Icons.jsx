// One small, hand-authored icon set (24x24, stroke-based, consistent
// weight) so the app has a real icon system instead of emoji/unicode
// glyphs standing in for one. Every icon is decorative — the visible
// text label next to it carries the accessible name — so each root
// <svg> is aria-hidden.

const base = {
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.75,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": "true",
  focusable: "false",
};

export function TicketIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M3 8.5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v1.75a1.75 1.75 0 0 0 0 3.5V15.5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-1.75a1.75 1.75 0 0 0 0-3.5z" />
      <path d="M14 7v10" strokeDasharray="2.2 2.6" />
    </svg>
  );
}

export function UserIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="8" r="3.5" />
      <path d="M4.5 20c1.2-3.6 4.2-5.5 7.5-5.5s6.3 1.9 7.5 5.5" />
    </svg>
  );
}

export function HeadsetIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M4 13.5v-2a8 8 0 0 1 16 0v2" />
      <rect x="3" y="13" width="4" height="6" rx="1.5" />
      <rect x="17" y="13" width="4" height="6" rx="1.5" />
      <path d="M19 19v.5a3 3 0 0 1-3 3h-2.5" />
    </svg>
  );
}

export function SettingsIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 3.5v2.4M12 18.1v2.4M20.5 12h-2.4M5.9 12H3.5M17.7 6.3l-1.7 1.7M8 16l-1.7 1.7M17.7 17.7 16 16M8 8 6.3 6.3" />
    </svg>
  );
}

export function ChevronRightIcon(props) {
  return (
    <svg {...base} strokeWidth={2} {...props}>
      <path d="M9 5l7 7-7 7" />
    </svg>
  );
}

export function MonitorIcon(props) {
  return (
    <svg {...base} {...props}>
      <rect x="3" y="4" width="18" height="12" rx="2" />
      <path d="M8 20h8M12 16v4" />
    </svg>
  );
}

export function SunIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </svg>
  );
}

export function MoonIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z" />
    </svg>
  );
}
