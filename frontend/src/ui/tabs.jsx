import { Tabs as T } from "radix-ui";
import { cn } from "@/lib/utils";

/* Underline tabs on a full-width rule; the active tab gets a 2px ink line. */
export const Tabs = T.Root;

export function TabsList({ className, ...props }) {
  return (
    <T.List
      className={cn("flex gap-5 overflow-x-auto border-b border-border [scrollbar-width:none]", className)}
      {...props}
    />
  );
}

export function TabsTrigger({ className, children, count, ...props }) {
  return (
    <T.Trigger
      className={cn(
        "relative -mb-px inline-flex h-9 shrink-0 cursor-pointer items-center gap-1.5 border-b-2 border-transparent px-0.5 text-base text-muted-fg outline-none transition-colors",
        "hover:text-fg focus-visible:ring-2 focus-visible:ring-ring focus-visible:rounded-sm data-[state=active]:border-fg data-[state=active]:font-medium data-[state=active]:text-fg",
        className,
      )}
      {...props}
    >
      {children}
      {count !== undefined && count !== null && (
        <span className="rounded-full bg-active px-1.5 text-xs tabular-nums text-muted-fg">{count}</span>
      )}
    </T.Trigger>
  );
}

export function TabsContent({ className, ...props }) {
  return <T.Content className={cn("outline-none", className)} {...props} />;
}
