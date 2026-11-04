import { cn } from "@/lib/utils";

export const fieldClass =
  "h-8 w-full min-w-0 rounded-md border border-input bg-sheet px-2.5 text-base text-fg shadow-none outline-none transition-colors " +
  "placeholder:text-muted-fg hover:border-fg/50 focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30 " +
  "disabled:cursor-not-allowed disabled:bg-raised disabled:text-muted-fg aria-invalid:border-negative";

export function Input({ className, type = "text", ...props }) {
  return <input data-slot="input" type={type} className={cn(fieldClass, className)} {...props} />;
}

export function Textarea({ className, ...props }) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(fieldClass, "h-auto min-h-20 py-2 leading-relaxed", className)}
      {...props}
    />
  );
}

/* A native <select>, styled: keyboard, screen readers and phones get the
 * platform control. */
export function Select({ className, children, ...props }) {
  return (
    <div className={cn("relative inline-flex w-full", className)}>
      <select data-slot="select" className={cn(fieldClass, "cursor-pointer appearance-none pr-8")} {...props}>
        {children}
      </select>
      <svg
        aria-hidden="true"
        viewBox="0 0 16 16"
        className="pointer-events-none absolute top-1/2 right-2.5 size-3.5 -translate-y-1/2 text-muted-fg"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
      >
        <path d="m4 6 4 4 4-4" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </div>
  );
}
