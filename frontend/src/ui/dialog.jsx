import { Dialog as D } from "radix-ui";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

export const Dialog = D.Root;
export const DialogTrigger = D.Trigger;
export const DialogClose = D.Close;

export function DialogContent({ className, title, description, children, hideClose = false, ...props }) {
  return (
    <D.Portal>
      <D.Overlay className="fixed inset-0 z-40 bg-scrim" />
      <D.Content
        className={cn(
          "fixed top-1/2 left-1/2 z-50 w-[min(30rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-xl border border-border bg-float p-6 shadow-pop outline-none",
          className,
        )}
        {...props}
      >
        <D.Title className="text-lg font-semibold text-fg">{title}</D.Title>
        {description ? (
          <D.Description className="mt-1 text-base text-muted-fg">{description}</D.Description>
        ) : (
          <D.Description className="sr-only">{title}</D.Description>
        )}
        <div className="mt-4">{children}</div>
        {!hideClose && (
          <D.Close
            className="absolute top-4 right-4 inline-flex size-8 items-center justify-center rounded-md text-muted-fg hover:bg-active hover:text-fg focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
            aria-label="Close"
          >
            <X className="size-4" aria-hidden="true" />
          </D.Close>
        )}
      </D.Content>
    </D.Portal>
  );
}
