import { initialsOf } from "@/lib/people";
import { cn } from "@/lib/utils";

/* Initials on a pale tint chosen from the name, so each person keeps the
 * same colour everywhere. Identity, not data: pale and never in charts. */
const HUES = [195, 215, 160, 30, 265, 340, 95, 5];

function hueFor(name = "") {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return HUES[h % HUES.length];
}

export function Avatar({ name, className, children }) {
  const hue = hueFor(name);
  return (
    <span
      aria-hidden="true"
      className={cn(
        "inline-flex size-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold",
        "bg-[oklch(0.93_0.04_var(--h))] text-[oklch(0.35_0.07_var(--h))] dark:bg-[oklch(0.32_0.05_var(--h))] dark:text-[oklch(0.9_0.05_var(--h))]",
        className,
      )}
      style={{ "--h": hue }}
    >
      {children || initialsOf(name)}
    </span>
  );
}
