"""Specific products, integrations and monitor types: which ones a
customer has actually mentioned, and which ones a KB entry depends on.

A KB entry about one integration or monitor type (PagerDuty, Azure, an
API monitor...) only applies if the customer has said so. Retrieval
alone can't tell: "I am getting invalid data provided for api error"
matched "I am getting a pagerduty rate limit error" on "getting" and
"error". `missing_terms` flags such an entry so the answer isn't grounded
on it as if it applied (backend/graph.py `grounding_candidates`).

An entry's terms come from its intent (the reliable signal: KB questions
contain filled-in monitor names like "API-Gateway-EU" or "App-Server-04"
that look like types but aren't) plus integration and cloud names in its
answer. Customer terms come from the customer's own words only.
"""

from __future__ import annotations

import re

# label -> pattern, matched case-insensitively.
INTEGRATIONS = {
    "PagerDuty": r"\bpager ?duty\b",
    "ServiceNow": r"\bservice ?now\b",
    "Slack": r"\bslack\b",
    "Microsoft Teams": r"\bms ?teams\b|\bmicrosoft teams\b",
    "Opsgenie": r"\bops ?genie\b",
    "Jira": r"\bjira\b",
    "Zapier": r"\bzapier\b",
    "webhook": r"\bweb ?hooks?\b",
}
MONITOR_TYPES = {
    "website": r"\bweb ?sites?\b|\bweb ?pages?\b|\burls?\b|\bhttps? monitors?\b",
    "API": r"\b(rest |soap |graphql )?api (monitor|endpoint|check)s?\b|\brest api\b|\bsoap\b|\bgraphql\b",
    "server": r"\bservers?\b|\blinux\b|\bwindows\b|\bvms?\b",
    "ping": r"\bping\b|\bicmp\b",
    "port": r"\bport (monitor|check)s?\b|\btcp port\b",
    "DNS": r"\bdns\b",
    "SSL certificate": r"\bssl\b|\btls\b|\bcertificates?\b",
    "Azure": r"\bazure\b",
    "AWS": r"\baws\b|\bamazon web services\b|\bec2\b",
    "Google Cloud": r"\bgcp\b|\bgoogle cloud\b",
    "Kubernetes": r"\bkubernetes\b|\bk8s\b",
    "Docker": r"\bdocker\b",
    "network device (SNMP)": r"\bsnmp\b|\bnetwork devices?\b|\brouters?\b|\bfirewalls?\b",
    "NetFlow": r"\bnet ?flow\b|\bsflow\b",
    "APM": r"\bapm\b|\bapplication performance\b|\btraces?\b",
    "application": r"\bapplication monitors?\b|\bapp monitors?\b",
    "log monitoring": r"\blog (monitor|monitoring|management|ingestion)\b|\bapplogs?\b",
}
TERMS = {**INTEGRATIONS, **MONITOR_TYPES}
_COMPILED = {label: re.compile(pattern, re.IGNORECASE) for label, pattern in TERMS.items()}

# Names that make an entry specific wherever they appear in its answer.
_ANSWER_NAMES = set(INTEGRATIONS) | {"Azure", "AWS", "Google Cloud", "Kubernetes", "Docker"}
# A monitor name such as "Azure-VM-WebApp-01" names a monitor, not a type.
_HYPHENATED_NAME = re.compile(r"\b\w+(?:-\w+)+\b")

# What each specific KB intent depends on. Intents not listed (contact
# support, alert configuration, monitor down, licensing...) are generic.
INTENT_TERMS = {
    "pagerduty_integration": {"PagerDuty"},
    "servicenow_integration": {"ServiceNow"},
    "azure_monitoring": {"Azure"},
    "docker_monitoring": {"Docker"},
    "kubernetes_monitoring": {"Kubernetes"},
    "snmp_monitoring": {"network device (SNMP)"},
    "netflow_monitoring": {"NetFlow"},
    "apm_troubleshooting": {"APM"},
    "application_monitoring": {"application", "APM"},
    "log_monitoring": {"log monitoring"},
    "website_monitoring": {"website"},
    "api_monitoring": {"API"},
    "server_monitoring": {"server"},
}

# A category the customer picked at chat start that already says what
# kind of monitor it is.
CATEGORY_MONITOR_TYPE = {
    "WEB_MONITORING": "website or API",
    "SERVER_MONITORING": "server",
    "SNMP": "network device (SNMP)",
    "NETFLOW": "NetFlow",
    "CLOUD": "cloud",
    "CONTAINERS": "container (Docker or Kubernetes)",
    "APM": "APM",
    "APPLICATION_MONITORING": "application",
    "LOGS": "log monitoring",
}


def mentioned(text: str) -> set[str]:
    """Every specific term named in `text`."""
    return {label for label, pattern in _COMPILED.items() if pattern.search(text or "")}


def monitor_types(customer_text: str, requested_category: str | None = None) -> list[str]:
    """Monitor types the customer has stated, in their words or through
    the category they picked."""
    found = [label for label in MONITOR_TYPES if _COMPILED[label].search(customer_text or "")]
    if not found and requested_category in CATEGORY_MONITOR_TYPE:
        found.append(CATEGORY_MONITOR_TYPE[requested_category])
    return found


def entry_terms(intent: str, response: str = "") -> set[str]:
    """What a KB entry is specific to (empty: a generic entry)."""
    terms = set(INTENT_TERMS.get(intent, set()))
    answer = _HYPHENATED_NAME.sub(" ", response or "")
    terms |= {label for label in _ANSWER_NAMES if _COMPILED[label].search(answer)}
    return terms


def missing_terms(candidate: dict, customer_text: str) -> set[str]:
    """The specific terms of a KB candidate (dict with intent/response)
    when the customer has mentioned none of them; empty when the entry is
    generic or the customer has named at least one."""
    terms = entry_terms(candidate.get("intent", ""), candidate.get("response", ""))
    if not terms or terms & mentioned(customer_text):
        return set()
    return terms
