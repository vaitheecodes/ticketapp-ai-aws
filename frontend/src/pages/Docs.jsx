import { ArrowRight, GitBranch, ListChecks, Radar } from "lucide-react";
import { Link } from "react-router-dom";
import PublicShell from "@/layout/PublicShell";

/* /docs: the policy pages anyone can read, no sign-in. */
const PAGES = [
  {
    to: "/docs/routing-policy",
    icon: GitBranch,
    title: "Routing policy",
    body: "When the Support Bot answers, asks one question, or hands a ticket to the support team: confidence bars, high-risk categories and escalation signals, read live from the backend.",
  },
  {
    to: "/docs/routing-policy#statuses",
    icon: ListChecks,
    title: "Ticket statuses",
    body: "What Open, In Progress, Pending Customer, Resolved and Closed mean, and who moves a ticket between them.",
  },
  {
    to: "/incidents",
    icon: Radar,
    title: "Service history",
    body: "Resolved incidents the support team has made public.",
  },
];

export default function Docs() {
  return (
    <PublicShell title="Docs">
      <div className="mx-auto max-w-3xl px-4 py-10 md:px-8 md:py-14">
        <h1 className="text-xl font-semibold text-fg">Docs</h1>
        <p className="mt-2 max-w-[65ch] text-md text-muted-fg">
          How the help desk handles a request, from the first message to resolution. These pages are public and read-only.
        </p>
        <ul className="mt-8 flex flex-col divide-y divide-rule border-y border-rule">
          {PAGES.map(({ to, icon: Icon, title, body }) => (
            <li key={title}>
              <Link
                to={to}
                className="group flex items-start gap-4 py-5 text-fg no-underline outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Icon aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-primary" strokeWidth={1.75} />
                <span className="min-w-0 flex-1">
                  <span className="block text-md font-medium group-hover:underline">{title}</span>
                  <span className="mt-1 block text-base text-muted-fg">{body}</span>
                </span>
                <ArrowRight aria-hidden="true" className="mt-1 size-4 shrink-0 text-muted-fg group-hover:text-fg" />
              </Link>
            </li>
          ))}
        </ul>
      </div>
    </PublicShell>
  );
}
