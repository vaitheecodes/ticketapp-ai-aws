import { cn } from "@/lib/utils";

/* An empty block keeps its frame and says what would fill it. */
export function EmptyState({ icon: Icon, title, children, action, className }) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-2 px-6 py-12 text-center", className)}>
      {Icon && <Icon aria-hidden="true" className="mb-1 size-5 text-muted-fg" strokeWidth={1.5} />}
      <p className="text-base font-medium text-fg">{title}</p>
      {children && <p className="max-w-sm text-sm text-muted-fg">{children}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
