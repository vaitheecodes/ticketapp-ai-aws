import { useEffect } from "react";
import { Link, NavLink } from "react-router-dom";
import { getRole, getToken, HOME_FOR_ROLE } from "@/api";
import { cn } from "@/lib/utils";
import { Button } from "@/ui/button";
import { TooltipProvider } from "@/ui/tooltip";
import ProductMark from "./ProductMark";
import ThemeMenu from "./ThemeMenu";

/* Pages anyone can open without signing in (docs, service history): the
 * same frame and sheet, with a top bar instead of a sidebar. */
const LINKS = [
  ["/docs", "Docs"],
  ["/incidents", "Service history"],
];

export default function PublicShell({ title, children }) {
  const home = getToken() ? HOME_FOR_ROLE[getRole()] : null;

  useEffect(() => {
    document.title = `${title} · Help Desk`;
  }, [title]);

  return (
    <TooltipProvider delayDuration={300}>
      <div className="flex min-h-dvh flex-col bg-frame text-fg">
        <header className="flex h-12 items-center gap-4 px-3 md:px-4">
          <Link to="/docs" className="flex items-center gap-2.5 text-fg no-underline">
            <ProductMark />
            <span className="hidden text-base font-semibold sm:inline">Help Desk</span>
          </Link>
          <nav aria-label="Public" className="flex items-center gap-1">
            {LINKS.map(([to, label]) => (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) =>
                  cn(
                    "rounded-md px-2.5 py-1.5 text-base text-muted-fg no-underline hover:bg-active hover:text-fg focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
                    isActive && "font-medium text-fg",
                  )
                }
              >
                {label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <ThemeMenu />
            <Button asChild variant="outline" size="sm">
              <Link to={home || "/login"} className="no-underline">
                {home ? "Open the app" : "Sign in"}
              </Link>
            </Button>
          </div>
        </header>
        <main id="main" className="flex-1 bg-sheet md:mx-2 md:mb-2 md:rounded-sheet md:border md:border-border">
          {children}
        </main>
      </div>
    </TooltipProvider>
  );
}
