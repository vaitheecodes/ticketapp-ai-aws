import { cva } from "class-variance-authority";
import { cn } from "@/lib/utils";

/* Pale pill for one status or priority mark. Ink on tint >= 7:1 in both
 * themes; the dot is optional and repeats the tone for colour-blind users
 * only together with the label, never alone. */
const badgeVariants = cva(
  "inline-flex h-5 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full px-2 text-xs font-medium [&_svg]:size-3",
  {
    variants: {
      tone: {
        neutral: "bg-pill-neutral text-pill-neutral-fg",
        positive: "bg-pill-positive text-pill-positive-fg",
        negative: "bg-pill-negative text-pill-negative-fg",
        warning: "bg-pill-warning text-pill-warning-fg",
        info: "bg-pill-info text-pill-info-fg",
        accent: "bg-pill-accent text-pill-accent-fg",
        outline: "border border-border bg-transparent text-muted-fg",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

export function Badge({ className, tone, dot = false, children, ...props }) {
  return (
    <span data-slot="badge" className={cn(badgeVariants({ tone }), className)} {...props}>
      {dot && <span aria-hidden="true" className="size-1.5 rounded-full bg-current opacity-80" />}
      {children}
    </span>
  );
}
