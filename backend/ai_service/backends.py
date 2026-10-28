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
  bedrock            Amazon Bedrock Converse API (boto3, default AWS
                     credential chain: AWS_PROFILE locally, the service's
                     IAM role when deployed).
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
import re
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
# Amazon Bedrock
# ---------------------------------------------------------------------------
# Bedrock error codes -> (config problem?, message). The AWS error message
# itself is never used: it can echo request details.
BEDROCK_ERRORS = {
    "ThrottlingException": (False, "Bedrock throttled the request (ThrottlingException)"),
    "ServiceQuotaExceededException": (False, "Bedrock quota exceeded (ServiceQuotaExceededException)"),
    "ModelTimeoutException": (False, "Bedrock model timed out (ModelTimeoutException)"),
    "ServiceUnavailableException": (False, "Bedrock is unavailable (ServiceUnavailableException)"),
    "ModelNotReadyException": (False, "Bedrock model is not ready (ModelNotReadyException)"),
    "InternalServerException": (False, "Bedrock internal error (InternalServerException)"),
    "ModelErrorException": (False, "Bedrock model error (ModelErrorException)"),
    "AccessDeniedException": (True, "Bedrock refused access (AccessDeniedException): check the IAM "
                                    "policy and that model access is enabled for BEDROCK_MODEL_ID"),
    "UnrecognizedClientException": (True, "AWS rejected the credentials (UnrecognizedClientException)"),
    "ExpiredTokenException": (True, "AWS credentials have expired (ExpiredTokenException)"),
    "ValidationException": (True, "Bedrock rejected the request (ValidationException): check "
                                  "BEDROCK_MODEL_ID, AWS_REGION and AI_MAX_TOKENS"),
    "ResourceNotFoundException": (True, "Bedrock model not found (ResourceNotFoundException): "
                                        "check BEDROCK_MODEL_ID and AWS_REGION"),
}


TOOL_NAME_UNSAFE = re.compile(r"[^a-zA-Z0-9_-]")


class BedrockBackend:
    """Amazon Bedrock Converse API via boto3. Credentials come from the
    default AWS chain only (AWS_PROFILE locally, the service role when
    deployed); none are read or stored here. BEDROCK_MODEL_ID (default
    Nova Lite through the APAC inference profile), AWS_REGION.

    boto3's own retries are off: like every backend, a provider error is
    reported once and never retried. `on_usage(dict)` is an optional hook
    (scripts/bedrock_quality_check.py) for token counts and latency."""

    name = "bedrock"
    DEFAULT_MODEL = "apac.amazon.nova-lite-v1:0"
    DEFAULT_REGION = "ap-south-1"

    def __init__(self, client=None, on_usage=None):
        self._client = client
        self.on_usage = on_usage

    @classmethod
    def model_id(cls) -> str:
        return env("BEDROCK_MODEL_ID", cls.DEFAULT_MODEL)

    @classmethod
    def region(cls) -> str:
        return env("AWS_REGION") or env("AWS_DEFAULT_REGION") or cls.DEFAULT_REGION

    def client(self):
        if self._client is None:
            import boto3
            from botocore.config import Config

            config = Config(region_name=self.region(), connect_timeout=10, read_timeout=timeout_seconds(),
                            retries={"total_max_attempts": 1, "mode": "standard"})
            try:
                self._client = boto3.client("bedrock-runtime", config=config)
            except Exception as exc:  # noqa: BLE001 — e.g. ProfileNotFound
                raise ConfigError(f"Could not create the Bedrock client ({type(exc).__name__}): "
                                  "check AWS_PROFILE / AWS_REGION") from None
        return self._client

    @staticmethod
    def request(system_prompt, user_content, *, max_tokens, json_schema, extra_messages, model_id):
        """Converse needs alternating roles, so correction notes are extra
        text blocks in the one user turn.

        Structured output uses tool use: the operation's schema is the
        input schema of a single tool the model is forced to call. Asked
        for JSON in text, Nova Lite returned schema-shaped objects
        ({"properties": {...answer...}, "type": "object"} or the schema
        itself) for 6 of 10 key prompts; validation and the retry stay as
        the backstop."""
        json_mode = json_schema is not None
        blocks = [{"text": user_content}] + [{"text": note} for note in extra_messages]
        request = {
            "modelId": model_id,
            "messages": [{"role": "user", "content": blocks}],
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": 0.0 if json_mode else 0.3},
        }
        if system_prompt:
            request["system"] = [{"text": system_prompt}]
        if json_mode and json_schema.get("properties"):
            name = TOOL_NAME_UNSAFE.sub("_", json_schema.get("title") or "")[:64] or "respond"
            request["toolConfig"] = {
                "tools": [{"toolSpec": {"name": name, "description": "Return the result.",
                                        "inputSchema": {"json": json_schema}}}],
                "toolChoice": {"tool": {"name": name}},
            }
        elif json_mode:
            blocks.append({"text": "Return only the JSON object, with no other text."})
        return request

    def generate(self, system_prompt, user_content, *, max_tokens, json_schema=None, extra_messages=()):
        from botocore.exceptions import (BotoCoreError, ClientError, ConnectTimeoutError,
                                         EndpointConnectionError, NoCredentialsError, ReadTimeoutError)

        request = self.request(system_prompt, user_content, max_tokens=max_tokens,
                               json_schema=json_schema, extra_messages=extra_messages,
                               model_id=self.model_id())
        try:
            response = self.client().converse(**request)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            is_config, message = BEDROCK_ERRORS.get(code, (False, f"Bedrock error ({code or 'unknown'})"))
            raise (ConfigError if is_config else BackendError)(message) from None
        except (ReadTimeoutError, ConnectTimeoutError):
            raise BackendError(f"Bedrock timed out after {timeout_seconds():.0f}s") from None
        except NoCredentialsError:
            raise ConfigError("No AWS credentials found: set AWS_PROFILE locally, or give the "
                              "service an IAM role") from None
        except EndpointConnectionError:
            raise BackendError(f"Could not reach Bedrock in {self.region()}") from None
        except BotoCoreError as exc:
            raise BackendError(f"Bedrock request failed ({type(exc).__name__})") from None
        if self.on_usage:
            self.on_usage({**response.get("usage", {}), "latencyMs": response.get("metrics", {}).get("latencyMs"),
                           "stopReason": response.get("stopReason")})
        return extract_bedrock_text(response)


def extract_bedrock_text(response: Any) -> str:
    """The forced tool call's input as JSON text, else the joined text
    blocks of output.message.content; anything else is an error. A reply
    cut off at maxTokens is returned as is (validation then rejects
    truncated JSON)."""
    try:
        blocks = response["output"]["message"]["content"]
    except (KeyError, TypeError):
        raise BackendError("Unrecognised Bedrock response shape") from None
    for block in blocks:
        tool_use = block.get("toolUse") if isinstance(block, dict) else None
        if isinstance(tool_use, dict) and isinstance(tool_use.get("input"), dict):
            return json.dumps(tool_use["input"])
    texts = [b["text"] for b in blocks if isinstance(b, dict) and isinstance(b.get("text"), str)]
    if not texts:
        raise BackendError("Bedrock response had no text")
    return "".join(texts)


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
    "bedrock": BedrockBackend,
    "azure": AzureOpenAIBackend,
    "mock": MockBackend,
}
