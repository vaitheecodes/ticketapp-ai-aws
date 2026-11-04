import { clsx } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

// Our type scale adds "text-md" (styles/theme.css); without this,
// tailwind-merge would read it as a colour and drop a real colour class.
const twMerge = extendTailwindMerge({ extend: { theme: { text: ["md"], radius: ["sheet"] } } });

/* Class names, merged so a later Tailwind class overrides an earlier one
 * (the shadcn/ui convention every component in src/ui/ follows). */
export function cn(...inputs) {
  return twMerge(clsx(inputs));
}
