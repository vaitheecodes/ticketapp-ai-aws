import { cn } from "@/lib/utils";

/* A panel on the sheet: 1px edge, 8px corners, no shadow at rest. */
export function Card({ className, as: Comp = "section", ...props }) {
  return <Comp data-slot="card" className={cn("rounded-lg border border-border bg-sheet", className)} {...props} />;
}

export function CardHeader({ className, ...props }) {
  return (
    <div data-slot="card-header" className={cn("flex items-start justify-between gap-3 px-5 pt-4 pb-3", className)} {...props} />
  );
}

export function CardTitle({ className, as: Comp = "h2", ...props }) {
  return <Comp data-slot="card-title" className={cn("text-md font-semibold text-fg", className)} {...props} />;
}

export function CardDescription({ className, ...props }) {
  return <p data-slot="card-description" className={cn("mt-0.5 text-sm text-muted-fg", className)} {...props} />;
}

export function CardContent({ className, ...props }) {
  return <div data-slot="card-content" className={cn("px-5 pb-5", className)} {...props} />;
}

export function CardFooter({ className, ...props }) {
  return (
    <div data-slot="card-footer" className={cn("flex items-center gap-2 border-t border-rule px-5 py-3", className)} {...props} />
  );
}
