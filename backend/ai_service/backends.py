"""AI provider backends behind one interface.

    generate(system_prompt, user_content, *, max_tokens, json_schema=None,
             extra_messages=()) -> str

`system_prompt` is trusted instructions; `user_content` is the already
fenced data blocks (provider.data_block). `extra_messages` carries the
retry note after invalid output. Backends return raw model text;
provider.py strips reasoning/code fences and validates. Every backend
raises BackendError on any failure; provider.py logs the exception type
only and turns it into AIError.

Selected by AI_PROVIDER:
  quickml_rest       Catalyst QuickML LLM Serving over REST with a Zoho OAuth
                     access token (refreshed from a self-client refresh token).
                     Works locally and inside AppSail.
  quickml_sdk        Same endpoint through the Catalyst Python SDK
                     (zcatalyst_sdk, `quick_ml().ask_llm`), initialised from
                     the incoming AppSail request's X-ZC-* headers.
  openai_compatible  Any OpenAI-style chat completions API (optional).
  azure              Azure OpenAI (kept until Phase 4 stage C6).
  mock               Deterministic offline fake (default when nothing is
                     configured; never used in production by accident — a
                     warning is logged).

QuickML endpoints take one prompt string, are stateless (conversation mode
off) and have their model parameters (temperature, max tokens, system
instructions) fixed in the console; single-prompt backends flatten the
system prompt, the fenced data and any retry note into one prompt.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import threading
import time
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class BackendError(Exception):
    """Any provider/network/config failure. Message must never contain
    secrets or prompt content."""


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
              else "Write only the reply itself."]
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Zoho OAuth (QuickML REST)
# ---------------------------------------------------------------------------
class ZohoTokenSource:
    """Access tokens for `Authorization: Zoho-oauthtoken ...`.

    Either QUICKML_ACCESS_TOKEN (a short-lived token pasted for a quick
    local test; never refreshed), or a self client: ZOHO_CLIENT_ID,
    ZOHO_CLIENT_SECRET, ZOHO_REFRESH_TOKEN (scope QuickML.deployment.READ),
    exchanged at ZOHO_ACCOUNTS_URL (default https://accounts.zoho.in for
    the India data centre). Tokens last an hour; refreshed 5 minutes early
    or after a 401."""

    def __init__(self, http_post=None):
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at = 0.0
        self._http_post = http_post

    @property
    def refreshable(self) -> bool:
        return bool(env("ZOHO_REFRESH_TOKEN"))

    def invalidate(self) -> None:
        with self._lock:
            self._token, self._expires_at = None, 0.0

    def token(self) -> str:
        static = env("QUICKML_ACCESS_TOKEN")
        if static and not self.refreshable:
            return static
        with self._lock:
            if self._token and time.time() < self._expires_at - 300:
                return self._token
            client_id, secret, refresh = (env("ZOHO_CLIENT_ID"), env("ZOHO_CLIENT_SECRET"),
                                          env("ZOHO_REFRESH_TOKEN"))
            if not (client_id and secret and refresh):
                raise BackendError("QuickML OAuth is not configured")
            post = self._http_post or _httpx_post
            accounts = env("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.in").rstrip("/")
            status, body = post(f"{accounts}/oauth/v2/token", data={
                "refresh_token": refresh, "client_id": client_id, "client_secret": secret,
                "grant_type": "refresh_token",
            })
            if status != 200 or not isinstance(body, dict) or not body.get("access_token"):
                # Zoho returns {"error": "invalid_code"} etc.; the code is safe to log.
                error = body.get("error") if isinstance(body, dict) else None
                raise BackendError(f"Zoho token refresh failed ({status}, {error or 'no token'})")
            self._token = body["access_token"]
            self._expires_at = time.time() + float(body.get("expires_in", 3600))
            return self._token


def _httpx_post(url: str, *, data=None, json_body=None, headers=None) -> tuple[int, Any]:
    import httpx

    response = httpx.post(url, data=data, json=json_body, headers=headers, timeout=timeout_seconds())
    try:
        body = response.json()
    except ValueError:
        body = None
    return response.status_code, body


def extract_quickml_text(body: Any) -> str:
    """The generated text from a QuickML response. Documented shape
    (Python SDK page): {"status": "success", "result": [{"content": ...}]};
    the REST response isn't documented in text, so a few likely shapes are
    accepted and anything else is an error."""
    if isinstance(body, dict):
        if str(body.get("status", "success")).lower() not in ("success", "ok", "200"):
            raise BackendError("QuickML returned a non-success status")
        for key in ("result", "data", "response", "output"):
            value = body.get(key)
            if isinstance(value, list) and value:
                value = value[0]
            if isinstance(value, str) and value.strip():
                return value
            if isinstance(value, dict):
                for inner in ("content", "response", "output", "text", "generated_text"):
                    if isinstance(value.get(inner), str):
                        return value[inner]
        choices = body.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") or {}
            if isinstance(message.get("content"), str):
                return message["content"]
    raise BackendError("Unrecognised QuickML response shape")


class QuickMLRestBackend:
    """POST QUICKML_ENDPOINT_URL with {"prompt": ...} and the headers the
    endpoint page shows: Authorization (Zoho-oauthtoken), CATALYST-ORG,
    Environment, X-QUICKML-ENDPOINT-KEY."""

    name = "quickml_rest"

    def __init__(self, tokens: ZohoTokenSource | None = None, http_post=None):
        self.tokens = tokens or ZohoTokenSource()
        self._http_post = http_post

    def _headers(self) -> dict:
        key = env("QUICKML_ENDPOINT_KEY")
        if not key:
            raise BackendError("QUICKML_ENDPOINT_KEY is not set")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Zoho-oauthtoken {self.tokens.token()}",
            "X-QUICKML-ENDPOINT-KEY": key,
            "Environment": env("QUICKML_ENVIRONMENT", "Development"),
        }
        org = env("CATALYST_ORG_ID")
        if org:
            headers["CATALYST-ORG"] = org
        return headers

    def generate(self, system_prompt, user_content, *, max_tokens, json_schema=None, extra_messages=()):
        url = env("QUICKML_ENDPOINT_URL")
        if not url:
            raise BackendError("QUICKML_ENDPOINT_URL is not set")
        prompt = single_prompt(system_prompt, user_content, extra_messages, json_mode=json_schema is not None)
        post = self._http_post or _httpx_post
        for attempt in range(2):
            status, body = post(url, json_body={"prompt": prompt}, headers=self._headers())
            if status == 401 and attempt == 0 and self.tokens.refreshable:
                self.tokens.invalidate()  # expired early or revoked: refresh once
                continue
            if status != 200:
                raise BackendError(f"QuickML HTTP {status}")
            return extract_quickml_text(body)
        raise BackendError("QuickML authorisation failed")


# ---------------------------------------------------------------------------
# QuickML via the Catalyst SDK (inside AppSail)
# ---------------------------------------------------------------------------
# Headers of the AppSail request being served (X-ZC-* project and
# credential headers); set by backend.main's middleware when
# AI_PROVIDER=quickml_sdk. Background tasks run inside the same request
# context, so they see it too.
catalyst_request_headers: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "catalyst_request_headers", default=None)


class _HeaderRequest:
    def __init__(self, headers: dict):
        self.headers = headers


class QuickMLSDKBackend:
    name = "quickml_sdk"

    def generate(self, system_prompt, user_content, *, max_tokens, json_schema=None, extra_messages=()):
        key = env("QUICKML_ENDPOINT_KEY")
        if not key:
            raise BackendError("QUICKML_ENDPOINT_KEY is not set")
        headers = catalyst_request_headers.get()
        if not headers:
            raise BackendError("No Catalyst request context for the SDK")
        try:
            import zcatalyst_sdk
        except ImportError:
            raise BackendError("zcatalyst-sdk is not installed") from None
        app = zcatalyst_sdk.initialize(req=_HeaderRequest(headers))
        quickml = app.quick_ml()
        if not hasattr(quickml, "ask_llm"):
            # zcatalyst-sdk 1.4.0 (PyPI, Oct 2026) only has predict(); the
            # docs' ask_llm may need a newer SDK. Use quickml_rest instead.
            raise BackendError("Installed zcatalyst-sdk has no ask_llm")
        prompt = single_prompt(system_prompt, user_content, extra_messages, json_mode=json_schema is not None)
        response = quickml.ask_llm(key, prompt)
        if isinstance(response, str):
            try:
                response = json.loads(response)
            except ValueError:
                return response
        return extract_quickml_text(response)


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
    "quickml_rest": QuickMLRestBackend,
    "quickml_sdk": QuickMLSDKBackend,
    "openai_compatible": OpenAICompatibleBackend,
    "azure": AzureOpenAIBackend,
    "mock": MockBackend,
}
