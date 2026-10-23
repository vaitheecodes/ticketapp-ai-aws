# Calling a QuickML LLM endpoint from AppSail (verified 23 Oct 2026)

How a Python AppSail service calls a Catalyst QuickML LLM Serving endpoint
without OAuth or a self client, using the credentials AppSail injects into
every incoming request. Verified from the throwaway `quickmlsdktest`
service (project ticket-ai, India DC, Development environment, endpoint
`glm-flash-47`, model GLM-4.7-Flash, conversation mode off). No secrets in
this file.

## Why not the SDK method

The docs' `quickml.ask_llm(endpoint_key, prompt)` /
`converse_with_llm(...)` (Python SDK page, last updated 2026-09-09) are
**not in any published SDK**: zcatalyst-sdk 1.0.0rc7 through 1.4.0 (the
latest on PyPI, incl. pre-releases) only have `predict()`, and the JS SDK
(`catalystbyzoho/zcatalyst-sdk-js`) is the same. So we call the REST URL
ourselves.

## The request that worked

```
POST {X_ZOHO_CATALYST_CONSOLE_URL}/quickml/v1/project/{project_id}/genai/endpoints/{endpoint_name}/generate
```

Inside AppSail (India DC) that resolved to
`https://api.catalyst.zoho.in/quickml/v1/project/<project_id>/genai/endpoints/glm-flash-47/generate`.
(The console shows the same path under `console.catalyst.zoho.in`; from
AppSail the SDK used `api.catalyst.zoho.in`, which worked.)

| Part | Value | Where it comes from |
|---|---|---|
| Base URL | `https://api.catalyst.zoho.in` | env `X_ZOHO_CATALYST_CONSOLE_URL`, set by AppSail |
| `project_id` | numeric project id | request header `X-ZC-ProjectId` (injected by AppSail) |
| `endpoint_name` | e.g. `glm-flash-47` | the endpoint's URL in QuickML > LLM Serving; config (env `QUICKML_ENDPOINT_NAME`), not secret |
| `Authorization` | `Zoho-oauthtoken <token>` | request header `X-ZC-Admin-Cred-Token` (with `X-ZC-Admin-Cred-Type` = access token), injected by AppSail per request |
| `CATALYST-ORG` | org id | env `CATALYST_ORG_ID` (not secret; also in `.catalystrc`). **Required.** |
| `Environment` | `Development` or `Production` | env `QUICKML_ENVIRONMENT`; match the AppSail environment |
| `X-QUICKML-ENDPOINT-KEY` | endpoint key | env `QUICKML_ENDPOINT_KEY` (secret; set in the console only) |
| `Content-Type` | `application/json` | — |

How the test made the call: `zcatalyst_sdk.initialize(req=request, scope="admin")`
reads the `X-ZC-*` headers, then the SDK's internal
`zcatalyst_sdk._http_client.AuthorizedHttpClient(app).request("POST",
path="/genai/endpoints/<name>/generate", user=CredentialUser.ADMIN,
catalyst_service=CatalystService.QUICK_ML, json=..., headers=...)` adds
the `Authorization` header and builds the URL above. That class is not a
public API; for production code, build the same request directly from the
injected headers (above) so an SDK change can't break it.

**Headers the SDK actually sent on the verified call** (read from
zcatalyst-sdk 1.4.0's `HttpClient.request`, added on top of the table
above). It's not known which of these QuickML requires, so ticketapp-ai's
`quickml_appsail` backend sends the same set:

| Header | Value source |
|---|---|
| `PROJECT_ID` | `X-ZC-Project-Key` request header |
| `X-Catalyst-Environment`, `environment` | `X-ZC-Environment` request header (this overrode the `Environment: Development` the test passed — header names are case-insensitive) |
| `X-CATALYST-USER` | `admin` |
| `X-ZC-PROJECT-SECRET-KEY` | `X-ZC-Project-Secret-Key` request header (secret; forwarded, never logged) |
| `USER-AGENT` | `zc-python-sdk/1.4.0` (not reproduced) |

The admin credential type comes from `X-ZC-Admin-Cred-Type`: `token` →
`Zoho-oauthtoken <token>`, `ticket` → `Zoho-ticket <ticket>`.

### Request body

```json
{"prompt": "<one complete prompt string>"}
```

One stateless prompt per call (conversation mode off): system
instructions, fenced data and any history go into this single string.
Temperature, max tokens and the endpoint's own instructions are fixed in
the console and can't be overridden per request.

### Response (HTTP 200)

```json
{
  "data": [
    {"data": "<the model's raw text output, as a string>"}
  ],
  "finish_reason": "stop",
  "model": "crm-di-glm47b_30b_it",
  "usage": {
    "completion_tokens": 15,
    "prompt_tokens": 53,
    "prompt_tokens_details": null,
    "total_tokens": 68
  }
}
```

- The generated text is **`data[0].data`** (a string). This differs from
  the SDK docs' sample (`{status, result:[{content, finish_reason, usage}]}`)
  — parse the observed shape and treat anything else as an error.
- When asked for JSON, `data[0].data` is the JSON text itself (e.g.
  `"{\"category\": \"AGENT\", \"confidence\": 0.9}"`); parse it again.
  For a plain question the model may still answer in JSON (one test
  returned `{"response": "Hello, ..."}`) because of the endpoint
  instructions — don't assume prose.
- Usage field names are `prompt_tokens` / `completion_tokens` /
  `total_tokens` (not `input_tokens` / `output_tokens` as in the docs).
- No reasoning (`<think>`) text appeared in the two test calls; keep
  stripping it defensively.
- Observed latency: a few seconds per short call.

## Development vs production

- Use the endpoint key and `Environment` header value for the environment
  the AppSail service runs in (this test: Development).
- AppSail environment variables are stored per environment in the console
  (ticket-ai > environment switch > Serverless > AppSail > service >
  Configuration > Environment Variables). Production variables can only be
  added after the service has been deployed to production.
- The injected credentials and `X_ZOHO_CATALYST_CONSOLE_URL` come from the
  environment serving the request, so no code change is needed between them.
- Not verified here: the production endpoint key, and whether a later
  `catalyst deploy` keeps console-set variables (this test never
  redeployed after setting them).

## Failure modes seen

| Response | Meaning | Fix |
|---|---|---|
| 400 `ORGID_HEADER_UNAVAILABLE` (`ziahub.error.ORGID_HEADER_UNAVAILABLE`) | Path and auth accepted, `CATALYST-ORG` header missing | Send `CATALYST-ORG: <org id>` |
| 400 `Required header 'x-quickml-endpoint-key' is not present.` | Everything else accepted, key header missing | Send `X-QUICKML-ENDPOINT-KEY` |
| 500 `INVALID_ENDPOINT_KEY` (`ziahub.error.INVALID_ENDPOINT_KEY`) | Key doesn't match this endpoint (wrong/stale value, stray whitespace, other endpoint or environment) | Re-copy the key for this endpoint and environment; note it's a 500 even though it's a client error |
| `AttributeError: 'QuickML' object has no attribute 'ask_llm'` | Published SDK lacks the documented method | Use the REST call above |

Errors come back as JSON with `code` and `message`; the SDK raises
`CatalystAPIError` with the HTTP status in `status_code`. Treat any
non-200 or unexpected shape as "AI unavailable" — never as a result.

## Still to check in the migration

- Background tasks (e.g. incident correlation after the response is sent)
  reuse the request's injected admin token; confirm it's still valid then.
- Calls outside a request (no injected headers) need another credential —
  for local development use the OAuth self-client provider (`quickml_rest`).
- Rate limits, max prompt size and timeouts for longer prompts weren't tested.
