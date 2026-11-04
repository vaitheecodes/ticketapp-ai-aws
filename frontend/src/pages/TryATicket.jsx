import { useEffect, useId, useState } from "react";
import { FlaskConical, Play } from "lucide-react";
import { api, getRole } from "@/api";
import AppShell, { Page } from "@/layout/AppShell";
import { Alert } from "@/ui/alert";
import { Button } from "@/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/ui/card";
import { EmptyState } from "@/ui/empty-state";
import { Input, Select, Textarea } from "@/ui/input";
import { Field } from "@/ui/label";
import { Skeleton } from "@/ui/skeleton";
import { BOT_NAME } from "@/lib/people";
import { RouteBadge } from "@/ui/status";
import { categoryLabel } from "@/utils/categories";

/* Try a Ticket: run a message through the live pipeline without saving it,
 * to see how it would be classified and routed. Technicians and managers
 * (POST /api/try-a-ticket); not part of the admin console. */

const OUTCOME = {
  ai: ["positive", `${BOT_NAME} would answer it`],
  clarify: ["info", `${BOT_NAME} would ask a clarifying question`],
  human: ["warning", "It would go to the support team"],
};

function Fact({ label, children }) {
  return (
    <div className="flex flex-col gap-1">
      <dt className="text-sm text-muted-fg">{label}</dt>
      <dd className="m-0 text-lg font-medium text-fg">{children}</dd>
    </div>
  );
}

export default function TryATicket() {
  const [ticketText, setTicketText] = useState("");
  const [channel, setChannel] = useState("chat");
  const [customerId, setCustomerId] = useState("");
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [maxAttempts, setMaxAttempts] = useState(null);
  const ids = { text: useId(), channel: useId(), customer: useId() };

  useEffect(() => {
    api
      .routingPolicy()
      .then((p) => setMaxAttempts(p.max_clarification_attempts))
      .catch(() => {});
  }, []);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!ticketText.trim()) return;
    setSubmitting(true);
    setError("");
    setResult(null);
    try {
      setResult(await api.tryATicket(ticketText.trim(), channel, customerId.trim()));
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  const outcome = result && OUTCOME[result.route];

  return (
    <AppShell role={getRole()} title="Try a ticket">
      <Page lead="Run a message through the live pipeline (real AI calls) to see how it would be classified and routed. Nothing is saved and no customer sees it.">
        <div className="grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
          <Card>
            <CardHeader>
              <CardTitle>Message</CardTitle>
            </CardHeader>
            <CardContent>
              <form className="flex flex-col gap-4" onSubmit={handleSubmit}>
                <Field label="Ticket text" htmlFor={ids.text}>
                  <Textarea
                    id={ids.text}
                    rows={6}
                    placeholder="e.g. Our Linux server monitor stopped reporting data at 10:00 UTC"
                    value={ticketText}
                    onChange={(e) => setTicketText(e.target.value)}
                    required
                  />
                </Field>
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="Channel" htmlFor={ids.channel}>
                    <Select id={ids.channel} value={channel} onChange={(e) => setChannel(e.target.value)}>
                      <option value="chat">Chat</option>
                      <option value="email">Email</option>
                    </Select>
                  </Field>
                  <Field label="Customer ID (optional)" htmlFor={ids.customer}>
                    <Input id={ids.customer} value={customerId} onChange={(e) => setCustomerId(e.target.value)} />
                  </Field>
                </div>
                <div>
                  <Button type="submit" disabled={submitting || !ticketText.trim()}>
                    <Play aria-hidden="true" />
                    {submitting ? "Running…" : "Run pipeline"}
                  </Button>
                </div>
                {error && <Alert tone="negative">{error}</Alert>}
              </form>
            </CardContent>
          </Card>

          <Card aria-live="polite">
            <CardHeader>
              <CardTitle>Trace</CardTitle>
              {result && <RouteBadge route={result.route} />}
            </CardHeader>
            <CardContent>
              {submitting && (
                <div className="flex flex-col gap-4" role="status" aria-label="Running">
                  <div className="grid grid-cols-3 gap-4">
                    {[0, 1, 2].map((i) => (
                      <Skeleton key={i} className="h-12" />
                    ))}
                  </div>
                  <Skeleton className="h-4 w-3/4" />
                  <Skeleton className="h-20" />
                </div>
              )}
              {!submitting && !result && (
                <EmptyState icon={FlaskConical} title="No run yet">
                  The category, confidence, route and the reply the bot would send appear here.
                </EmptyState>
              )}
              {result && (
                <div className="flex flex-col gap-5">
                  <dl className="m-0 grid grid-cols-2 gap-4 sm:grid-cols-3">
                    <Fact label="Category">{categoryLabel(result.category)}</Fact>
                    <Fact label="Confidence">
                      <span className="tabular-nums">{(result.confidence * 100).toFixed(0)}%</span>
                    </Fact>
                    <Fact label="Language tags">
                      <span className="text-base">
                        {result.language_tags?.length ? result.language_tags.join(", ") : "None"}
                      </span>
                    </Fact>
                  </dl>
                  <div className="text-base">
                    <span className="text-muted-fg">Route reason: </span>
                    {result.route_reason}
                  </div>
                  {outcome && (
                    <Alert tone={outcome[0]} title={outcome[1]}>
                      {result.route === "clarify" && (
                        <p className="m-0 mb-1 text-sm">
                          Attempt {result.clarification_attempts}
                          {maxAttempts != null ? ` of ${maxAttempts}` : ""}
                        </p>
                      )}
                      {result.ai_response ? (
                        <p className="m-0 whitespace-pre-line">{result.ai_response}</p>
                      ) : (
                        result.route === "human" && (
                          <p className="m-0">Technicians see the ticket text, not this category or confidence.</p>
                        )
                      )}
                    </Alert>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </Page>
    </AppShell>
  );
}
