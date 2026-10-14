"""Shared paths and reference data for the bronze -> silver -> gold pipeline."""

from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]

RAW_DIR = PROJECT_ROOT / "data" / "raw"
BRONZE_DIR = PROJECT_ROOT / "data" / "bronze"
SILVER_DIR = PROJECT_ROOT / "data" / "silver"
GOLD_DIR = PROJECT_ROOT / "data" / "gold"

RAW_FILENAME = "Site24x7_Customer_Support_Training_Dataset_27K.csv"

DUCKDB_PATH = GOLD_DIR / "warehouse.duckdb"

# ---------------------------------------------------------------------------
# Reference data: flags legend
# ---------------------------------------------------------------------------
# Letters observed in the raw data that are NOT documented in the dataset
# card's legend are mapped to a best-effort label rather than dropped, so
# decoding never silently loses information.
FLAG_LEGEND = {
    "M": "morphological_variation",
    "L": "semantic_variation",
    "B": "basic_syntax",
    "I": "interrogative",
    "C": "coordinated_syntax",
    "N": "negation",
    "P": "politeness",
    "Q": "colloquial",
    "W": "offensive_language",
    "K": "keyword_mode",
    "E": "abbreviation",
    "Z": "typo_error",
    # Observed in the raw data but undocumented in the public dataset card.
    "V": "variation_other",
    "S": "spelling_variation",
}

# ---------------------------------------------------------------------------
# Reference data: category / intent enum (ground truth from the raw data —
# the unique category/intent combinations actually present in the
# Site24x7 CSV, 17 categories / 27 intents, all genuine Site24x7 support
# topics: monitoring setup/troubleshooting, alerting, integrations,
# account/licensing, and data retention)
# ---------------------------------------------------------------------------
CATEGORY_INTENTS = {
    "ACCOUNT": ["licensing"],
    "AGENT": ["agent_installation", "agent_not_reporting"],
    "ALERTS": [
        "alert_configuration", "alert_not_triggered", "false_alert",
        "monitor_threshold",
    ],
    "APM": ["apm_troubleshooting"],
    "APPLICATION_MONITORING": ["application_monitoring"],
    "CLOUD": ["azure_monitoring"],
    "CONTAINERS": ["docker_monitoring", "kubernetes_monitoring"],
    "DATA": ["data_retention"],
    "INTEGRATION": [
        "integrations", "pagerduty_integration", "servicenow_integration",
    ],
    "LOGS": ["log_monitoring"],
    "MONITORING": ["monitor_down", "monitor_not_discovered", "monitor_status"],
    "NETFLOW": ["netflow_monitoring"],
    "REPORTING": ["report_dashboard"],
    "SERVER_MONITORING": ["server_monitoring"],
    "SNMP": ["snmp_monitoring"],
    "SUPPORT": ["contact_support"],
    "WEB_MONITORING": ["api_monitoring", "website_monitoring"],
}

CATEGORIES = sorted(CATEGORY_INTENTS.keys())
INTENTS = sorted({i for intents in CATEGORY_INTENTS.values() for i in intents})

# Categories where a wrong auto-resolution is costly (licensing/billing
# changes, data retention/deletion) — held to a stricter confidence bar
# than the base threshold by src/agent's decide_route. The closest
# equivalent, for a monitoring product, to "irreversible or consequential
# if wrong" (the old Bitext taxonomy's REFUND/CANCEL/PAYMENT).
HIGH_RISK_CATEGORIES = {"ACCOUNT", "DATA"}

# ---------------------------------------------------------------------------
# Support platform enums used by synthetic ticket/resolution generation
# ---------------------------------------------------------------------------
CHANNELS = ["email", "chat", "voice"]
CHANNEL_WEIGHTS = [0.45, 0.40, 0.15]

RESOLVED_BY_VALUES = ["ai", "human"]
RESOLUTION_STATUS_VALUES = ["resolved", "escalated", "pending"]
PRIORITY_VALUES = ["low", "medium", "high", "urgent"]

# Intents that are well-documented, low-risk troubleshooting/setup topics
# with a clear KB answer — good candidates for AI deflection at a high
# rate in a mature CX automation deployment.
AI_FRIENDLY_INTENTS = {
    "monitor_down", "monitor_status", "monitor_not_discovered",
    "alert_configuration", "alert_not_triggered", "monitor_threshold",
    "agent_installation", "agent_not_reporting", "api_monitoring",
    "website_monitoring", "server_monitoring", "log_monitoring",
    "snmp_monitoring", "netflow_monitoring", "report_dashboard",
}

# Intents that tend to require a human: account/licensing changes and
# data retention/deletion are consequential if handled wrong, and
# contact_support is an explicit ask to skip the AI.
HUMAN_LEANING_INTENTS = {"licensing", "data_retention", "contact_support"}

# Intents that bump priority upward regardless of the base category weight
# — signals that monitoring itself is degraded or blind, which is more
# urgent than an ordinary how-to question.
HIGH_PRIORITY_INTENTS = {"monitor_down", "agent_not_reporting", "false_alert"}
