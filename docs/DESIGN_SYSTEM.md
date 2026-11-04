# ticketapp-ai design system (light + dark)

Status: spec, not yet applied. Scope: `frontend/src/styles/tokens.css`,
`components.css`, `portal.css`, `frontend/src/index.css`, plus a small theme
toggle and an `index.html` boot script. No new npm dependencies.

---

## 1. Direction

The new look is a neutral, graphite-and-paper operations console: a cool gray
ramp with no hue on surfaces, one restrained brand blue (`#1f5fcc` light,
`#2a65d4` dark), and color kept for meaning (status, priority, SLA, charts).
It reads as professional because it follows the conventions of mature support
tools (Zendesk, Atlassian, GitHub). Surfaces sit flat, separated by hairline
borders. Light mode adds a 1px shadow. Corners are tight (6px controls, 8px
cards), and status shows as small rectangular "lozenges", not glowing pills.
Most of the visual weight goes to the data, so a dense queue or admin table
stays easy to scan. The old theme looked AI-made for three reasons. It used
the default Tailwind indigo (`#6366f1`), the most recognizable "generated
SaaS" color. It sat on a blue-tinted near-black that copies Linear. And it
rounded everything into pills (12-14px cards, 999px chips, 999px badges).
The old theme also had real defects. It was dark-only. `--color-primary`
(indigo-500) was used as text in `index.css` at about 4.1:1, below AA, and
several amber values were hard-coded as `rgba(251,191,36,…)`. Fira Sans and
Fira Mono stay: they are not the cause of the generic look, they have proper
tabular figures, and keeping them avoids churn.

Status tone vocabulary (learn once, used everywhere):

| Tone | Hue | Ticket status | Chat | Route | Priority | SLA |
|---|---|---|---|---|---|---|
| `warn` | amber | open | escalated | human | – | at risk |
| `info` | blue | in_progress | – | – | medium | on track |
| `wait` (new) | plum | pending_customer | active | – | – | – |
| `ok` | green | resolved | resolved | – | – | met |
| `neutral` | gray | closed | abandoned | – | low | paused |
| `danger` | red | – | – | – | urgent (solid) | breached |
| `orange` (new, priority only) | orange | – | – | – | high | – |
| `accent` | brand blue | – | – | ai, clarify | – | – |

`pending_customer` moves from `accent` to the new `wait` tone, because brand
blue and `info` blue would otherwise collide with `in_progress`.
`components/ui/StatusChip.jsx` needs these changes: in `STATUS_TONE`,
`pending_customer` becomes `"wait"`; in `CHAT_STATUS`, `active` gets tone
`"wait"`. That is a single-word JSX edit each, allowed when the spec is
applied.

---

## 2. tokens.css: replacement for the `:root { … }` token block

Replace everything from the first `:root {` through its closing `}` (the
tokens block, lines ~20-127 today) with the following. The *second* `:root`
block (caret/scrollbar) and the base rules below it stay; see section 5 for
the edits there. Every existing property name is kept. New names are marked
`/* new */`.

Selector contract:

- `:root, :root[data-theme="light"]` → light (default when the OS is light or
  nothing is known).
- `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) }` →
  dark when the OS is dark and the user has not forced light.
- `:root[data-theme="dark"]` → dark, forced, wins on a light OS.

The dark values must be duplicated in both dark selectors (plain CSS has no
mixin). Keep the two copies identical. A comment marks them. All selectors
are on `<html>`, the same element where the `--color-*` aliases in
`index.css` are declared, so those aliases resolve per theme without changes.
Never set `data-theme` on a descendant element, because the aliases would
not re-resolve there.

```css
/* ===========================================================================
   Design tokens — ticketapp-ai (Site24x7-style help desk)
   ===========================================================================
   Two themes, one vocabulary. Light is the default; dark follows the OS;
   [data-theme="light"|"dark"] on <html> forces either (see ThemeToggle).
   Every text token clears WCAG AA 4.5:1 on every surface it may sit on, and
   every border/ring/chart token used as the only boundary clears 3:1 —
   ratios in docs/DESIGN_SYSTEM.md §3.

   Rule that survives from the dark-only era: accent-500/550/600/700 are
   FILLS. Accent TEXT and icons use accent-400 (which is dark blue in light
   mode and light blue in dark mode).
   ========================================================================= */

/* --- theme-independent: type, space, radius, motion, semantic aliases ---- */
:root {
  --font-sans: "Fira Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  --font-mono: "Fira Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace;

  --text-xs: 0.6875rem;  /* 11px — chips, table headers */
  --text-sm: 0.8125rem;  /* 13px — meta, labels, dense data */
  --text-base: 0.875rem; /* 14px — UI body */
  --text-md: 1rem;       /* 16px — prose, chat */
  --text-lg: 1.125rem;   /* 18px — section heading */
  --text-xl: 1.375rem;   /* 22px — page title */
  --text-2xl: 1.75rem;   /* 28px — KPI value, landing (was 30px) */

  --leading-tight: 1.25;
  --leading-snug: 1.45;
  --leading-relaxed: 1.6;

  --tracking-label: 0.04em;
  --tracking-tight: -0.011em;
  --tracking-title: -0.018em;

  --space-1: 0.25rem;
  --space-2: 0.5rem;
  --space-3: 0.75rem;
  --space-4: 1rem;
  --space-5: 1.25rem;
  --space-6: 1.5rem;
  --space-8: 2rem;
  --space-10: 2.5rem;
  --space-12: 3rem;
  --space-16: 4rem;

  /* Tighter than before: controls 6, cards 8, dialogs 10. Pills only for
     counters and the theme segmented control's track. */
  --radius-xs: 3px;
  --radius-sm: 4px;
  --radius-md: 6px;
  --radius-lg: 8px;
  --radius-xl: 10px;
  --radius-pill: 999px;

  --control-h-sm: 1.75rem;   /* new — 28px */
  --control-h-md: 2.125rem;  /* new — 34px */
  --control-h-lg: 2.5rem;    /* new — 40px (login, dialog primary) */

  --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  --dur-fast: 120ms;
  --dur: 180ms;

  /* Semantic aliases — resolve against whichever theme block is active
     (same element, so var() substitutes the themed value). */
  --priority-low: var(--status-neutral);          /* new */
  --priority-low-soft: var(--status-neutral-soft);/* new */
  --priority-medium: var(--status-info);          /* new */
  --priority-medium-soft: var(--status-info-soft);/* new */
  --priority-high: var(--status-orange);          /* new */
  --priority-high-soft: var(--status-orange-soft);/* new */
  --priority-urgent: var(--text-on-accent);       /* new — text on solid fill */
  --priority-urgent-soft: var(--danger-fill);     /* new — solid red lozenge */

  --sla-on-track: var(--status-info);             /* new */
  --sla-on-track-soft: var(--status-info-soft);   /* new */
  --sla-at-risk: var(--status-warn);              /* new */
  --sla-at-risk-soft: var(--status-warn-soft);    /* new */
  --sla-breached: var(--status-danger);           /* new */
  --sla-breached-soft: var(--status-danger-soft); /* new */
  --sla-met: var(--status-ok);                    /* new */
  --sla-met-soft: var(--status-ok-soft);          /* new */
  --sla-paused: var(--status-neutral);            /* new */
  --sla-paused-soft: var(--status-neutral-soft);  /* new */

  --focus-ring-width: 2px;                        /* new */
  --focus-ring-offset: 2px;                       /* new */
}

/* --- LIGHT (default) ----------------------------------------------------- */
:root,
:root[data-theme="light"] {
  color-scheme: light;

  /* surfaces — paper on a cool off-white page */
  --surface-page: #f5f6f8;
  --surface-raised: #ffffff;
  --surface-overlay: #eef0f3;      /* recessed/neutral fill: counts, segmented track, skeleton */
  --surface-float: #ffffff;        /* new — toasts, dialogs, menus (true floating layer) */
  --surface-input: #ffffff;
  --surface-hover: rgba(23, 27, 36, 0.04);
  --surface-active: rgba(23, 27, 36, 0.07);
  --header-bg: #ffffff;            /* new */
  --scrim: rgba(23, 27, 36, 0.45); /* new — dialog backdrop */

  /* borders */
  --border-subtle: #e3e6eb;        /* card / table hairlines (decorative) */
  --border-default: #d2d7de;       /* button outlines, dividers */
  --border-strong: #a3abb7;        /* emphasised dividers, scrollbar thumb */
  --border-input: #868f9d;         /* new — 3.27:1 on white: field boundary */
  --border-input-hover: #6b7482;   /* new */

  /* text */
  --text-primary: #171b24;
  --text-secondary: #4a5262;
  --text-tertiary: #5f6878;
  --text-on-accent: #ffffff;

  /* accent — restrained blue */
  --accent-300: #3d7be0;
  --accent-400: #1a5bc4;           /* accent TEXT + icons, links */
  --accent-500: #2563d6;           /* identity fills: active tab rail, dots, focus border */
  --accent-550: #1a52b3;           /* primary hover (darker in light) */
  --accent-600: #1f5fcc;           /* primary rest */
  --accent-700: #16469a;           /* primary pressed */
  --accent-soft: #e8effc;
  --accent-soft-strong: #d4e2f9;
  --accent-ring: rgba(37, 99, 214, 0.28); /* soft halo around focused inputs (decorative) */
  --focus-ring: #2563d6;           /* new — the 3:1 indicator itself */

  /* status (fg = text-safe on its soft AND on raised) */
  --status-ok: #17744a;
  --status-ok-soft: #e5f3ec;
  --status-warn: #875400;
  --status-warn-soft: #fcf1db;
  --status-info: #1b5bbf;
  --status-info-soft: #e6eefb;
  --status-neutral: #4f5868;
  --status-neutral-soft: #edeff3;
  --status-danger: #b3261e;
  --status-danger-soft: #fceceb;
  --status-wait: #8a3a7c;          /* new — pending customer, active chat */
  --status-wait-soft: #f6e9f3;     /* new */
  --status-orange: #a94a08;        /* new — priority high only */
  --status-orange-soft: #fcede2;   /* new */
  --danger-fill: #b3261e;          /* new — solid destructive button, urgent lozenge */
  --danger-fill-hover: #a12019;    /* new */
  --note-fill: var(--status-warn-soft);  /* new — internal note card */
  --note-border: #ecd29a;          /* new */
  --note-strong: #f5e2b5;          /* new — note avatar/tag/active mode fill */

  /* tables */
  --table-header-bg: #f5f6f8;      /* new */
  --table-zebra: #f9fafb;          /* new — optional, wide admin tables only */
  --table-row-hover: #eef2fa;      /* new */
  --table-row-selected: #e3ecfb;   /* new */

  /* charts — CVD-checked categorical set (see §3.3) */
  --chart-1: #3163c9;  /* blue   */
  --chart-2: #8a3208;  /* rust   */
  --chart-3: #08896a;  /* teal   */
  --chart-4: #b33188;  /* magenta*/
  --chart-5: #b97a0c;  /* ochre  */
  --chart-6: #43474f;  /* slate  */
  --chart-grid: #e3e6eb;
  --chart-axis: #868f9d;
  --chart-label: var(--text-tertiary);

  /* elevation — light mode gets a real (subtle) shadow scale */
  --shadow-1: 0 1px 2px rgba(23, 27, 36, 0.06);                                   /* new — cards, rows */
  --shadow-2: 0 1px 3px rgba(23, 27, 36, 0.08), 0 1px 2px rgba(23, 27, 36, 0.04); /* new — hover lift, KPI */
  --shadow-3: 0 12px 24px -6px rgba(23, 27, 36, 0.16), 0 4px 8px -4px rgba(23, 27, 36, 0.08); /* new */
  --shadow-overlay: var(--shadow-3);
  --shadow-sticky: 0 1px 0 var(--border-subtle), 0 6px 16px -12px rgba(23, 27, 36, 0.25);

  --selection-bg: #cfe0fb;         /* new */
}

/* --- DARK: OS preference (keep identical to the forced block below) ------ */
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;

    --surface-page: #0f1114;
    --surface-raised: #16191e;
    --surface-overlay: #1e2228;
    --surface-float: #1e2228;
    --surface-input: #121418;
    --surface-hover: rgba(255, 255, 255, 0.04);
    --surface-active: rgba(255, 255, 255, 0.07);
    --header-bg: #16191e;
    --scrim: rgba(0, 0, 0, 0.6);

    --border-subtle: #252930;
    --border-default: #30353e;
    --border-strong: #464c57;
    --border-input: #666e7b;
    --border-input-hover: #7c8491;

    --text-primary: #e7e9ed;
    --text-secondary: #aeb5c1;
    --text-tertiary: #8f97a4;
    --text-on-accent: #ffffff;

    --accent-300: #9cc0f8;
    --accent-400: #74a7f5;
    --accent-500: #4a8af0;
    --accent-550: #3170e0;
    --accent-600: #2a65d4;
    --accent-700: #2459bd;
    --accent-soft: #172540;
    --accent-soft-strong: #1e3156;
    --accent-ring: rgba(116, 167, 245, 0.35);
    --focus-ring: #74a7f5;

    --status-ok: #5cc493;
    --status-ok-soft: #15291f;
    --status-warn: #e3b54e;
    --status-warn-soft: #2b2312;
    --status-info: #74a7f5;
    --status-info-soft: #15233a;
    --status-neutral: #a2aab6;
    --status-neutral-soft: #23272e;
    --status-danger: #f2847b;
    --status-danger-soft: #331a19;
    --status-wait: #df92cd;
    --status-wait-soft: #2e1a2a;
    --status-orange: #f0995c;
    --status-orange-soft: #2f1e12;
    --danger-fill: #c4372d;
    --danger-fill-hover: #ad2f26;
    --note-fill: var(--status-warn-soft);
    --note-border: #5a4720;
    --note-strong: #3b2f15;

    --table-header-bg: #1b1f25;
    --table-zebra: #191c22;
    --table-row-hover: #1d2330;
    --table-row-selected: #1f2c45;

    --chart-1: #5b8def;
    --chart-2: #e0702e;
    --chart-3: #4fd1a8;
    --chart-4: #d68bc8;
    --chart-5: #d9c046;
    --chart-6: #7a828f;
    --chart-grid: #252930;
    --chart-axis: #666e7b;
    --chart-label: var(--text-tertiary);

    --shadow-1: none;
    --shadow-2: none;
    --shadow-3: 0 12px 32px -8px rgba(0, 0, 0, 0.65), 0 2px 8px -2px rgba(0, 0, 0, 0.4);
    --shadow-overlay: var(--shadow-3);
    --shadow-sticky: 0 1px 0 var(--border-subtle), 0 8px 24px -16px rgba(0, 0, 0, 0.8);

    --selection-bg: #2a3a57;
  }
}

/* --- DARK: forced (keep identical to the media block above) -------------- */
:root[data-theme="dark"] {
  color-scheme: dark;

  --surface-page: #0f1114;
  --surface-raised: #16191e;
  --surface-overlay: #1e2228;
  --surface-float: #1e2228;
  --surface-input: #121418;
  --surface-hover: rgba(255, 255, 255, 0.04);
  --surface-active: rgba(255, 255, 255, 0.07);
  --header-bg: #16191e;
  --scrim: rgba(0, 0, 0, 0.6);

  --border-subtle: #252930;
  --border-default: #30353e;
  --border-strong: #464c57;
  --border-input: #666e7b;
  --border-input-hover: #7c8491;

  --text-primary: #e7e9ed;
  --text-secondary: #aeb5c1;
  --text-tertiary: #8f97a4;
  --text-on-accent: #ffffff;

  --accent-300: #9cc0f8;
  --accent-400: #74a7f5;
  --accent-500: #4a8af0;
  --accent-550: #3170e0;
  --accent-600: #2a65d4;
  --accent-700: #2459bd;
  --accent-soft: #172540;
  --accent-soft-strong: #1e3156;
  --accent-ring: rgba(116, 167, 245, 0.35);
  --focus-ring: #74a7f5;

  --status-ok: #5cc493;
  --status-ok-soft: #15291f;
  --status-warn: #e3b54e;
  --status-warn-soft: #2b2312;
  --status-info: #74a7f5;
  --status-info-soft: #15233a;
  --status-neutral: #a2aab6;
  --status-neutral-soft: #23272e;
  --status-danger: #f2847b;
  --status-danger-soft: #331a19;
  --status-wait: #df92cd;
  --status-wait-soft: #2e1a2a;
  --status-orange: #f0995c;
  --status-orange-soft: #2f1e12;
  --danger-fill: #c4372d;
  --danger-fill-hover: #ad2f26;
  --note-fill: var(--status-warn-soft);
  --note-border: #5a4720;
  --note-strong: #3b2f15;

  --table-header-bg: #1b1f25;
  --table-zebra: #191c22;
  --table-row-hover: #1d2330;
  --table-row-selected: #1f2c45;

  --chart-1: #5b8def;
  --chart-2: #e0702e;
  --chart-3: #4fd1a8;
  --chart-4: #d68bc8;
  --chart-5: #d9c046;
  --chart-6: #7a828f;
  --chart-grid: #252930;
  --chart-axis: #666e7b;
  --chart-label: var(--text-tertiary);

  --shadow-1: none;
  --shadow-2: none;
  --shadow-3: 0 12px 32px -8px rgba(0, 0, 0, 0.65), 0 2px 8px -2px rgba(0, 0, 0, 0.4);
  --shadow-overlay: var(--shadow-3);
  --shadow-sticky: 0 1px 0 var(--border-subtle), 0 8px 24px -16px rgba(0, 0, 0, 0.8);

  --selection-bg: #2a3a57;
}
```

### Boot script (no flash of the wrong theme)

Put this in `frontend/index.html` `<head>`, before the module script. Also
replace `<meta name="theme-color" content="#0b0d12">` with the two
media-scoped metas below.

```html
<meta name="theme-color" media="(prefers-color-scheme: light)" content="#ffffff" />
<meta name="theme-color" media="(prefers-color-scheme: dark)" content="#16191e" />
<script>
  try {
    var t = localStorage.getItem("theme");
    if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
  } catch (e) {}
</script>
```

"System" means no `data-theme` attribute at all (remove the attribute rather
than setting `data-theme="system"`).

---

## 3. Contrast table

The ratios were computed with the WCAG 2.x relative-luminance formula by a
script, not estimated. Soft backgrounds are solid hexes, so the ratios below
are exact. AA: 4.5:1 for text, 3:1 for non-text and large text.

### 3.1 Text on surfaces

| Pair | Light | Dark |
|---|---|---|
| text-primary / page | 15.94 | 15.56 |
| text-primary / raised | 17.23 | 14.49 |
| text-primary / overlay (=float in dark) | 15.09 | 13.14 |
| text-primary / input | 17.23 | 15.17 |
| text-secondary / page | 7.26 | 9.17 |
| text-secondary / raised | 7.85 | 8.54 |
| text-secondary / overlay | 6.88 | 7.74 |
| text-tertiary / page | 5.20 | 6.42 |
| text-tertiary / raised | 5.62 | 5.98 |
| text-tertiary / overlay | 4.92 | 5.42 |
| text-tertiary / table-header-bg | 5.20 | 5.62 |
| text-tertiary / table-row-hover | 5.01 | 5.34 |
| text-tertiary / table-row-selected | 4.72 | 4.74 |
| text-secondary / note-fill (internal note) | 7.01 | 7.53 |
| accent-400 (link/accent text) / page | 5.82 | 7.73 |
| accent-400 / raised | 6.29 | 7.21 |
| accent-400 / overlay | 5.51 | 6.53 |
| accent-400 / accent-soft (accent chip) | 5.45 | 6.25 |

### 3.2 Fills with text, and status/priority/SLA lozenges

| Pair | Light | Dark |
|---|---|---|
| white / accent-600 (primary rest, customer chat bubble) | 5.89 | 5.37 |
| white / accent-550 (primary hover) | 7.24 | 4.64 |
| white / accent-700 (primary pressed) | 8.84 | 6.48 |
| white / danger-fill (destructive solid, urgent lozenge) | 6.54 | 5.34 |
| white / danger-fill-hover | 7.68 | 6.54 |
| status-ok / ok-soft (resolved, SLA met) | 5.05 | 7.14 |
| status-warn / warn-soft (open, SLA at risk) | 5.68 | 8.13 |
| status-info / info-soft (in progress, medium, SLA on track) | 5.46 | 6.44 |
| status-danger / danger-soft (SLA breached) | 5.71 | 6.42 |
| status-neutral / neutral-soft (closed, low, SLA paused) | 6.23 | 6.40 |
| status-wait / wait-soft (pending customer) | 6.00 | 7.04 |
| status-orange / orange-soft (priority high) | 5.00 | 7.16 |
| each status fg as plain text on raised | 5.72-7.17 | 7.02-9.22 |

### 3.3 Non-text (3:1)

| Pair | Light | Dark |
|---|---|---|
| border-input / raised (field boundary) | 3.27 | 3.42 |
| border-input / page | 3.02 | 3.67 |
| border-input-hover / raised | 4.72 | 4.67 |
| focus-ring / raised | 5.48 | 7.21 |
| focus-ring / page | 5.06 | 7.73 |
| accent-500 (focused input border, active tab rail) / raised | 5.48 | 5.19 |
| chart-axis / raised | 3.27 | 3.42 |
| chart-1 … chart-6 / raised (min) | 3.32 (chart-5) | 4.54 (chart-6) |
| chart-1 … chart-6 / page (min) | 3.32 | 4.54 |

Light chart min on raised: chart-1 5.17, chart-2 7.64, chart-3 4.05,
chart-4 5.23, chart-5 3.32, chart-6 8.62. Dark: 5.45, 5.48, 9.24, 7.04,
9.71, 4.54.

Color-vision check for the chart set: I simulated deuteranopia, protanopia
and tritanopia (Machado 2009 matrices) and took the minimum pairwise CIE76
ΔE across all six series. Light: normal 35.1, deutan 18.3, protan 26.7,
tritan 18.1. Dark: normal 41.8, deutan 16.4, protan 20.7, tritan 19.8. In
every case the minimum stays above ΔE 15, so every pair remains
distinguishable. The order matters: series 1+2 (blue/rust or orange) are
the safest pair, so a two-series chart uses those two.

`--border-subtle` and `--border-default` are below 3:1 on purpose. They are
decorative dividers only. Anything whose boundary carries meaning (inputs,
the checkbox outline, the segmented-control thumb) uses `--border-input` or
`--accent-500`.

---

## 4. Component guidance

**App header / nav.** Make the `.portal-header` a bar: `--header-bg`, a 1px
`--border-subtle` bottom border, a min height of 56px, and
`padding: var(--space-3) 0` inside the page width. In light mode add
`box-shadow: var(--shadow-sticky)` if it is made sticky. The left side holds
the product mark (existing SVG in `--accent-500`, 20px), then "Site24x7 Help
Desk" (`--text-base`, 600), a 1px × 16px `--border-default` divider, and the
portal title (`--text-base`, 500, `--text-secondary`). Do not use a 24px h1
in the bar; the page title lives in the content (`--text-xl`, 600). The role
badge becomes a neutral tag: 1px `--border-default` border, `--text-secondary`
text, `--radius-sm`, 11px, 600, uppercase, `--tracking-label`. It is not an
accent pill. The right side holds the theme toggle, then the signed-in
email (`--text-sm`, `--text-secondary`, hidden under 640px), then Log out
(ghost `btn-sm`).

**Tabs** (`.tabs`, `.view-tabs`, `.panel-switch`, `.filter-tabs`). Use
underline tabs. The container has a 1px `--border-subtle` bottom border.
Each tab button has height 36px, `padding: 0 var(--space-3)`, `--text-base`,
500, and `--text-secondary`. Hover changes the color to `--text-primary`
with no background. Active is `--text-primary` at weight 600 with a 2px
`--accent-500` bottom border overlapping the container border
(`margin-bottom:-1px`). Active tab text is never accent-colored. Counts
inside tabs use `.panel-count`: `--surface-overlay`, `--text-secondary`,
`--radius-pill` (the one allowed pill), 11px, tabular. `.filter-tabs` (All/Open/…) can
instead be a segmented control: a `--surface-overlay` track, `--radius-md`,
3px padding, and an active segment with `--surface-raised`, `--shadow-1`,
and `--text-primary`.

**Buttons** (`.btn`, plus legacy `button` and `.btn-primary` in index.css).

| Variant | Rest | Hover | Active |
|---|---|---|---|
| primary | bg/border `--accent-600`, text `--text-on-accent` | `--accent-550` | `--accent-700` |
| secondary | bg `--surface-raised`, border `--border-default`, text `--text-primary` | bg `--surface-overlay`, border `--border-strong` | bg `--surface-active` |
| ghost | transparent, `--text-secondary` | `--surface-hover`, `--text-primary` | `--surface-active` |
| danger (outline) | transparent, text `--status-danger`, border `--status-danger` at 40% mix | bg `--status-danger-soft` | – |
| danger-solid (new, `.btn-danger-solid`) | bg `--danger-fill`, white | `--danger-fill-hover` | – |

All variants use `--radius-md` (6px) and weight 500. There is no
translateY, no shadow, and no gradient. Sizes: `btn-sm` 28px high, 13px,
padding `0 10px`. `btn-md` 34px, 14px, padding `0 14px`. New `btn-lg` 40px,
15px, padding `0 18px`, used for login and dialog primary buttons. Icons are
16px at md and 14px at sm, with an 8px gap. Disabled is opacity 0.5 with
`cursor: not-allowed`. Use one primary per view region.

**Inputs / selects / textareas** (`.control`, legacy `input,select,textarea`).
Background `--surface-input`, 1px `--border-input`, `--radius-md`, height
`--control-h-md`, padding `0 10px`, 14px, `--text-primary`. Placeholder text
is `--text-tertiary`. Hover changes the border to `--border-input-hover`.
Focus sets the border to `--accent-500` plus
`box-shadow: 0 0 0 3px var(--accent-ring)`, with no outline. Invalid sets
the border to `--status-danger` and the ring to `--status-danger-soft`; the
error text goes below in `--status-danger`, 13px, and is linked with
`aria-describedby`. Disabled uses `--surface-overlay` background and
`--text-tertiary` text. The label always sits above the field
(`.field-label`: 13px, 500, `--text-secondary`) with a real
`<label htmlFor>`. Selects keep the custom chevron in `--text-tertiary`.
Checkboxes and radios use `accent-color: var(--accent-500)`.

**Tables** (`table`, `th`, `td`, `.tickets-table`, `.table-wrap`). The
wrapper has `--surface-raised`, a 1px `--border-subtle` border,
`--radius-lg`, and `--shadow-1`. Default density is a 40px row
(`padding: 8px 12px`, 13px body). A compact variant `.table-compact` uses
32px rows (`padding: 6px 10px`). Header cells use `--table-header-bg`, 11px,
600, uppercase, `--tracking-label`, `--text-tertiary`, a 1px
`--border-default` bottom border, and `position: sticky; top: 0` inside
scroll wrappers. Rows have a 1px `--border-subtle` divider. Hover uses
`--table-row-hover`, selected uses `--table-row-selected`, and the selected
row also gets a 2px `--accent-500` inset left marker
(`box-shadow: inset 2px 0 0 var(--accent-500)`). Zebra striping is off by
default; opt in with `.table-zebra tbody tr:nth-child(even)` using
`--table-zebra`, for the admin All Tickets and audit tables only. Numeric
columns (counts, durations, confidence, SLA remaining) get `.num`:
`text-align: right; font-variant-numeric: tabular-nums`, and the matching
`th` is right-aligned too. Ticket IDs use mono 12px in `--text-secondary`.
Long text truncates with an ellipsis and a `title` attribute; it never
wraps the row.

**Status / priority / SLA chips** (`.chip`, `.pill`, `.priority-chip`, new
`.sla-chip`). These are rectangular lozenges, not pills. Spec:
`border-radius: var(--radius-sm)` (4px), height 20px,
`padding: 0 6px`, 11px, weight 600, `line-height: 20px`. Text is in
sentence case; uppercase is reserved for table headers. The background is
the tone's `-soft` token and the text is the tone's fg token. The 6px
`.chip-dot` stays for status. Priority uses no dot; instead it prefixes a
tiny bar glyph (SVG, 1-4 bars) so priority is not conveyed by color alone.
Urgent is the one solid chip (`--danger-fill` background, white text).
SLA chips carry a 12px clock icon. The breached chip adds the remaining or
overdue time in tabular figures ("Breached · 2h 14m"). The paused chip uses
the neutral tone with a pause icon. Add new classes: `.chip-wait`,
`.chip-orange`, `.priority-low|medium|high|urgent`,
`.sla-on_track|at_risk|breached|met|paused` (underscore to match the
backend values).

**Cards / panels** (`.card`, `.row`, `.request-card`, `.review-card`,
`.ai-panel`, `.settings-section`). Use `--surface-raised`, a 1px
`--border-subtle` border, `--radius-lg` (8px), `--shadow-1` (none in dark),
and 20px padding (16px for dense lists). Panel heading: `--text-lg`, 600,
`--text-primary`, 12px below. A panel with its own toolbar puts the header
row on a 1px `--border-subtle` divider. Do not nest a card inside a card;
use a divider or `--surface-overlay` inset blocks (`--radius-md`) instead.
`.request-card.is-active` uses an `--accent-500` border plus a
`--accent-soft` background.

**KPI stat tiles** (`.stat-card`, admin dashboard). A card with 16px
padding laid out in this order:

1. The label (`--text-sm`, 500, `--text-secondary`, not uppercase).
2. The value (`--text-2xl` 28px, 600, `--text-primary`, tabular,
   `--tracking-title`).
3. A delta line (`--text-sm`): a 12px arrow SVG plus a percentage in
   `--status-ok` or `--status-danger` according to whether the change is
   good, not just whether it went up. For example, a rise in breached
   tickets shows in red.
4. Comparison text in `--text-tertiary`, such as "vs previous 7 days".

The grid is `repeat(auto-fill, minmax(12rem, 1fr))` with a 12px gap. Tiles
have no colored backgrounds and no icons in circles. An SLA tile may show a
4px progress bar: track `--surface-overlay`, fill using the SLA tone.

**Charts (hand-written SVG).**

- Text uses `font-family: var(--font-sans)`. Labels and ticks use
  `--chart-label`, 11px, tabular. Axis titles are 12px, 500,
  `--text-secondary`.
- Gridlines are horizontal only: 1px `--chart-grid` with
  `shape-rendering: crispEdges` and no dashes. The baseline/axis is 1px
  `--chart-axis`. There is no vertical axis line. Aim for 4-5 ticks at
  round values.
- Bars: fill with a series token, no stroke, top corners rounded at most
  2px (draw with `rx="2"` and clip the bottom, or keep them square). The
  band gap is 30-40% of the band. A value label sits above the bar only
  when there are 12 bars or fewer.
- Lines: `stroke-width: 2`, `stroke-linejoin: round`, `stroke-linecap: round`,
  `fill: none`, `vector-effect: non-scaling-stroke`. Show markers only on
  hover or focus and on the last point (r=3, filled, with a 2px
  `--surface-raised` stroke). Area fills, if used, are a series color at 8%
  opacity via `fill-opacity`.
- Colors: a single-series chart uses `--chart-1`. Multi-series charts use
  series 1-6 in order. Charts *of* statuses, priorities or SLA states use
  the semantic tokens, not the series. Label lines directly at their end
  when there are 3 or fewer; otherwise add a legend above the plot, with
  10px squares and 12px `--text-secondary` labels.
- Reference lines (SLA target) are 1px `--status-warn` with
  `stroke-dasharray: 4 3`, labelled at the right end.
- Every chart needs `role="img"` plus an `aria-label` sentence that states
  the key number, and a visually hidden data table or a "View as table"
  toggle. Colors are always read from CSS (`fill: var(--chart-1)` in a
  class), never hard-coded in JSX, so the chart switches with the theme.

**Empty states** (`.empty`, `.empty-compact`). These sit inside the
panel's own area, left-aligned in lists and centered only for a whole empty
page. They have an optional 24px outline icon in `--text-tertiary`, a title
(`--text-base`, 600, `--text-primary`) such as "No tickets in this view", a
one-line body (`--text-sm`, `--text-secondary`) that explains why or what
to do next, and an optional secondary button. Use 32px vertical padding.
There are no illustrations, no emoji, and no dashed boxes.

**Toasts** (`.toast`). Use `--surface-float`, a 1px `--border-default`
border, `--shadow-overlay`, `--radius-md`, a max width of 24rem, and 14px
`--text-primary` text. A 3px left rail shows the tone (`--status-ok`,
`--status-danger`, `--status-info`). Each toast has a 16px leading tone
icon and a ghost close button (28px, `aria-label="Dismiss"`). The position
stays bottom-right, with a 16px inset. Timing stays as implemented today: ok
4.5s, error 8s. Errors should use `role="alert"` and successes
`role="status"`.

**Modal / dialog (temporary-password reveal).**

- Use a native `<dialog>` opened with `showModal()`. Style
  `dialog::backdrop { background: var(--scrim) }`.
- Panel: `--surface-float`, a 1px `--border-default` border,
  `--radius-xl`, `--shadow-overlay`, width `min(28rem, 100vw - 32px)`,
  24px padding.
- Content: a title (`--text-lg`, 600, wired with `aria-labelledby`) and a
  body in `--text-secondary`. The password sits in a read-only field in
  `--surface-input` with a 1px `--border-input` border, mono 15px,
  `letter-spacing: 0.04em`, `user-select: all`, and an adjacent secondary
  "Copy" button. On success the button label changes to "Copied" and an
  `aria-live="polite"` message announces it.
- Show a `--status-warn` note with a warning icon: "Shown once. It can't be
  retrieved after you close this."
- The footer is right-aligned with one primary button: "I've copied it".
  Focus moves to the Copy button on open and returns to the trigger on
  close. Esc closes the dialog. Because the password can't be retrieved
  again, the warning note is the safeguard; there is no second "are you
  sure" prompt.

**Theme toggle.** Use three states: **System / Light / Dark**. System is
the default and must be reachable again after a manual pick, which a
two-state toggle cannot do.

- Markup: a segmented radiogroup in the header's right cluster (and
  top-right of the Login/ChangePassword pages):
  `<div role="radiogroup" aria-label="Color theme">` containing three
  `<button role="radio" aria-checked>` buttons. Each button is 28×28 with a
  16px hand-authored SVG (monitor, sun, moon) and a visible tooltip via
  `title`. The accessible name comes from a visually hidden `.sr-only` span
  ("System theme", "Light theme", "Dark theme").
- Keyboard: arrow keys move the selection, as in a radiogroup.
- Style: the track uses `--surface-overlay`, `--radius-md`, and 2px
  padding. The checked button uses `--surface-raised`, a 1px
  `--border-input` border, `--shadow-1`, and `--text-primary`. Unchecked
  buttons use `--text-tertiary`, and hover changes them to
  `--text-primary`.
- Behaviour: System removes `data-theme` and `localStorage.theme`. Light or
  Dark sets both. The component is roughly 40 lines in
  `components/ThemeToggle.jsx`, and its icons are added to `Icons.jsx`.

---

## 5. Prioritized CSS changes per file

Apply in this order. Steps 1-3 alone make light mode correct; the rest is
polish.

### P0: tokens.css

1. Replace the token `:root` block with section 2. The non-color tokens now
   sit in a theme-independent `:root`. `color-scheme: dark` is removed from
   it.
2. `::selection { background: var(--selection-bg); }` (was
   `--accent-soft-strong`).
3. `:focus-visible { outline: var(--focus-ring-width) solid var(--focus-ring); outline-offset: var(--focus-ring-offset); }`
4. `:root { caret-color: var(--accent-400); … }`. Leave this as is; it
   works in both themes.
5. Fix the header comment: remove the "Dark-committed … one theme" rationale.

### P0: index.css (alias layer + hard-coded colors)

1. **Delete** these lines in the `:root` alias block: `--radius-sm: 6px;
   --radius-md: 8px; --radius-lg: 12px; --shadow-sm: none; --shadow-md:
   none;`. They silently override tokens.css because index.css's `:root`
   block comes after the import. Then re-add `--shadow-sm: var(--shadow-1);
   --shadow-md: var(--shadow-2);`.
2. `--color-primary: var(--accent-400);`. It is used as *text* in
   `.hero-brand-icon`, `.link-button`, `.role-badge`, `.tabs button.active`
   and `.pill-ai`. Today it is indigo-500 at about 4.1:1 on the card, which
   fails AA. Set `--color-primary-hover: var(--accent-300);` and add
   `--color-primary-fill: var(--accent-600);` (new alias).
3. `.btn-primary` (l.155-168): `background/border-color:
   var(--accent-600)`, `color: var(--text-on-accent)` (was `white`). On
   hover use `var(--accent-550)`. Remove `transform: translateY(-1px)` and
   the `box-shadow` lines.
4. `.chat-bubble-customer` (l.620-624): `background: var(--accent-600);
   color: var(--text-on-accent);` (was `--color-primary` + `white`; white
   on dark accent-500 would be 3.40:1).
5. `input, textarea, select` (l.198-207): `border: 1px solid
   var(--border-input); background: var(--surface-input);`, plus
   `min-height: var(--control-h-md)` (not for textarea). Add `:hover` with
   `border-color: var(--border-input-hover)`.
6. `input:focus, …` (l.209-214): `border-color: var(--accent-500);
   box-shadow: 0 0 0 3px var(--accent-ring);`.
7. `button:focus-visible, .button:focus-visible, a:focus-visible` (l.219-223):
   `outline-color: var(--focus-ring)`.
8. `button, .button` (l.121-138): `border-color: var(--border-default);
   background: var(--surface-raised); border-radius: var(--radius-md);`.
   Hover sets `background: var(--surface-overlay)`. Drop `transform` from
   the transition list.
9. `.role-badge` (l.310-319): `background: transparent; border: 1px solid
   var(--border-default); color: var(--text-secondary); border-radius:
   var(--radius-sm);`.
10. `.tabs button:hover`: `background: none`. `.tabs button.active`:
    `color: var(--text-primary); border-bottom-color: var(--accent-500);`.
11. `.pill` (l.461+): `border-radius: var(--radius-sm)`; and
    `.pill-pending_customer { background: var(--status-wait-soft); color:
    var(--status-wait); }`, `.pill-ai { … color: var(--accent-400); }`,
    `.pill-closed { background: var(--status-neutral-soft); color:
    var(--status-neutral); }`.
12. Tables (l.494-522): `th { background: var(--table-header-bg); color:
    var(--text-tertiary); border-bottom-color: var(--border-default); }`,
    `tbody tr:hover { background: var(--table-row-hover); }`, and
    `th, td { padding: 0.5rem 0.75rem; }`. Add `.num`, `th.num` and
    `.table-zebra` as in section 4. `.tickets-table tbody tr.is-selected`
    uses `--table-row-selected`.
13. `.card` and `.ticket-item`: `box-shadow: var(--shadow-1)`.
    `.ticket-item:hover` keeps its color-mix (that is theme-safe).
14. `.portal-header`: the bar treatment from section 4. Set
    `.portal-header h1 { font-size: var(--text-base); font-weight: 600; }`.

### P1: components.css

1. `.btn.btn-secondary` (l.68-76): rest `background:
   var(--surface-raised)`. Hover (l.73-76): `background:
   var(--surface-overlay)`. Replace the hard-coded
   `color-mix(… white 4%)`, which is invisible in light mode.
2. `.btn.btn-danger` (l.89-97): keep. Add `.btn.btn-danger-solid { background:
   var(--danger-fill); border-color: var(--danger-fill); color:
   var(--text-on-accent); }` with hover `--danger-fill-hover`. Add `.btn-lg`
   (`min-height: var(--control-h-lg)`).
3. `.control` (~l.150-190): `border-color: var(--border-input)`. Hover
   (l.169) → `var(--border-input-hover)`. Focus (l.173-176): change the
   `background: var(--surface-page)` to `var(--surface-input)`, because the
   page color would grey the field in light mode, and set
   `box-shadow: 0 0 0 3px var(--accent-ring)`.
4. `.chip` (~l.228-236): `border-radius: var(--radius-sm); padding: 0
   0.375rem; height: 1.25rem; font-weight: 600;`. Add `.chip-wait { background:
   var(--status-wait-soft); color: var(--status-wait); }` and `.chip-orange`.
   `.chip-accent` already uses `--accent-400` and is correct in both themes.
5. `.row` (l.285+): add `box-shadow: var(--shadow-1)`. `.row.is-expanded`'s
   `color-mix(… var(--accent-500) 8%)` is theme-safe; keep it.
6. `.msg-customer .md code` (l.626-629): `rgba(0,0,0,.22)` /
   `rgba(255,255,255,.18)` sit on the always-blue bubble, so they are fine in
   both themes. Leave them and add a comment saying why.
7. `.msg-ai .msg-body` (l.516): add `box-shadow: var(--shadow-1)`.

### P1: portal.css

1. Internal-note amber, hard-coded for dark (`rgba(251,191,36,…)`):
   - l.1156 `.email-card-note { border-color: var(--note-border); background: var(--note-fill); }`
   - l.1164 `.email-card-note .email-avatar { background: var(--note-strong); }`
   - l.1173 `.private-tag { background: var(--note-strong); border-radius: var(--radius-sm); }`
   - l.1192 `.staff-composer.is-note { border-color: var(--note-border); background: var(--note-fill); }`
   - l.1222 `.is-note .composer-modes button.active { background: var(--note-strong); }`
2. `.priority-chip` (l.1055-1073): `border-radius: var(--radius-sm)`. Base
   (low/unknown) uses `--priority-low-soft`/`--priority-low`.
   `.priority-medium` uses the `--priority-medium*` tokens. `.priority-high`
   uses `--priority-high-soft`/`--priority-high` (orange, was warn amber,
   which collided with Open). `.priority-urgent { background:
   var(--danger-fill); color: var(--text-on-accent); }`.
3. `.toast` (l.910-923): `background: var(--surface-float); border: 1px solid
   var(--border-default);`. Add `.toast-info { border-left: 3px solid
   var(--status-info); }`.
4. `.view-tabs button`, `.panel-switch button`, `.filter-tabs button`
   (l.241-262, 395-414, 1006-1024): active state per section 4 (text
   `--text-primary`, rail `--accent-500`, no accent text, no hover
   background). `.panel-count` stays a pill.
5. `.stat-card` / `.stat-value` (l.1262-1276): `.stat-value { font-size:
   var(--text-2xl); font-weight: 600; letter-spacing: var(--tracking-title);
   font-variant-numeric: tabular-nums; }`. Add `.stat-delta`,
   `.stat-delta.is-good` (`--status-ok`), `.stat-delta.is-bad`
   (`--status-danger`), and `.stat-compare` (`--text-tertiary`).
6. `.confidence-high .confidence-bar span` (l.1313): keep `--accent-400`.
   It is a fill here and passes in both themes.
7. `.skeleton` (l.946+): the gradient `--surface-overlay → --border-default`
   works in both themes. Keep it.
8. `.request-card`, `.review-card`, `.email-card`, `.ai-panel`: add
   `box-shadow: var(--shadow-1)`, and set radius to `var(--radius-lg)`
   (already token-driven, so it shrinks automatically).
9. New blocks: `.sla-chip` + `.sla-on_track|at_risk|breached|met|paused`,
   chart classes (`.chart-grid line { stroke: var(--chart-grid) }`,
   `.chart-axis`, `.chart-label`, `.series-1 … .series-6 { fill/stroke }`),
   `.theme-toggle`, and `dialog.modal`, all per section 4.

### Hard-coded color inventory (would break or look wrong in light mode)

| Where | Value | Problem | Fix |
|---|---|---|---|
| `index.css` l.159 `.btn-primary` | `color: white` + `--color-primary` fill | dark accent-500 fill would be 3.40:1 | P0 #3 |
| `index.css` l.623 `.chat-bubble-customer` | `color: white` | same | P0 #4 |
| `index.css` l.36-40 alias block | `--radius-*`, `--shadow-sm/md: none` | overrides tokens; blocks light shadows | P0 #1 |
| `index.css` l.25 | `--color-primary: var(--accent-500)` used as text | fails AA today (~4.1:1) | P0 #2 |
| `components.css` l.74 | `color-mix(… white 4%)` | no hover feedback on white | P1 #1 |
| `components.css` l.174 | focus `background: var(--surface-page)` | greys field in light | P1 #3 |
| `components.css` l.627-628 | rgba black/white on bubble | OK (bubble is blue in both) | leave |
| `portal.css` l.1156, 1164, 1173, 1192, 1222 | `rgba(251,191,36,…)` | dark-tuned amber; muddy on white, off-palette | P1 #1 |
| `tokens.css` l.22 | `color-scheme: dark` | forces dark form controls/scrollbars | P0 #1 |
| `index.html` l.13 | `theme-color #0b0d12` | dark browser chrome in light mode | boot-script section |
| JSX inline styles | – | none set colors (only layout: `flex`, `margin`, `width`); `UiGallery.jsx` swatches use `var(--token)` and are fine; `IncidentsPanel.jsx` bar width only | none |
| `Icons.jsx` / page SVGs | `currentColor` throughout | theme-safe | none |

### P2: follow-ups (JSX, small)

- `StatusChip.jsx`: `pending_customer` → `"wait"`; chat `active` → `"wait"`.
  Add `PriorityChip` and `SlaChip` exports that use the new classes.
- `PortalHeader.jsx`: add the product mark, `ThemeToggle`, the user email,
  and a ghost Log out button (`className="btn btn-ghost btn-sm"`).
- `UiGallery.jsx`: add light/dark swatch rows for the new tokens, and a
  theme switch, so both themes can be checked on one page.
- Verify with `impeccable detect` and manually at 375/768/1440px × light,
  dark, and forced-each-way-against-the-OS (four combinations).
