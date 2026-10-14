# Databricks notebook source
# MAGIC %md
# MAGIC # Silver transform — PySpark / Databricks port
# MAGIC
# MAGIC Secondary artifact demonstrating PySpark/Databricks skills. The
# MAGIC **primary, actually-run pipeline is the local pandas/DuckDB version**
# MAGIC under `src/pipeline/` — this notebook reimplements the same silver-stage
# MAGIC logic (flag decoding, entity-placeholder filling, synthetic ticket +
# MAGIC resolution metadata, duplicate detection) as Spark DataFrame
# MAGIC transformations, so it scales to a distributed cluster instead of a
# MAGIC single pandas process.
# MAGIC
# MAGIC ### How to run on Databricks Community Edition (free tier)
# MAGIC 1. Create a cluster (Community Edition gives you one, single-node,
# MAGIC    already has PySpark + Faker-installable via `%pip install faker`).
# MAGIC 2. Upload `Bitext_Sample_Customer_Support_Training_Dataset_27K_responses-v11.csv`
# MAGIC    via **Data > Add Data > Upload File**, which lands it under
# MAGIC    `/FileStore/tables/...` on DBFS. Update `RAW_PATH` below to match.
# MAGIC 3. Import this file as a notebook (**Workspace > Import**, source
# MAGIC    format "Python" — the `# Databricks notebook source` header lets
# MAGIC    Databricks parse the `# COMMAND ----------` cell markers below).
# MAGIC 4. Run all cells. Output silver tables land under `/FileStore/silver/`
# MAGIC    on DBFS as Parquet.

# COMMAND ----------

# MAGIC %pip install faker

# COMMAND ----------

import random
import re
from datetime import datetime, timedelta

from pyspark.sql import functions as F
from pyspark.sql import types as T
from faker import Faker

RAW_PATH = "dbfs:/FileStore/tables/Bitext_Sample_Customer_Support_Training_Dataset_27K_responses_v11.csv"
SILVER_TICKETS_PATH = "dbfs:/FileStore/silver/tickets"
SILVER_RESOLUTIONS_PATH = "dbfs:/FileStore/silver/resolutions"

SEED = 42

# COMMAND ----------

# MAGIC %md ### Reference data (mirrors src/pipeline/config.py)

# COMMAND ----------

FLAG_LEGEND = {
    "M": "morphological_variation", "L": "semantic_variation", "B": "basic_syntax",
    "I": "interrogative", "C": "coordinated_syntax", "N": "negation", "P": "politeness",
    "Q": "colloquial", "W": "offensive_language", "K": "keyword_mode", "E": "abbreviation",
    "Z": "typo_error", "V": "variation_other", "S": "spelling_variation",
}

AI_FRIENDLY_INTENTS = {
    "track_order", "track_refund", "check_invoice", "check_payment_methods",
    "delivery_options", "delivery_period", "check_refund_policy",
    "check_cancellation_fee", "newsletter_subscription", "recover_password",
}
HUMAN_LEANING_INTENTS = {
    "complaint", "contact_human_agent", "cancel_order", "payment_issue",
    "delete_account", "registration_problems",
}
HIGH_PRIORITY_INTENTS = {"complaint", "payment_issue", "cancel_order", "delete_account"}

CHANNELS = ["email", "chat", "voice"]
CHANNEL_WEIGHTS = [0.45, 0.40, 0.15]
PRIORITY_VALUES = ["low", "medium", "high", "urgent"]

# COMMAND ----------

# MAGIC %md ### Load bronze (raw CSV, as-is + lineage columns)

# COMMAND ----------

raw_df = (
    spark.read.option("header", True).csv(RAW_PATH)
    .withColumn("row_id", F.monotonically_increasing_id())
    .withColumn("source_file", F.lit(RAW_PATH))
    .withColumn("ingested_at", F.lit(datetime.utcnow().isoformat()))
)
display(raw_df.limit(5))

# COMMAND ----------

# MAGIC %md ### Flag decoding + entity placeholder extraction/filling
# MAGIC
# MAGIC Same pattern-based classify-then-generate approach as the local
# MAGIC pipeline's `placeholder_fill.py`: rather than hand-writing a generator
# MAGIC for each of the 391 distinct `{{...}}` placeholders, classify by
# MAGIC keyword in the placeholder name and fall back to the placeholder's own
# MAGIC label text (correct for UI-label placeholders like `{{Settings}}`).

# COMMAND ----------

PLACEHOLDER_RE = re.compile(r"\{\{([^{}]+)\}\}")


def decode_flags(flags):
    if flags is None:
        return []
    return [FLAG_LEGEND.get(ch, f"unknown_{ch}") for ch in flags]


def extract_placeholders(text):
    if text is None:
        return []
    seen = {}
    for m in PLACEHOLDER_RE.findall(text):
        name = m.strip()
        seen.setdefault(name, None)
    return list(seen.keys())


def classify(name):
    n = name.lower()
    if "email" in n:
        return "email"
    if any(k in n for k in ("phone", "hotline", "toll-free", "toll free")):
        return "phone"
    if "url" in n or n.endswith(" page") or "page url" in n:
        return "url"
    if "salutation" in n:
        return "salutation"
    if "zip" in n:
        return "zip"
    if "city" in n:
        return "city"
    if "country" in n:
        return "country"
    if "address" in n:
        return "address"
    if any(k in n for k in ("pin", "password", "access key", "user key", "account key", "profile key")):
        return "credential"
    if "company" in n or "business name" in n:
        return "company_name"
    if "first name" in n:
        return "first_name"
    if "last name" in n:
        return "last_name"
    if any(k in n for k in ("client name", "client full name", "customer name", "person name",
                             "full name", "representative name", "username")):
        return "person_name"
    if any(k in n for k in ("number", " id", "identifier")) and not any(
        k in n for k in ("phone number", "toll-free number")
    ):
        return "code"
    if any(k in n for k in ("amount", "currency", "money")):
        return "money"
    if "month" in n:
        return "month"
    if "year" in n:
        return "year"
    if "date" in n:
        return "date"
    if any(k in n for k in ("hours", "opening time", "closing time", "start time", "end time", "working hours")):
        return "business_hours"
    if any(k in n for k in ("eta", "delivery time", "shipping time", "shipping days",
                             "cut off", "cut-off", "timeframe", "number of days", "x-day")):
        return "duration_phrase"
    if "time" in n:
        return "clock_time"
    if "steps" in n:
        return "steps_phrase"
    if any(k in n for k in ("category", "account type", "plan", "membership", "profile type")):
        return "plan_name"
    return "label"


_ID_PREFIXES = [
    ("invoice", "INV"), ("order", "ORD"), ("tracking", "TRK"), ("shipment", "TRK"),
    ("case", "CASE"), ("rebate", "REB"), ("compensation", "COMP"), ("reimbursement", "REI"),
    ("restitution", "RES"), ("claim", "CLM"), ("transaction", "TXN"), ("account", "ACC"),
    ("customer", "CUST"), ("ticket", "TCK"), ("reference", "REF"),
]
_PLAN_NAMES = ["Basic", "Standard", "Premium", "Business", "Enterprise"]
_SALUTATIONS = ["Mr.", "Ms.", "Mrs.", "Dr.", "Mx."]
_STEPS_PHRASES = [
    "Log in to your account", "Go to your account settings",
    "Select the relevant option from the menu", "Confirm your details",
    "Click Save to apply the change",
]
_DURATION_PHRASES = ["1-2 business days", "3-5 business days", "5-7 business days",
                      "24-48 hours", "7-10 business days"]


def make_value_generator():
    """Per-partition Faker instance (Faker isn't picklable across a driver-
    broadcast closure reliably, so it's constructed lazily inside the UDF)."""
    faker = Faker()

    def generate(category, name, seed):
        rng = random.Random(seed)
        Faker.seed(seed)
        if category == "email":
            return faker.email()
        if category == "phone":
            return faker.numerify("+1-###-###-####")
        if category == "url":
            return f"https://{faker.domain_name()}/{faker.uri_path()}".rstrip("/")
        if category == "salutation":
            return rng.choice(_SALUTATIONS)
        if category == "zip":
            return faker.postcode()
        if category == "city":
            return faker.city()
        if category == "country":
            return faker.country()
        if category == "address":
            return faker.street_address()
        if category == "credential":
            return faker.numerify("####") if "pin" in name.lower() else faker.bothify("??????####").upper()
        if category == "company_name":
            return faker.company()
        if category == "first_name":
            return faker.first_name()
        if category == "last_name":
            return faker.last_name()
        if category == "person_name":
            return faker.name()
        if category == "code":
            prefix = "REF"
            nl = name.lower()
            for key, pfx in _ID_PREFIXES:
                if key in nl:
                    prefix = pfx
                    break
            return f"{prefix}-{faker.numerify('#######')}"
        if category == "money":
            return f"${rng.uniform(5, 500):.2f}"
        if category == "month":
            return faker.month_name()
        if category == "year":
            return str(rng.choice([2024, 2025, 2026]))
        if category == "date":
            return faker.date_between(start_date="-1y", end_date="today").strftime("%B %d, %Y")
        if category == "business_hours":
            return "Mon-Fri, 9:00 AM - 6:00 PM EST"
        if category == "duration_phrase":
            return rng.choice(_DURATION_PHRASES)
        if category == "clock_time":
            return faker.time(pattern="%I:%M %p")
        if category == "steps_phrase":
            return rng.choice(_STEPS_PHRASES)
        if category == "plan_name":
            return rng.choice(_PLAN_NAMES)
        return name

    return generate


def fill_row(row_id, instruction, response):
    """Runs per-row on the executor: extract placeholders from both texts,
    build one value per unique name (consistent across instruction/response
    for this ticket), substitute, and return everything the silver schema
    needs from this step."""
    generate = fill_row._generate
    instr_ph = extract_placeholders(instruction)
    resp_ph = extract_placeholders(response)
    all_ph = list(dict.fromkeys(instr_ph + resp_ph))
    value_map = {}
    for i, name in enumerate(all_ph):
        seed = (int(row_id) * 1000 + i) ^ SEED
        value_map[name] = generate(classify(name), name, seed)

    def sub(text):
        def _r(m):
            return value_map.get(m.group(1).strip(), m.group(1))
        return PLACEHOLDER_RE.sub(_r, text) if text else text

    return sub(instruction), sub(response), instr_ph, resp_ph


fill_row._generate = make_value_generator()

fill_schema = T.StructType([
    T.StructField("instruction_text", T.StringType()),
    T.StructField("response_text", T.StringType()),
    T.StructField("instruction_entities", T.ArrayType(T.StringType())),
    T.StructField("response_entities", T.ArrayType(T.StringType())),
])
fill_udf = F.udf(fill_row, fill_schema)
decode_flags_udf = F.udf(decode_flags, T.ArrayType(T.StringType()))

filled = (
    raw_df
    .withColumn("_filled", fill_udf("row_id", "instruction", "response"))
    .withColumn("language_tags", decode_flags_udf("flags"))
    .select(
        "row_id", "flags", "language_tags", "category", "intent",
        "_filled.instruction_text", "_filled.response_text",
        "_filled.instruction_entities", "_filled.response_entities",
    )
)
display(filled.limit(5))

# COMMAND ----------

# MAGIC %md ### Synthetic ticket-stream + resolution metadata
# MAGIC
# MAGIC Same rules as the local pipeline: business-hours/weekday-weighted
# MAGIC `created_at`, weighted `channel`, Pareto-skewed `customer_id` pool,
# MAGIC intent-driven `priority`/`resolved_by`/`resolution_status`, log-normal
# MAGIC `handle_time_seconds`, and an outcome-correlated nullable `csat_score`.

# COMMAND ----------

n_customers = max(1, raw_df.count() // 4)


@F.udf(T.StringType())
def gen_ticket_id(row_id):
    return f"TCK-{100000 + int(row_id)}"


@F.udf(T.TimestampType())
def gen_created_at(row_id):
    rng = random.Random(int(row_id) ^ SEED)
    end = datetime.utcnow()
    day_offset = rng.randint(0, 364)
    candidate = end - timedelta(days=day_offset)
    if candidate.weekday() >= 5 and rng.random() < 0.55:
        day_offset = rng.randint(0, 364)
        while (end - timedelta(days=day_offset)).weekday() >= 5:
            day_offset = rng.randint(0, 364)
    hour = rng.randint(9, 17) if rng.random() < 0.72 else rng.randint(0, 23)
    return (end - timedelta(days=day_offset)).replace(
        hour=hour, minute=rng.randint(0, 59), second=rng.randint(0, 59), microsecond=0
    )


@F.udf(T.StringType())
def gen_channel(row_id):
    rng = random.Random(int(row_id) ^ SEED ^ 0x1)
    return rng.choices(CHANNELS, weights=CHANNEL_WEIGHTS, k=1)[0]


@F.udf(T.StringType())
def gen_customer_id(row_id):
    rng = random.Random(int(row_id) ^ SEED ^ 0x2)
    # Pareto-shaped pick: bias toward a small low-index "frequent customer" band.
    if rng.random() < 0.35:
        idx = int(rng.paretovariate(1.5)) % max(1, n_customers // 20)
    else:
        idx = rng.randint(0, n_customers - 1)
    return f"CUST-{idx:06d}"


@F.udf(T.StringType())
def gen_priority(intent, flags, row_id):
    rng = random.Random(int(row_id) ^ SEED ^ 0x3)
    if intent in HIGH_PRIORITY_INTENTS:
        weights = [0.05, 0.25, 0.45, 0.25]
    elif intent in AI_FRIENDLY_INTENTS:
        weights = [0.50, 0.35, 0.12, 0.03]
    else:
        weights = [0.25, 0.45, 0.25, 0.05]
    if flags and "W" in flags:
        weights = [weights[0] * 0.3, weights[1] * 0.6, weights[2] * 1.3, weights[3] * 2.0]
        total = sum(weights)
        weights = [w / total for w in weights]
    return rng.choices(PRIORITY_VALUES, weights=weights, k=1)[0]


resolution_schema = T.StructType([
    T.StructField("resolved_by", T.StringType()),
    T.StructField("resolution_status", T.StringType()),
    T.StructField("handle_time_seconds", T.IntegerType()),
    T.StructField("csat_score", T.DoubleType()),
])


@F.udf(resolution_schema)
def gen_resolution(intent, priority, row_id):
    rng = random.Random(int(row_id) ^ SEED ^ 0x4)
    if intent in AI_FRIENDLY_INTENTS:
        ai_prob = 0.82
    elif intent in HUMAN_LEANING_INTENTS:
        ai_prob = 0.12
    else:
        ai_prob = 0.50
    if priority in ("high", "urgent"):
        ai_prob *= 0.4
    ai_prob = min(max(ai_prob, 0.03), 0.95)

    is_ai = rng.random() < ai_prob
    resolved_by = "ai" if is_ai else "human"
    bump = priority in ("high", "urgent")

    if is_ai:
        weights = [0.65, 0.30, 0.05] if bump else [0.85, 0.10, 0.05]
    else:
        weights = [0.55, 0.35, 0.10] if bump else [0.70, 0.20, 0.10]
    status = rng.choices(["resolved", "escalated", "pending"], weights=weights, k=1)[0]

    mean_log, sigma, lo, hi = (3.0, 0.6, 60, 3600) if not is_ai else (4.5, 0.5, 15, 600)
    ht = rng.lognormvariate(mean_log, sigma)
    if bump:
        ht *= 1.3
    handle_time = int(min(max(ht, lo), hi))

    if status == "pending":
        csat = None
    else:
        null_prob = 0.35 if status == "escalated" else 0.15
        if rng.random() < null_prob:
            csat = None
        else:
            weights = [0.15, 0.25, 0.30, 0.20, 0.10] if status == "escalated" else [0.03, 0.05, 0.12, 0.35, 0.45]
            csat = float(rng.choices([1, 2, 3, 4, 5], weights=weights, k=1)[0])

    return (resolved_by, status, handle_time, csat)


enriched = (
    filled
    .withColumn("ticket_id", gen_ticket_id("row_id"))
    .withColumn("created_at", gen_created_at("row_id"))
    .withColumn("channel", gen_channel("row_id"))
    .withColumn("customer_id", gen_customer_id("row_id"))
    .withColumn("priority", gen_priority("intent", "flags", "row_id"))
    .withColumn("_res", gen_resolution("intent", "priority", "row_id"))
)

# COMMAND ----------

# MAGIC %md ### Duplicate (repeat-contact) detection

# COMMAND ----------

from pyspark.sql import Window

dup_window = Window.partitionBy("instruction").orderBy("row_id")
enriched = enriched.withColumn(
    "duplicate_of_ticket_id",
    F.when(
        F.row_number().over(dup_window) > 1,
        F.first("ticket_id").over(dup_window),
    ).otherwise(F.lit(None)),
)

# COMMAND ----------

# MAGIC %md ### Split + write silver tables

# COMMAND ----------

tickets = enriched.select(
    "ticket_id", "created_at", "channel", "customer_id", "category", "intent",
    "instruction_text", "instruction_entities", "priority", "language_tags",
    "duplicate_of_ticket_id",
)
resolutions = enriched.select(
    "ticket_id", "response_text", "response_entities",
    F.col("_res.resolved_by").alias("resolved_by"),
    F.col("_res.resolution_status").alias("resolution_status"),
    F.col("_res.handle_time_seconds").alias("handle_time_seconds"),
    F.col("_res.csat_score").alias("csat_score"),
)

tickets.write.mode("overwrite").parquet(SILVER_TICKETS_PATH)
resolutions.write.mode("overwrite").parquet(SILVER_RESOLUTIONS_PATH)

print(f"tickets -> {SILVER_TICKETS_PATH} ({tickets.count()} rows)")
print(f"resolutions -> {SILVER_RESOLUTIONS_PATH} ({resolutions.count()} rows)")
display(tickets.limit(5))
display(resolutions.limit(5))
