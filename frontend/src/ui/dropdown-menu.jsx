import { DropdownMenu as M } from "radix-ui";
import { cn } from "@/lib/utils";

export const DropdownMenu = M.Root;
export const DropdownMenuTrigger = M.Trigger;
export const DropdownMenuGroup = M.Group;
export const DropdownMenuRadioGroup = M.RadioGroup;

export function DropdownMenuContent({ className, sideOffset = 6, align = "end", ...props }) {
  return (
    <M.Portal>
      <M.Content
        sideOffset={sideOffset}
        align={align}
        className={cn(
          "z-50 min-w-48 overflow-hidden rounded-lg border border-border bg-float p-1 text-fg shadow-pop",
          className,
        )}
        {...props}
      />
    </M.Portal>
  );
}

const itemClass =
  "relative flex h-8 cursor-pointer select-none items-center gap-2 rounded-md px-2 text-base outline-none " +
  "data-[highlighted]:bg-active data-[disabled]:pointer-events-none data-[disabled]:opacity-50 [&_svg]:size-4 [&_svg]:text-muted-fg";

export function DropdownMenuItem({ className, ...props }) {
  return <M.Item className={cn(itemClass, className)} {...props} />;
}

export function DropdownMenuRadioItem({ className, children, ...props }) {
  return (
    <M.RadioItem className={cn(itemClass, "pr-8", className)} {...props}>
      {children}
      <M.ItemIndicator className="absolute right-2 inline-flex">
        <svg viewBox="0 0 16 16" className="size-4 text-primary" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
          <path d="m3.5 8.5 3 3 6-7" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </M.ItemIndicator>
    </M.RadioItem>
  );
}

export function DropdownMenuLabel({ className, ...props }) {
  return <M.Label className={cn("px-2 py-1.5 text-sm text-muted-fg", className)} {...props} />;
}

export function DropdownMenuSeparator({ className, ...props }) {
  return <M.Separator className={cn("-mx-1 my-1 h-px bg-rule", className)} {...props} />;
}
