"""Admin-editable application settings.

Three sections, each a validated pydantic model stored as JSON in
app_settings (one row per section):

  routing    decide_route's escalation conditions: confidence bars,
             high-risk categories, which language tags force a human,
             max clarifying rounds
  incidents  correlation window, pre-filter size, similarity, routing bar,
             how long resolved incidents are remembered
  statuses   display labels for the five ticket statuses (the statuses
             themselves are fixed: the workflow depends on each one)

Precedence: admin value (DB) > environment variable (INCIDENT_*) > code
default. Values are cached in memory: loaded at startup (load()) and
refreshed whenever the admin saves (save()). With no DB loaded — e.g. the
pure-function routing tests — the defaults apply. The cache is
per-process: a multi-worker deployment would need a reload hook (the
Procfile runs a single uvicorn process).
"""

from __future__ import annotations

import json
import logging
import os

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.ai_service.schemas import CATEGORIES
from backend.conversation import now

logger = logging.getLogger(__name__)

LANGUAGE_TAGS = ("offensive_language", "coordinated_syntax", "negation")


class RoutingSettings(BaseModel):
    confidence_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    high_risk_confidence_threshold: float = Field(default=0.90, ge=0.0, le=1.0)
    high_risk_categories: list[str] = ["ACCOUNT", "DATA"]
    escalation_language_tags: list[str] = list(LANGUAGE_TAGS)
    max_clarification_attempts: int = Field(default=3, ge=0, le=10)

    @field_validator("high_risk_categories")
    @classmethod
    def _known_categories(cls, value):
        unknown = sorted(set(value) - set(CATEGORIES))
        if unknown:
            raise ValueError(f"Unknown categories: {', '.join(unknown)}")
        return sorted(set(value))

    @field_validator("escalation_language_tags")
    @classmethod
    def _known_tags(cls, value):
        unknown = sorted(set(value) - set(LANGUAGE_TAGS))
        if unknown:
            raise ValueError(f"Unknown language tags: {', '.join(unknown)}")
        return sorted(set(value))

    @model_validator(mode="after")
    def _high_risk_is_stricter(self):
        if self.high_risk_confidence_threshold < self.confidence_threshold:
            raise ValueError("The high-risk bar can't be lower than the base confidence bar")
        return self


class IncidentSettings(BaseModel):
    window_hours: int = Field(default=12, ge=1, le=24 * 14)
    min_tickets: int = Field(default=3, ge=2, le=40)
    similarity: float = Field(default=0.80, ge=0.0, le=1.0)
    routing_confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    # How long a resolved incident is remembered for "did I have an issue?"
    memory_days: int = Field(default=90, ge=1, le=3650)


class StatusLabels(BaseModel):
    open: str = Field(default="Open", min_length=1, max_length=40)
    in_progress: str = Field(default="In Progress", min_length=1, max_length=40)
    pending_customer: str = Field(default="Pending Customer", min_length=1, max_length=40)
    resolved: str = Field(default="Resolved", min_length=1, max_length=40)
    closed: str = Field(default="Closed", min_length=1, max_length=40)


SECTIONS: dict[str, type[BaseModel]] = {
    "routing": RoutingSettings,
    "incidents": IncidentSettings,
    "statuses": StatusLabels,
}

_ENV = {
    "incidents": {
        "window_hours": "INCIDENT_WINDOW_HOURS",
        "min_tickets": "INCIDENT_MIN_TICKETS",
        "similarity": "INCIDENT_SIMILARITY",
        "routing_confidence": "INCIDENT_ROUTING_CONFIDENCE",
        "memory_days": "INCIDENT_MEMORY_DAYS",
    },
}

_overrides: dict[str, dict] = {}


def _env_values(section: str) -> dict:
    values = {}
    for field, var in _ENV.get(section, {}).items():
        if os.environ.get(var):
            values[field] = os.environ[var]
    return values


def defaults(section: str) -> BaseModel:
    """Env (where it applies) over code defaults; an invalid env value is
    ignored with a warning rather than breaking startup."""
    model = SECTIONS[section]
    try:
        return model(**_env_values(section))
    except ValueError:
        logger.warning("Invalid environment value for %s settings; using defaults", section)
        return model()


def get(section: str) -> BaseModel:
    base = defaults(section)
    if section in _overrides:
        try:
            return SECTIONS[section](**{**base.model_dump(), **_overrides[section]})
        except ValueError:
            logger.warning("Stored %s settings are invalid; using defaults", section)
    return base


def load(conn) -> None:
    _overrides.clear()
    for row in conn.execute("SELECT key, value FROM app_settings"):
        if row["key"] in SECTIONS:
            _overrides[row["key"]] = json.loads(row["value"])


def save(conn, section: str, values: dict, actor_id: str) -> BaseModel:
    """Validate and persist a full section, then refresh the cache. Raises
    pydantic.ValidationError for invalid input."""
    validated = SECTIONS[section](**values)
    conn.execute(
        "INSERT INTO app_settings (key, value, updated_by, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_by = excluded.updated_by, "
        "updated_at = excluded.updated_at",
        (section, validated.model_dump_json(), actor_id, now()),
    )
    _overrides[section] = validated.model_dump()
    return validated


def reset(conn, section: str) -> BaseModel:
    conn.execute("DELETE FROM app_settings WHERE key = ?", (section,))
    _overrides.pop(section, None)
    return get(section)


def clear_cache() -> None:
    _overrides.clear()
