import { useEffect, useState } from "react";
import { ChevronLeft } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "@/api";
import PublicShell from "@/layout/PublicShell";
import { Alert } from "@/ui/alert";
import { Skeleton } from "@/ui/skeleton";
import { StatusBadge } from "@/ui/status";
import { categoryLabel } from "@/utils/categories";

/* The routing policy, public and read-only (GET /api/public/routing-policy,
 * the admin's saved values applied). Nobody signs in to read it. */

const STATUS_MEANING = [
  ["open", "With the support team, waiting for a technician (or the owner's next step)."],
  ["in_progress", "A technician has claimed it and is working on it."],
  ["pending_customer", "Waiting for the customer's reply. The SLA resolution clock is paused."],
  ["resolved", "Answered by the Support Bot or a technician. A customer reply reopens it."],
  ["closed", "Finished. A closed ticket takes no further replies."],
];

function Section({ id, title, children }) {
  return (
    <section id={id} className="scroll-mt-6 border-t border-rule pt-8">
      <h2 className="text-lg font-semibold text-fg">{title}</h2>
      <div className="mt-4">{children}</div>
    </section>
  );
}

function Row({ label, children }) {
  return (
    <div className="grid gap-1 py-3 sm:grid-cols-[16rem_minmax(0,1fr)] sm:gap-6">
      <dt className="text-base text-muted-fg">{label}</dt>
      <dd className="m-0 text-base text-fg">{children}</dd>
    </div>
  );
}

export default function RoutingPolicyDoc() {
  const [policy, setPolicy] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .routingPolicy()
      .then(setPolicy)
      .catch((err) => setError(err.message));
  }, []);

  const pct = (v) => `${Math.round(v * 100)}%`;

  return (
    <PublicShell title="Routing policy">
      <div className="mx-auto max-w-3xl px-4 py-10 md:px-8 md:py-14">
        <Link to="/docs" className="inline-flex items-center gap-1 text-sm text-muted-fg no-underline hover:text-fg">
          <ChevronLeft aria-hidden="true" className="size-4" />
          Docs
        </Link>
        <h1 className="mt-3 text-xl font-semibold text-fg">Routing policy</h1>
        <p className="mt-2 max-w-[65ch] text-md text-muted-fg">
          Every customer message is classified, then routed: the Support Bot answers it, asks one clarifying question, or
          hands it to the support team. The values below are read live from the backend; the admin sets them in Settings.
        </p>

        <div className="mt-10 flex flex-col gap-10">
          {error && <Alert tone="negative" title="The policy could not be loaded">{error}</Alert>}

          <Section id="thresholds" title="Thresholds">
            {!policy && !error ? (
              <div className="flex flex-col gap-3" role="status" aria-label="Loading">
                {[0, 1, 2, 3].map((i) => (
                  <Skeleton key={i} className="h-5" />
                ))}
              </div>
            ) : (
              policy && (
                <dl className="m-0 divide-y divide-rule">
                  <Row label="Base confidence bar">
                    <span className="tabular-nums">{pct(policy.confidence_threshold)}</span>
                  </Row>
                  <Row label="High-risk confidence bar">
                    <span className="tabular-nums">{pct(policy.high_risk_confidence_threshold)}</span>
                  </Row>
                  <Row label="High-risk categories">
                    {policy.high_risk_categories.map(categoryLabel).join(", ") || "None"}
                  </Row>
                  <Row label="Escalation language">{policy.escalation_language_tags.join(", ") || "None"}</Row>
                  <Row label="Clarifying questions">
                    Up to <span className="tabular-nums">{policy.max_clarification_attempts}</span> before a hand-off
                  </Row>
                </dl>
              )
            )}
          </Section>

          {policy && (
            <Section id="decision-order" title="Decision order">
              <p className="mb-4 text-base text-muted-fg">Checked top to bottom; the first rule that applies decides.</p>
              <ol className="m-0 flex list-none flex-col gap-3 p-0">
                {policy.decision_order.map((step, i) => (
                  <li key={step} className="grid grid-cols-[1.75rem_minmax(0,1fr)] gap-2 text-base">
                    <span className="text-sm font-medium tabular-nums text-muted-fg">{i + 1}.</span>
                    <span className="max-w-[68ch] text-fg">{step}</span>
                  </li>
                ))}
              </ol>
            </Section>
          )}

          {policy && (
            <Section id="categories" title="Categories">
              <p className="text-base text-fg">{policy.categories.map(categoryLabel).join(", ")}</p>
            </Section>
          )}

          <Section id="statuses" title="Ticket statuses">
            <dl className="m-0 divide-y divide-rule">
              {STATUS_MEANING.map(([key, meaning]) => (
                <Row key={key} label={<StatusBadge status={key} />}>
                  {meaning}
                </Row>
              ))}
            </dl>
          </Section>
        </div>
      </div>
    </PublicShell>
  );
}
