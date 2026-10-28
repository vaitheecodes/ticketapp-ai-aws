"""Run the 10 key AI prompts against the real Bedrock model and report,
per prompt: OK/FAIL, model calls, rejected replies (invalid JSON or
schema), latency and tokens, plus a total cost estimate. Not a unit
test: it makes real (billed) calls.

    AWS_PROFILE=ticketapp .venv/bin/python scripts/bedrock_quality_check.py

Uses BEDROCK_MODEL_ID (default Nova Lite) and AWS_REGION. The prompts are
cases 1-10 of scripts/ai_smoke_test.py: categorise (clear, high-risk),
chat answer, clarifying question, continuation check, escalation summary,
suggested steps, incident correlation, incident summary, post-incident
draft. Each may be retried once after an invalid reply (the provider's
normal rule), so at most 20 calls are made; the script stops if that
would be exceeded.

Cost uses BEDROCK_PRICE_INPUT_PER_1M / BEDROCK_PRICE_OUTPUT_PER_1M (USD)
when set, else the Nova Lite list price; check the Bedrock pricing page.
Never prints credentials.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

import ai_smoke_test  # noqa: E402  (same folder; also puts the repo root on sys.path)
from backend.ai_service import AIError, provider  # noqa: E402
from backend.ai_service.backends import BedrockBackend  # noqa: E402

CASES = ai_smoke_test.CASES[:10]
MAX_CALLS = 2 * len(CASES)
# USD per 1M tokens (input, output), list prices.
PRICES = {"amazon.nova-lite-v1:0": (0.06, 0.24)}


class _Rejected(logging.Handler):
    """Counts replies the provider rejected (invalid JSON / schema, or an
    empty or oversized text reply), from its warning log lines."""

    count = 0

    def emit(self, record):
        message = record.getMessage()
        if "failed" in message and "validation" in message or "empty or too long" in message:
            _Rejected.count += 1


class _Budget(Exception):
    pass


class _Meter:
    """Counts calls and usage for the current prompt; enforces MAX_CALLS."""

    def __init__(self):
        self.total_calls = 0
        self.reset()

    def reset(self):
        self.calls, self.input, self.output, self.latency_ms, self.truncated = 0, 0, 0, 0, 0
        self.replies = []

    def __call__(self, usage):
        self.calls += 1
        self.input += usage.get("inputTokens") or 0
        self.output += usage.get("outputTokens") or 0
        self.latency_ms += usage.get("latencyMs") or 0
        self.truncated += usage.get("stopReason") == "max_tokens"


class _CappedBackend(BedrockBackend):
    def generate(self, *args, **kwargs):
        if self.meter.total_calls >= MAX_CALLS:
            raise _Budget(f"call limit of {MAX_CALLS} reached")
        self.meter.total_calls += 1
        text = super().generate(*args, **kwargs)
        self.meter.replies.append(text)
        return text


def prices(model_id: str) -> tuple[float, float] | None:
    if os.environ.get("BEDROCK_PRICE_INPUT_PER_1M") and os.environ.get("BEDROCK_PRICE_OUTPUT_PER_1M"):
        return float(os.environ["BEDROCK_PRICE_INPUT_PER_1M"]), float(os.environ["BEDROCK_PRICE_OUTPUT_PER_1M"])
    base = model_id.split(".", 1)[1] if model_id.split(".", 1)[0] in ("apac", "us", "eu", "global") else model_id
    return PRICES.get(base)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--show-output", action="store_true", help="print each parsed reply")
    parser.add_argument("--save-rejected", metavar="PATH",
                        help="write the raw replies of prompts that needed a retry or failed to this "
                             "JSON file (test prompts only; keep it out of the repo)")
    args = parser.parse_args()

    # Warnings feed the rejected-reply count; only errors (e.g. access denied) are printed.
    logging.basicConfig(level=logging.WARNING, format="  error: %(message)s", stream=sys.stdout)
    logging.getLogger().handlers[0].setLevel(logging.ERROR)
    logging.getLogger().addHandler(_Rejected())
    meter = _Meter()
    backend = _CappedBackend(on_usage=meter)
    backend.meter = meter
    provider.set_backend(backend)
    model = BedrockBackend.model_id()
    price = prices(model)
    print(f"model: {model}   region: {BedrockBackend.region()}   prompts: {len(CASES)}\n")

    header = f"{'prompt':<34} {'result':<6} {'calls':>5} {'rejected':>8} {'latency':>9} {'in tok':>7} {'out tok':>7}"
    print(header + "\n" + "-" * len(header))
    rows, outputs, rejected_raw = [], [], {}
    for name, run in CASES:
        meter.reset()
        _Rejected.count = 0
        start = time.time()
        try:
            out, status = run(), "OK"
        except AIError as exc:
            out, status = str(exc), "FAIL"
        except _Budget as exc:
            print(f"stopped: {exc}")
            break
        wall = time.time() - start
        rejected = _Rejected.count
        rows.append((status, meter.calls, rejected, wall, meter.input, meter.output, meter.truncated))
        print(f"{name:<34} {status:<6} {meter.calls:>5} {rejected:>8} {wall:>8.2f}s {meter.input:>7} {meter.output:>7}"
              + ("  (cut at max tokens)" if meter.truncated else ""))
        outputs.append((name, out.model_dump() if hasattr(out, "model_dump") else out))
        if rejected or status == "FAIL":
            rejected_raw[name] = [r[:4000] for r in meter.replies]

    ok = sum(r[0] == "OK" for r in rows)
    calls, rejected = sum(r[1] for r in rows), sum(r[2] for r in rows)
    tin, tout = sum(r[4] for r in rows), sum(r[5] for r in rows)
    latencies = sorted(r[3] for r in rows)
    print("-" * len(header))
    print(f"{'total':<34} {f'{ok}/{len(rows)}':<6} {calls:>5} {rejected:>8} {sum(latencies):>8.2f}s {tin:>7} {tout:>7}")
    if latencies:
        print(f"\nlatency per prompt: median {latencies[len(latencies) // 2]:.2f}s, max {latencies[-1]:.2f}s")
    print(f"prompts with a retry: {sum(r[1] > 1 for r in rows)}   failed after retry: {len(rows) - ok}")
    if price:
        cost = tin / 1e6 * price[0] + tout / 1e6 * price[1]
        print(f"estimated cost: ${cost:.6f}  (at ${price[0]}/1M input, ${price[1]}/1M output)")
        print(f"  per 1,000 runs of these prompts: ${cost * 1000:.2f}")
    else:
        print("estimated cost: unknown for this model (set BEDROCK_PRICE_INPUT_PER_1M / _OUTPUT_PER_1M)")

    if args.save_rejected and rejected_raw:
        with open(args.save_rejected, "w") as fh:
            json.dump(rejected_raw, fh, indent=2)
        print(f"raw replies of {len(rejected_raw)} prompt(s) written to {args.save_rejected}")

    if args.show_output:
        for name, out in outputs:
            text = out if isinstance(out, str) else json.dumps(out, indent=2, default=str)
            print(f"\n== {name}\n" + text[:2000])
    return 0 if ok == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
