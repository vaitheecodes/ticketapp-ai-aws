import { useEffect, useState } from "react";
import { Menu } from "lucide-react";
import { NavLink } from "react-router-dom";
import { cn } from "@/lib/utils";
import { Button } from "@/ui/button";
import { Sheet, SheetContent, SheetTrigger } from "@/ui/sheet";
import { TooltipProvider } from "@/ui/tooltip";
import { NAV } from "./nav";
import ProductMark from "./ProductMark";
import ThemeMenu from "./ThemeMenu";
import UserMenu from "./UserMenu";

/* The framed sheet: one tinted frame holds the sidebar and the 48px top bar
 * with no rule between them; the page sits on a white sheet inset 8px from
 * the window's right and bottom edges (16px corners, 1px edge, no shadow).
 * Under 768px the frame folds away: the sidebar becomes a drawer and the
 * sheet becomes the screen. */

function Brand({ scope }) {
  return (
    <div className="flex h-12 items-center gap-2.5 px-4">
      <ProductMark />
      <div className="min-w-0 leading-tight">
        <div className="truncate text-base font-semibold text-fg">Help Desk</div>
        <div className="truncate text-xs text-muted-fg">{scope}</div>
      </div>
    </div>
  );
}

function SidebarNav({ role, counts = {}, onNavigate }) {
  const { groups } = NAV[role];
  return (
    <nav aria-label="Main" className="flex flex-col gap-5 px-2.5 pt-3 pb-6">
      {groups.map((group) => (
        <div key={group.label}>
          <div className="px-2.5 pb-1.5 text-xs font-medium text-muted-fg">{group.label}</div>
          <ul className="flex flex-col gap-0.5">
            {group.items.map(({ to, label, icon: Icon, end }) => (
              <li key={to}>
                <NavLink
                  to={to}
                  end={end}
                  onClick={onNavigate}
                  className={({ isActive }) =>
                    cn(
                      "flex h-8 items-center gap-2.5 rounded-md px-2.5 text-base text-muted-fg no-underline transition-colors outline-none",
                      "hover:bg-active/70 hover:text-fg focus-visible:ring-2 focus-visible:ring-ring",
                      isActive && "bg-active font-medium text-fg",
                    )
                  }
                >
                  <Icon aria-hidden="true" className="size-4 shrink-0" strokeWidth={1.75} />
                  <span className="flex-1 truncate">{label}</span>
                  {counts[to] ? (
                    <span className="rounded-full bg-sheet px-1.5 text-xs font-medium tabular-nums text-fg">
                      {counts[to]}
                    </span>
                  ) : null}
                </NavLink>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  );
}

export default function AppShell({ role, title, actions, counts, children, sheetClassName }) {
  const [open, setOpen] = useState(false);
  const { scope } = NAV[role];

  useEffect(() => {
    document.title = `${title} · Help Desk`;
  }, [title]);

  return (
    <TooltipProvider delayDuration={300}>
      <div className="min-h-dvh bg-frame text-fg md:grid md:h-dvh md:grid-cols-[228px_minmax(0,1fr)] md:grid-rows-[48px_minmax(0,1fr)]">
        <a
          href="#main"
          className="sr-only z-50 rounded-md bg-sheet px-3 py-2 focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
        >
          Skip to content
        </a>

        <aside className="hidden md:row-span-2 md:flex md:flex-col md:overflow-y-auto">
          <Brand scope={scope} />
          <SidebarNav role={role} counts={counts} />
        </aside>

        <header className="sticky top-0 z-30 flex h-12 items-center gap-2 bg-frame px-2 md:static md:pr-4 md:pl-0">
          <Sheet open={open} onOpenChange={setOpen}>
            <SheetTrigger asChild>
              <Button variant="ghost" size="icon" className="md:hidden" aria-label="Open menu">
                <Menu aria-hidden="true" />
              </Button>
            </SheetTrigger>
            <SheetContent title="Menu">
              <Brand scope={scope} />
              <div className="overflow-y-auto">
                <SidebarNav role={role} counts={counts} onNavigate={() => setOpen(false)} />
              </div>
            </SheetContent>
          </Sheet>
          <h1 className="min-w-0 flex-1 truncate text-md font-semibold">{title}</h1>
          {actions && <div className="hidden items-center gap-2 sm:flex">{actions}</div>}
          <ThemeMenu />
          <UserMenu role={role} />
        </header>

        <main
          id="main"
          tabIndex={-1}
          className={cn(
            "min-h-[calc(100dvh-3rem)] bg-sheet outline-none md:mr-2 md:mb-2 md:min-h-0 md:overflow-y-auto md:rounded-sheet md:border md:border-border",
            sheetClassName,
          )}
        >
          {actions && <div className="flex flex-wrap items-center gap-2 px-4 pt-4 sm:hidden">{actions}</div>}
          {children}
        </main>
      </div>
    </TooltipProvider>
  );
}

/* Standard page padding inside the sheet, with an optional lead line (the
 * screen's one finding or what the page is for) and a toolbar. */
export function Page({ lead, toolbar, className, children }) {
  return (
    <div className={cn("flex flex-col gap-6 p-4 md:p-6", className)}>
      {(lead || toolbar) && (
        <div className="flex flex-wrap items-center justify-between gap-3">
          {lead && <p className="max-w-[72ch] text-base text-muted-fg">{lead}</p>}
          {toolbar && <div className="flex flex-wrap items-center gap-2">{toolbar}</div>}
        </div>
      )}
      {children}
    </div>
  );
}
