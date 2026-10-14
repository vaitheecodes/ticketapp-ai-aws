"""Entity-placeholder extraction and synthetic value generation.

The Site24x7 dataset has exactly one placeholder entity — `{{monitor
name}}` / `{{Monitor Name}}` (case varies by row, same entity) — so this
is a single-entity generator, not the ~30-bucket classifier the old
Bitext-derived version needed.
"""

import random
import re
from typing import Dict, List

PLACEHOLDER_RE = re.compile(r"\{\{([^{}]+)\}\}")

# A realistic spread of Site24x7-style monitor names, spanning the kinds
# of resources the dataset's categories cover (web/API, servers, DBs,
# containers, cloud, network devices) so a response that references
# "{{Monitor Name}}" reads naturally regardless of ticket topic.
_MONITOR_NAMES = [
    "Prod-WebServer-01", "Prod-WebServer-02", "Staging-WebServer-01",
    "API-Gateway-EU", "API-Gateway-US", "API-Gateway-APAC",
    "DB-Primary-US-East", "DB-Primary-EU-West", "DB-Replica-02",
    "LoadBalancer-Prod-01", "Auth-Service", "Payments-API",
    "CDN-Edge-US-West", "Redis-Cache-01", "K8s-Cluster-Prod",
    "K8s-Cluster-Staging", "Docker-Host-03", "App-Server-04",
    "SNMP-Switch-Core-01", "NetFlow-Router-EU", "Azure-VM-WebApp-01",
    "Azure-Blob-Storage-Monitor", "APM-Checkout-Service",
    "Log-Ingest-Pipeline-01", "Server-DB-Backup-01",
    "Website-Homepage-Monitor", "Website-Checkout-Flow",
]


def extract_placeholders(text: str) -> List[str]:
    """Return the ordered, de-duplicated list of `{{...}}` names in text."""
    seen: Dict[str, None] = {}
    for match in PLACEHOLDER_RE.findall(text):
        name = match.strip()
        if name not in seen:
            seen[name] = None
    return list(seen.keys())


def build_value_map(placeholders: List[str], rng: random.Random) -> Dict[str, str]:
    """One synthetic monitor name per unique placeholder, reused
    consistently across a ticket's instruction and response text.
    "monitor name" and "Monitor Name" resolve to the same value even
    though they're distinct strings, since they're the same entity."""
    value_by_key: Dict[str, str] = {}
    value_map: Dict[str, str] = {}
    for name in placeholders:
        key = name.lower()
        if key not in value_by_key:
            value_by_key[key] = rng.choice(_MONITOR_NAMES)
        value_map[name] = value_by_key[key]
    return value_map


def fill_text(text: str, value_map: Dict[str, str]) -> str:
    def _sub(match: "re.Match[str]") -> str:
        name = match.group(1).strip()
        return value_map.get(name, name)

    return PLACEHOLDER_RE.sub(_sub, text)
