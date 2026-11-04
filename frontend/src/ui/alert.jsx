import { CircleAlert, CircleCheck, Info, TriangleAlert } from "lucide-react";
import { cn } from "@/lib/utils";

const TONES = {
  info: ["bg-pill-info text-pill-info-fg", Info],
  positive: ["bg-pill-positive text-pill-positive-fg", CircleCheck],
  warning: ["bg-pill-warning text-pill-warning-fg", TriangleAlert],
  negative: ["bg-pill-negative text-pill-negative-fg", CircleAlert],
};

export function Alert({ tone = "info", title, children, className, action }) {
  const [classes, Icon] = TONES[tone];
  return (
    <div
      role={tone === "negative" ? "alert" : "status"}
      className={cn("flex items-start gap-3 rounded-lg px-4 py-3 text-base", classes, className)}
    >
      <Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0 flex-1">
        {title && <p className="font-medium">{title}</p>}
        {children && <div className={cn(title && "mt-0.5")}>{children}</div>}
      </div>
      {action}
    </div>
  );
}
