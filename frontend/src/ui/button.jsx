import { cva } from "class-variance-authority";
import { Slot } from "radix-ui";
import { cn } from "@/lib/utils";

/* shadcn/ui-style button. One filled (primary) action per screen; the rest
 * are outline, ghost or link. Focus ring on keyboard focus only. */
const buttonVariants = cva(
  "inline-flex shrink-0 cursor-pointer items-center justify-center gap-2 whitespace-nowrap rounded-md border text-base font-medium " +
    "transition-colors outline-none select-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 " +
    "focus-visible:ring-offset-sheet disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "border-primary bg-primary text-primary-fg hover:border-primary-hover hover:bg-primary-hover",
        destructive: "border-negative bg-negative text-white hover:border-negative-hover hover:bg-negative-hover dark:text-sheet",
        outline: "border-border bg-sheet text-fg hover:border-input hover:bg-raised",
        secondary: "border-transparent bg-active text-fg hover:bg-pill-neutral hover:border-transparent",
        ghost: "border-transparent bg-transparent text-fg hover:border-transparent hover:bg-active",
        link: "h-auto border-transparent bg-transparent p-0 text-primary underline-offset-4 hover:border-transparent hover:bg-transparent hover:underline",
      },
      size: {
        default: "h-8 px-3",
        sm: "h-7 px-2.5 text-sm",
        lg: "h-10 px-4",
        icon: "size-8 p-0",
        "icon-sm": "size-7 p-0",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export function Button({ className, variant, size, asChild = false, type, ...props }) {
  const Comp = asChild ? Slot.Root : "button";
  return (
    <Comp
      data-slot="button"
      type={asChild ? undefined : type || "button"}
      className={cn(buttonVariants({ variant, size }), className)}
      {...props}
    />
  );
}
