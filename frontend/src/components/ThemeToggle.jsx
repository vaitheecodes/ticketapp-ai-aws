import { useRef, useState } from "react";
import { getTheme, setTheme } from "../utils/theme";
import { MonitorIcon, MoonIcon, SunIcon } from "./Icons";

const OPTIONS = [
  ["system", "System theme", MonitorIcon],
  ["light", "Light theme", SunIcon],
  ["dark", "Dark theme", MoonIcon],
];

/* System / Light / Dark as a radiogroup (arrow keys move the choice).
 * System stays reachable after a manual pick, which a two-way switch
 * can't offer. */
export default function ThemeToggle() {
  const [theme, setThemeState] = useState(getTheme);
  const refs = useRef([]);

  function choose(value) {
    setTheme(value);
    setThemeState(value);
  }

  function onKeyDown(e, index) {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    if (!step) return;
    e.preventDefault();
    const next = (index + step + OPTIONS.length) % OPTIONS.length;
    choose(OPTIONS[next][0]);
    refs.current[next]?.focus();
  }

  return (
    <div className="theme-toggle" role="radiogroup" aria-label="Color theme">
      {OPTIONS.map(([value, label, Icon], i) => (
        <button
          key={value}
          ref={(el) => (refs.current[i] = el)}
          type="button"
          role="radio"
          aria-checked={theme === value}
          tabIndex={theme === value ? 0 : -1}
          title={label}
          onClick={() => choose(value)}
          onKeyDown={(e) => onKeyDown(e, i)}
        >
          <Icon width="16" height="16" />
          <span className="sr-only">{label}</span>
        </button>
      ))}
    </div>
  );
}
