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

import logging
import re
from functools import lru_cache

from backend import kb_registry

logger = logging.getLogger(__name__)

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
    # "for api": Site24x7's add-monitor error "Invalid data provided for api".
    "API": r"\b(rest |soap |graphql )?api (monitor|endpoint|check)s?\b|\brest api\b|\bsoap\b|\bgraphql\b"
           r"|\bfor (an |the )?api\b",
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
# Platforms a KB guide can be specific to (e.g. the Linux and Windows agent
# entries, when the reviewer registers that in kb_registry.json). Not
# monitor types: "server" already covers both.
PLATFORMS = {
    "Linux": r"\blinux\b|\bubuntu\b|\bcentos\b|\brhel\b|\bred ?hat\b|\bdebian\b",
    "Windows": r"\bwindows\b",
}
TERMS = {**INTEGRATIONS, **MONITOR_TYPES, **PLATFORMS}
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


@lru_cache(maxsize=1)
def _merged() -> tuple[dict[str, re.Pattern], dict[str, set[str]]]:
    """Built-in terms and intent map plus data/kb/kb_registry.json (terms
    and intents registered when entries are approved; a registered intent
    replaces a built-in one). Loaded once per process; reload() after a
    change."""
    registry = kb_registry.load()
    compiled = dict(_COMPILED)
    built_in = {label.lower() for label in _COMPILED}
    for label, pattern in registry["terms"].items():
        if label.lower() in built_in:
            logger.warning("kb_registry: term %s repeats a built-in term; the built-in pattern is kept", label)
            continue
        compiled[label] = re.compile(pattern, re.IGNORECASE)
    intents = {intent: set(labels) for intent, labels in INTENT_TERMS.items()}
    for intent, labels in registry["intent_terms"].items():
        unknown = [label for label in labels if label not in compiled]
        if unknown:
            logger.warning("kb_registry: intent %s names unknown terms %s; ignored", intent, unknown)
        intents[intent] = {label for label in labels if label in compiled}
    return compiled, intents


def reload() -> None:
    _merged.cache_clear()


def known_terms() -> list[str]:
    return sorted(_merged()[0])


def intent_terms(intent: str) -> set[str] | None:
    """The terms an intent depends on, or None if it was never registered."""
    terms = _merged()[1].get(intent)
    return set(terms) if terms is not None else None


def mentioned(text: str) -> set[str]:
    """Every specific term named in `text`."""
    return {label for label, pattern in _merged()[0].items() if pattern.search(text or "")}


def monitor_types(customer_text: str, requested_category: str | None = None) -> list[str]:
    """Monitor types the customer has stated, in their words or through
    the category they picked."""
    found = [label for label in MONITOR_TYPES if _COMPILED[label].search(customer_text or "")]
    if not found and requested_category in CATEGORY_MONITOR_TYPE:
        found.append(CATEGORY_MONITOR_TYPE[requested_category])
    return found


def entry_terms(intent: str, response: str = "") -> set[str]:
    """What a KB entry is specific to (empty: a generic entry)."""
    terms = set(_merged()[1].get(intent, set()))
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


def names_another_variant(candidate: dict, customer_text: str) -> bool:
    """The entry is about a different variant of its intent than the one
    the customer named: its own question/answer names only terms the
    customer hasn't (the Android guide for "my iPad app", the Linux guide
    for "on Windows"). Intents with several variants share one term set,
    so missing_terms alone counts either guide as confirmed."""
    terms = entry_terms(candidate.get("intent", ""), candidate.get("response", ""))
    named = terms & mentioned(customer_text)
    if not named:
        return False
    own = terms & mentioned(f"{candidate.get('instruction', '')}\n{candidate.get('response', '')}")
    return bool(own) and not own & named
