import { cn } from "@/lib/utils";

export function Skeleton({ className, ...props }) {
  return <div aria-hidden="true" className={cn("animate-pulse rounded-md bg-active", className)} {...props} />;
}

/* A table-shaped placeholder: n rows of a few bars. */
export function TableSkeleton({ rows = 6, columns = 5, label = "Loading" }) {
  return (
    <div role="status" aria-label={label} className="divide-y divide-rule">
      {Array.from({ length: rows }, (_, r) => (
        <div key={r} className="flex h-12 items-center gap-6 px-5">
          {Array.from({ length: columns }, (_, c) => (
            <Skeleton key={c} className={cn("h-3", c === 0 ? "w-40" : c === 1 ? "flex-1" : "w-16")} />
          ))}
        </div>
      ))}
    </div>
  );
}
