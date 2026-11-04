import { cn } from "@/lib/utils";

/* Rules and alignment, no zebra and no header band. The header sticks to
 * the top of the scroll container; numbers go right-aligned via "num". */
export function Table({ className, wrapperClassName, ...props }) {
  return (
    <div data-slot="table-wrap" className={cn("relative w-full overflow-auto", wrapperClassName)}>
      <table data-slot="table" className={cn("w-full caption-bottom border-collapse text-base", className)} {...props} />
    </div>
  );
}

export function TableHeader({ className, ...props }) {
  return <thead data-slot="table-header" className={cn("sticky top-0 z-10 bg-sheet", className)} {...props} />;
}

export function TableBody({ className, ...props }) {
  return <tbody data-slot="table-body" className={cn("[&_tr:last-child]:border-0", className)} {...props} />;
}

export function TableRow({ className, ...props }) {
  return (
    <tr
      data-slot="table-row"
      className={cn(
        "border-b border-rule transition-colors hover:bg-raised data-[state=selected]:bg-active",
        className,
      )}
      {...props}
    />
  );
}

export function TableHead({ className, ...props }) {
  return (
    <th
      data-slot="table-head"
      className={cn(
        "h-9 whitespace-nowrap border-b border-border px-3 text-left align-middle text-sm font-medium text-muted-fg " +
          "[&.num]:text-right first:pl-5 last:pr-5",
        className,
      )}
      {...props}
    />
  );
}

export function TableCell({ className, ...props }) {
  return (
    <td
      data-slot="table-cell"
      className={cn("h-12 px-3 align-middle text-fg [&.num]:text-right first:pl-5 last:pr-5", className)}
      {...props}
    />
  );
}
