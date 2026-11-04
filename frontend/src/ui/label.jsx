import { cn } from "@/lib/utils";

export function Label({ className, ...props }) {
  return <label data-slot="label" className={cn("text-sm font-medium text-fg", className)} {...props} />;
}

/* Label, control, then a hint or an error under it. */
export function Field({ label, htmlFor, hint, error, className, children }) {
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      {label && <Label htmlFor={htmlFor}>{label}</Label>}
      {children}
      {error ? (
        <p className="text-sm text-negative" role="alert">
          {error}
        </p>
      ) : (
        hint && <p className="text-sm text-muted-fg">{hint}</p>
      )}
    </div>
  );
}
