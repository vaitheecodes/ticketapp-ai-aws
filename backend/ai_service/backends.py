"""AI provider backends behind one interface.

    generate(system_prompt, user_content, *, max_tokens, json_schema=None,
             extra_messages=()) -> str

`system_prompt` is trusted instructions; `user_content` is the already
fenced data blocks (provider.data_block). `extra_messages` carries the
retry note after invalid output. Backends return raw model text;
provider.py strips reasoning/code fences, unwraps and validates. Every
backend raises BackendError on any failure; provider.py logs the safe
message and turns it into AIError.

Selected by AI_PROVIDER:
  quickml_appsail    Catalyst QuickML LLM Serving over REST, authenticated
                     with the credential AppSail injects into each incoming
                     request (docs/quickml-appsail-call.md). Only works
                     while serving a request on AppSail.
  openai_compatible  Any OpenAI-style chat completions API (optional
                     fallback).
  azure              Azure OpenAI (legacy; removed in Phase 4 stage C6).
  mock               Deterministic offline fake: the default for tests and
                     local runs (no real-model access off AppSail).

The released Catalyst SDKs have no ask_llm, and the Zoho self client is
blocked for this account, so there is no SDK or OAuth path.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class BackendError(Exception):
    """Any provider/network/config failure. The message is written by us
    and must never contain secrets, tokens or prompt content."""


class ConfigError(BackendError):
    """A setup problem (missing/invalid key, org id, endpoint name). Retrying
    can't help; provider.py logs it at ERROR so an operator notices."""


class Backend(Protocol):
    name: str

    def generate(self, system_prompt: str, user_content: str, *, max_tokens: int,
                 json_schema: dict | None = None, extra_messages: tuple[str, ...] = ()) -> str: ...


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else default


def timeout_seconds() -> float:
    try:
        return float(env("AI_TIMEOUT_SECONDS", "90"))
    except ValueError:
        return 90.0


def single_prompt(system_prompt: str, user_content: str, extra_messages: tuple[str, ...] = (),
                  json_mode: bool = False) -> str:
    """One prompt string for single-prompt endpoints. Instructions first,
    then the fenced (untrusted) data, then any correction note."""
    parts = ["### Instructions", system_prompt.strip(), "### Data", user_content.strip()]
    for note in extra_messages:
        parts += ["### Correction", note.strip()]
    parts += ["### Response",
              "Return only the JSON object, with no other text." if json_mode
              else "Write only the reply itself, as plain text."]
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# QuickML on AppSail
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AppSailCredentials:
    """What AppSail injects into one incoming request and the QuickML call
    needs. Captured per request by backend.main's middleware into
    `appsail_credentials` (a context variable, so it is scoped to that
    request and its background tasks, never shared or global). repr
    hides the secrets so it can't leak into a log line."""

    admin_token: str
    admin_token_type: str  # "token" (OAuth access token) or "ticket"
    project_id: str
    project_key: str | None = None
    environment: str | None = None
    project_secret_key: str | None = None

    def __repr__(self) -> str:
        return (f"AppSailCredentials(project_id={self.project_id!r}, "
                f"environment={self.environment!r}, admin_token_type={self.admin_token_type!r})")

    @classmethod
    def from_headers(cls, headers: dict[str, str]) -> "AppSailCredentials | None":
        """headers: lower-cased header names -> values."""
        token, project_id = headers.get("x-zc-admin-cred-token"), headers.get("x-zc-projectid")
        # project_id goes into the URL path: digits only.
        if not (token and project_id and project_id.isdigit()):
            return None
        return cls(
            admin_token=token,
            admin_token_type=(headers.get("x-zc-admin-cred-type") or "token").lower(),
            project_id=project_id,
            project_key=headers.get("x-zc-project-key"),
            environment=headers.get("x-zc-environment"),
            project_secret_key=headers.get("x-zc-project-secret-key"),
        )


appsail_credentials: contextvars.ContextVar[AppSailCredentials | None] = contextvars.ContextVar(
    "appsail_credentials", default=None)

# Error codes QuickML returns in {"code": ..., "message": ...}, mapped to a
# message an operator can act on. None of them is worth retrying.
QUICKML_ERRORS = {
    "INVALID_ENDPOINT_KEY": "QuickML rejected the endpoint key (INVALID_ENDPOINT_KEY): check "
                            "QUICKML_ENDPOINT_KEY matches QUICKML_ENDPOINT_NAME and this environment",
    "ORGID_HEADER_UNAVAILABLE": "QuickML needs the CATALYST-ORG header: set QUICKML_ORG_ID",
}


def _error_code(body: Any) -> str | None:
    if isinstance(body, dict):
        code = body.get("code")
        if isinstance(body.get("data"), dict):
            code = body["data"].get("error_code") or code
        if isinstance(code, str):
            if "x-quickml-endpoint-key" in code.lower():
                return "MISSING_ENDPOINT_KEY_HEADER"
            return code
    return None


def extract_quickml_text(body: Any) -> str:
    """The generated text. Verified shape (docs/quickml-appsail-call.md):
    {"data": [{"data": "<text>"}], "finish_reason", "model", "usage"}.
    The SDK docs' {"status", "result": [{"content"}]} is accepted too;
    anything else is an error."""
    if isinstance(body, dict):
        items = body.get("data")
        if isinstance(items, list) and items and isinstance(items[0], dict) \
                and isinstance(items[0].get("data"), str):
            return items[0]["data"]
        results = body.get("result")
        if isinstance(results, list) and results and isinstance(results[0], dict) \
                and isinstance(results[0].get("content"), str):
            return results[0]["content"]
    raise BackendError("Unrecognised QuickML response shape")


def _httpx_post(url: str, *, json_body: dict, headers: dict) -> tuple[int, Any]:
    import httpx

    try:
        response = httpx.post(url, json=json_body, headers=headers, timeout=timeout_seconds())
    except httpx.TimeoutException:
        raise BackendError(f"QuickML timed out after {timeout_seconds():.0f}s") from None
    except httpx.HTTPError as exc:
        raise BackendError(f"QuickML request failed ({type(exc).__name__})") from None
    try:
        body = response.json()
    except ValueError:
        body = None
    return response.status_code, body


class QuickMLAppSailBackend:
    """POST {base}/quickml/v1/project/{project_id}/genai/endpoints/{name}/generate
    with {"prompt": ...}. Headers reproduce the request verified on AppSail
    (sent by the Catalyst SDK's client): Authorization from the injected
    admin credential, PROJECT_ID, Environment / X-Catalyst-Environment,
    X-CATALYST-USER, CATALYST-ORG (QUICKML_ORG_ID), the project secret key if injected, and
    X-QUICKML-ENDPOINT-KEY."""

    name = "quickml_appsail"

    def __init__(self, http_post=None):
        self._http_post = http_post

    @staticmethod
    def url(creds: AppSailCredentials) -> str:
        name = env("QUICKML_ENDPOINT_NAME")
        if not name:
            raise ConfigError("QUICKML_ENDPOINT_NAME is not set")
        # AppSail sets X_ZOHO_CATALYST_CONSOLE_URL (https://api.catalyst.zoho.in in the IN DC).
        base = (env("X_ZOHO_CATALYST_CONSOLE_URL") or env("QUICKML_API_BASE")
                or "https://api.catalyst.zoho.in").rstrip("/")
        return f"{base}/quickml/v1/project/{creds.project_id}/genai/endpoints/{name}/generate"

    @staticmethod
    def headers(creds: AppSailCredentials) -> dict[str, str]:
        # QUICKML_ORG_ID: Catalyst rejects env var names containing CATALYST
        # ("reserved keywords"), so CATALYST_ORG_ID is a local-only fallback;
        # X_ZOHO_CATALYST_ORG_ID is read in case the platform sets it.
        key = env("QUICKML_ENDPOINT_KEY")
        org = env("QUICKML_ORG_ID") or env("CATALYST_ORG_ID") or env("X_ZOHO_CATALYST_ORG_ID")
        if not key:
            raise ConfigError("QUICKML_ENDPOINT_KEY is not set")
        if not org:
            raise ConfigError(QUICKML_ERRORS["ORGID_HEADER_UNAVAILABLE"])
        prefix = "Zoho-ticket " if creds.admin_token_type == "ticket" else "Zoho-oauthtoken "
        environment = creds.environment or env("QUICKML_ENVIRONMENT", "Development")
        headers = {
            "Content-Type": "application/json",
            "Authorization": prefix + creds.admin_token,
            "CATALYST-ORG": org,
            "Environment": environment,
            "X-Catalyst-Environment": environment,
            "X-CATALYST-USER": "admin",
            "X-QUICKML-ENDPOINT-KEY": key,
        }
        if creds.project_key:
            headers["PROJECT_ID"] = creds.project_key
        if creds.project_secret_key:
            headers["X-ZC-PROJECT-SECRET-KEY"] = creds.project_secret_key
        return headers

    def generate(self, system_prompt, user_content, *, max_tokens, json_schema=None, extra_messages=()):
        creds = appsail_credentials.get()
        if creds is None:
            raise BackendError("No AppSail request credentials: QuickML is only reachable while "
                               "serving a request on AppSail (use AI_PROVIDER=mock locally)")
        prompt = single_prompt(system_prompt, user_content, extra_messages, json_mode=json_schema is not None)
        status, body = (self._http_post or _httpx_post)(
            self.url(creds), json_body={"prompt": prompt}, headers=self.headers(creds))
        if status == 200:
            return extract_quickml_text(body)
        code = _error_code(body)
        if code in QUICKML_ERRORS:
            raise ConfigError(QUICKML_ERRORS[code])
        if code == "MISSING_ENDPOINT_KEY_HEADER":
            raise ConfigError("QuickML did not receive the endpoint key header")
        if status in (401, 403):
            raise BackendError(f"QuickML refused the AppSail credential (HTTP {status})")
        if status == 429:
            raise BackendError("QuickML rate limit reached (HTTP 429)")
        raise BackendError(f"QuickML HTTP {status}" + (f" ({code})" if code else ""))


# ---------------------------------------------------------------------------
# OpenAI-style APIs
# ---------------------------------------------------------------------------
class OpenAICompatibleBackend:
    """AI_BASE_URL, AI_API_KEY, AI_MODEL; optional AI_EMBEDDING_MODEL."""

    name = "openai_compatible"

    def __init__(self, client=None):
        self._client = client

    def client(self):
        if self._client is None:
            from openai import OpenAI

            if not (env("AI_BASE_URL") and env("AI_MODEL")):
                raise BackendError("AI_BASE_URL / AI_MODEL are not set")
            self._client = OpenAI(base_url=env("AI_BASE_URL"), api_key=env("AI_API_KEY", "none"),
                                  timeout=timeout_seconds())
        return self._client

    def model(self) -> str:
        return env("AI_MODEL") or ""

    def generate(self, system_prompt, user_content, *, max_tokens, json_schema=None, extra_messages=()):
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_content}]
        messages += [{"role": "user", "content": note} for note in extra_messages]
        kwargs = {"response_format": {"type": "json_object"}} if json_schema is not None else {}
        completion = self.client().chat.completions.create(
            model=self.model(), messages=messages, temperature=0.2, max_tokens=max_tokens, **kwargs)
        return completion.choices[0].message.content or ""

    def embedding_model(self) -> str | None:
        return env("AI_EMBEDDING_MODEL")

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = self.client().embeddings.create(model=self.embedding_model(), input=texts)
        return [item.embedding for item in response.data]


class AzureOpenAIBackend(OpenAICompatibleBackend):
    """AZURE_OPENAI_ENDPOINT / _API_KEY / _API_VERSION / _DEPLOYMENT and
    optional _EMBEDDING_DEPLOYMENT. Removed in Phase 4 stage C6."""

    name = "azure"

    def client(self):
        if self._client is None:
            from openai import AzureOpenAI

            if not (env("AZURE_OPENAI_ENDPOINT") and env("AZURE_OPENAI_DEPLOYMENT")):
                raise BackendError("Azure OpenAI is not configured")
            self._client = AzureOpenAI(
                azure_endpoint=env("AZURE_OPENAI_ENDPOINT"), api_key=env("AZURE_OPENAI_API_KEY"),
                api_version=env("AZURE_OPENAI_API_VERSION", "2024-10-21"), timeout=timeout_seconds())
        return self._client

    def model(self) -> str:
        return env("AZURE_OPENAI_DEPLOYMENT") or ""

    def embedding_model(self) -> str | None:
        return env("AZURE_OPENAI_EMBEDDING_DEPLOYMENT")


# ---------------------------------------------------------------------------
# Mock
# ---------------------------------------------------------------------------
def _sample(schema: dict, defs: dict) -> Any:
    """Smallest value that satisfies a pydantic JSON schema node."""
    if "$ref" in schema:
        return _sample(defs[schema["$ref"].rsplit("/", 1)[-1]], defs)
    if "default" in schema:
        return schema["default"]
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    for combo in ("anyOf", "oneOf", "allOf"):
        if combo in schema:
            options = [s for s in schema[combo] if s.get("type") != "null"] or schema[combo]
            return _sample(options[0], defs)
    kind = schema.get("type")
    if kind == "object":
        return {k: _sample(v, defs) for k, v in schema.get("properties", {}).items()
                if k in schema.get("required", [])}
    if kind == "array":
        return [_sample(schema.get("items", {}), defs) for _ in range(schema.get("minItems", 0))]
    if kind == "string":
        return "Mock output (offline AI provider)."[: schema.get("maxLength", 200)].ljust(
            schema.get("minLength", 0), ".")
    if kind in ("number", "integer"):
        low = schema.get("minimum", schema.get("exclusiveMinimum", 0))
        return low if kind == "integer" else float(low)
    if kind == "boolean":
        return False
    return None


class MockBackend:
    """Deterministic and offline: JSON requests get the minimal valid
    object for the schema; text requests a fixed sentence. Useful for
    running the app without a model; tests script their own replies."""

    name = "mock"
    TEXT = "This is a placeholder reply from the offline mock AI provider."

    def generate(self, system_prompt, user_content, *, max_tokens, json_schema=None, extra_messages=()):
        if json_schema is None:
            return self.TEXT
        return json.dumps(_sample(json_schema, json_schema.get("$defs", {})))


BACKENDS = {
    "quickml_appsail": QuickMLAppSailBackend,
    "openai_compatible": OpenAICompatibleBackend,
    "azure": AzureOpenAIBackend,
    "mock": MockBackend,
}
