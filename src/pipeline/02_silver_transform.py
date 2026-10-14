"""Silver stage: the core data engineering work.

Turns the raw bronze rows (flags / instruction / category / intent /
response) into two normalized, support-platform-shaped tables:

  tickets      - customer-facing ticket record
  resolutions  - agent/AI resolution record (1:1 with tickets, FK on ticket_id)

Everything synthetic here (timestamps, channel, customer_id, priority,
resolved_by, resolution_status, handle_time, csat) is generated with a
fixed random seed so the pipeline is reproducible, and is documented in
the README as a stand-in for real operational/CRM data a production
ingestion pipeline would receive from ticketing systems, telephony, and
survey tools rather than derive itself.
"""

import random
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

import config
import placeholder_fill as pf

SEED = 42


def decode_flags(flags: str) -> list:
    return [config.FLAG_LEGEND.get(ch, f"unknown_{ch}") for ch in flags]


def generate_created_at(n: int, rng: np.random.Generator, end_date: datetime) -> pd.Series:
    """Timestamps over the trailing 12 months, weighted toward weekdays and
    business hours (9am-6pm), the way real ticket-creation volume clusters."""
    day_offsets = rng.integers(0, 365, size=n)
    candidate_dates = [end_date - timedelta(days=int(o)) for o in day_offsets]
    weekday_mask = np.array([d.weekday() < 5 for d in candidate_dates])

    # Reject ~55% of weekend picks and resample from weekdays to bias the
    # distribution toward weekday volume without a hard cutoff.
    resample_mask = (~weekday_mask) & (rng.random(n) < 0.55)
    if resample_mask.any():
        resampled_offsets = rng.integers(0, 365, size=resample_mask.sum())
        weekday_offsets = day_offsets[weekday_mask] if weekday_mask.any() else day_offsets
        for idx, off in zip(np.where(resample_mask)[0], resampled_offsets):
            day_offsets[idx] = rng.choice(weekday_offsets) if len(weekday_offsets) else off

    business_hours_mask = rng.random(n) < 0.72
    hours = np.where(
        business_hours_mask,
        rng.integers(9, 18, size=n),
        rng.integers(0, 24, size=n),
    )
    minutes = rng.integers(0, 60, size=n)
    seconds = rng.integers(0, 60, size=n)

    timestamps = [
        (end_date - timedelta(days=int(day_offsets[i])))
        .replace(hour=int(hours[i]), minute=int(minutes[i]), second=int(seconds[i]), microsecond=0)
        for i in range(n)
    ]
    return pd.Series(timestamps)


def generate_customer_ids(n: int, rng: np.random.Generator) -> pd.Series:
    """Draw from a smaller pool with a power-law skew so a minority of
    customers account for a disproportionate share of tickets (repeat
    contacts), rather than a uniform 1-ticket-per-customer distribution."""
    pool_size = max(1, n // 4)
    pool = [f"CUST-{i:06d}" for i in range(pool_size)]
    weights = rng.pareto(a=1.5, size=pool_size) + 0.1
    weights /= weights.sum()
    return pd.Series(rng.choice(pool, size=n, p=weights))


def generate_priority(category: pd.Series, intent: pd.Series, flags: pd.Series,
                       rng: random.Random) -> pd.Series:
    levels = config.PRIORITY_VALUES  # low, medium, high, urgent
    out = []
    for cat, intnt, flag in zip(category, intent, flags):
        if intnt in config.HIGH_PRIORITY_INTENTS:
            weights = [0.05, 0.25, 0.45, 0.25]
        elif intnt in config.AI_FRIENDLY_INTENTS:
            weights = [0.50, 0.35, 0.12, 0.03]
        else:
            weights = [0.25, 0.45, 0.25, 0.05]

        if "W" in flag:  # offensive-language flag: escalate priority
            weights = [weights[0] * 0.3, weights[1] * 0.6, weights[2] * 1.3, weights[3] * 2.0]
            total = sum(weights)
            weights = [w / total for w in weights]

        out.append(rng.choices(levels, weights=weights, k=1)[0])
    return pd.Series(out)


def generate_resolution_fields(intent: pd.Series, priority: pd.Series,
                                rng: random.Random, np_rng: np.random.Generator) -> pd.DataFrame:
    n = len(intent)
    resolved_by, status, handle_time, csat = [], [], [], []

    for intnt, prio in zip(intent, priority):
        if intnt in config.AI_FRIENDLY_INTENTS:
            ai_prob = 0.82
        elif intnt in config.HUMAN_LEANING_INTENTS:
            ai_prob = 0.12
        else:
            ai_prob = 0.50
        if prio in ("high", "urgent"):
            ai_prob *= 0.4
        ai_prob = min(max(ai_prob, 0.03), 0.95)

        is_ai = rng.random() < ai_prob
        resolved_by.append("ai" if is_ai else "human")

        escalation_bump = prio in ("high", "urgent")
        if is_ai:
            weights = [0.65, 0.30, 0.05] if escalation_bump else [0.85, 0.10, 0.05]
        else:
            weights = [0.55, 0.35, 0.10] if escalation_bump else [0.70, 0.20, 0.10]
        this_status = rng.choices(config.RESOLUTION_STATUS_VALUES, weights=weights, k=1)[0]
        status.append(this_status)

        if is_ai:
            mean_log, sigma, lo, hi = np.log(90), 0.5, 15, 600
        else:
            mean_log, sigma, lo, hi = np.log(420), 0.6, 60, 3600
        ht = float(np_rng.lognormal(mean=mean_log, sigma=sigma))
        if escalation_bump:
            ht *= 1.3
        handle_time.append(int(np.clip(ht, lo, hi)))

        if this_status == "pending":
            null_prob = 1.0  # no CSAT survey fires until a ticket resolves
        elif this_status == "escalated":
            null_prob = 0.35
        else:
            null_prob = 0.15
        if rng.random() < null_prob:
            csat.append(None)
        else:
            if this_status == "resolved":
                weights = [0.03, 0.05, 0.12, 0.35, 0.45]
            elif this_status == "escalated":
                weights = [0.15, 0.25, 0.30, 0.20, 0.10]
            else:
                weights = [0.10, 0.15, 0.30, 0.25, 0.20]
            csat.append(rng.choices([1, 2, 3, 4, 5], weights=weights, k=1)[0])

    return pd.DataFrame({
        "resolved_by": resolved_by,
        "resolution_status": status,
        "handle_time_seconds": handle_time,
        "csat_score": csat,
    })


def main() -> None:
    bronze_batches = sorted(config.BRONZE_DIR.glob("ingest_date=*/raw.parquet"))
    if not bronze_batches:
        sys.exit("No bronze batches found. Run 01_bronze_ingest.py first.")
    latest_batch = bronze_batches[-1]
    df = pd.read_parquet(latest_batch)
    print(f"[silver] reading {len(df):,} rows from {latest_batch}")

    rng_py = random.Random(SEED)
    rng_np = np.random.default_rng(SEED)

    n = len(df)
    end_date = datetime.now(timezone.utc).replace(tzinfo=None)

    # --- language tags -----------------------------------------------
    language_tags = df["flags"].apply(decode_flags)

    # --- entity extraction + synthetic value filling ------------------
    instruction_entities, response_entities = [], []
    instruction_text, response_text = [], []
    for instr, resp in zip(df["instruction"], df["response"]):
        instr_ph = pf.extract_placeholders(instr)
        resp_ph = pf.extract_placeholders(resp)
        all_ph = list(dict.fromkeys(instr_ph + resp_ph))
        value_map = pf.build_value_map(all_ph, rng_py)
        instruction_entities.append(instr_ph)
        response_entities.append(resp_ph)
        instruction_text.append(pf.fill_text(instr, value_map))
        response_text.append(pf.fill_text(resp, value_map))

    # --- ticket-stream metadata ----------------------------------------
    ticket_id = [f"TCK-{100000 + rid}" for rid in df["row_id"]]
    created_at = generate_created_at(n, rng_np, end_date)
    channel = pd.Series(rng_np.choice(config.CHANNELS, size=n, p=config.CHANNEL_WEIGHTS))
    customer_id = generate_customer_ids(n, rng_np)
    priority = generate_priority(df["category"], df["intent"], df["flags"], rng_py)

    # --- canonical KB entry mapping ---------------------------------
    # This dataset has only 386 unique instruction texts; ~98% of rows
    # are one of those 386 duplicated ~5x under different `flags`
    # values (a dataset-generation artifact, not repeat customer
    # contact the way Bitext's rare near-duplicate rows were). Rather
    # than flag these as "repeat contact" duplicates, each row is
    # tagged with the canonical KB entry (one of the 386 unique
    # instructions, pre-entity-fill) it was drawn from. This makes the
    # 386 canonical Q&A pairs the unit the eval track's held-out split
    # should partition on (split KB entries 80/20, then map tickets
    # back by kb_entry_id — never split on raw ticket rows, or
    # near-duplicate flag-variants of the same question land on both
    # sides of the split).
    canonical_instructions = sorted(df["instruction"].unique())
    kb_entry_id_by_instruction = {
        instr: f"KB-{i + 1:04d}" for i, instr in enumerate(canonical_instructions)
    }
    kb_entry_id = df["instruction"].map(kb_entry_id_by_instruction)

    # --- resolution metadata --------------------------------------------
    resolution_fields = generate_resolution_fields(df["intent"], priority, rng_py, rng_np)

    tickets = pd.DataFrame({
        "ticket_id": ticket_id,
        "created_at": created_at,
        "channel": channel,
        "customer_id": customer_id,
        "category": df["category"].values,
        "intent": df["intent"].values,
        "instruction_text": instruction_text,
        "instruction_entities": instruction_entities,
        "priority": priority.values,
        "language_tags": language_tags.values,
        "kb_entry_id": kb_entry_id.values,
    })

    resolutions = pd.DataFrame({
        "ticket_id": ticket_id,
        "response_text": response_text,
        "response_entities": response_entities,
        "resolved_by": resolution_fields["resolved_by"].values,
        "resolution_status": resolution_fields["resolution_status"].values,
        "handle_time_seconds": resolution_fields["handle_time_seconds"].values,
        "csat_score": resolution_fields["csat_score"].values,
    })

    config.SILVER_DIR.mkdir(parents=True, exist_ok=True)
    tickets_path = config.SILVER_DIR / "tickets.parquet"
    resolutions_path = config.SILVER_DIR / "resolutions.parquet"
    tickets.to_parquet(tickets_path, index=False)
    resolutions.to_parquet(resolutions_path, index=False)

    print(f"[silver] tickets -> {tickets_path} ({len(tickets):,} rows)")
    print(f"[silver] resolutions -> {resolutions_path} ({len(resolutions):,} rows)")
    print(f"[silver] canonical KB entries: {tickets['kb_entry_id'].nunique():,} "
          f"(vs {len(tickets):,} total ticket rows)")
    print("[silver] priority distribution:\n", tickets["priority"].value_counts())
    print("[silver] resolved_by distribution:\n", resolutions["resolved_by"].value_counts())
    print("[silver] resolution_status distribution:\n", resolutions["resolution_status"].value_counts())
    print("[silver] csat null rate:", resolutions["csat_score"].isna().mean().round(3))


if __name__ == "__main__":
    main()
