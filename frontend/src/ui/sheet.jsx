import { Dialog as D } from "radix-ui";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

/* A side drawer (the phone navigation). Radix Dialog: focus is trapped and
 * Escape closes it. */
export const Sheet = D.Root;
export const SheetTrigger = D.Trigger;
export const SheetClose = D.Close;

export function SheetContent({ className, side = "left", title, children, ...props }) {
  return (
    <D.Portal>
      <D.Overlay className="fixed inset-0 z-40 bg-scrim" />
      <D.Content
        className={cn(
          "fixed top-0 bottom-0 z-50 flex w-[18rem] max-w-[85vw] flex-col bg-frame shadow-pop outline-none",
          side === "left" ? "left-0" : "right-0",
          className,
        )}
        {...props}
      >
        <D.Title className="sr-only">{title}</D.Title>
        <D.Description className="sr-only">{title}</D.Description>
        {children}
        <D.Close
          className="absolute top-2.5 right-2.5 inline-flex size-8 items-center justify-center rounded-md text-muted-fg hover:bg-active hover:text-fg focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          aria-label="Close menu"
        >
          <X className="size-4" aria-hidden="true" />
        </D.Close>
      </D.Content>
    </D.Portal>
  );
}
