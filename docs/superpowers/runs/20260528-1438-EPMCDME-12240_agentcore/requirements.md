# Requirements — 20260528-1438-EPMCDME-12240_agentcore

**Source**: local-work-item:docs/superpowers/work-items/agentcore-invocation-fix-and-config-expansion.md
**Work Item**: docs/superpowers/work-items/agentcore-invocation-fix-and-config-expansion.md
**Original input**: |
  Given we have AgentCore endpoints registered (see docs/agentcore-runtimes-api.md), do the following:
  1. Investigate how the assistants are registered (how imported AgentCore endpoints become CodeMie assistants/AI runs)
  2. Fix how the assistants are called on AWS - currently it does not work
  3. Investigate how to expand the configuration_json for assistants: it should include whether the response
     is an HTTP stream or not, the field or JSON path for response, thoughts, chunks etc.

## Goal

Fix broken AWS AgentCore endpoint invocation and extend the invocation config schema to let users
control streaming vs JSON mode and configure field paths for response, thoughts, and chunks.

## Investigation Findings

### How assistants are registered (import flow)

1. **API entry**: `POST /v1/vendors/aws/agentcore-runtimes` in `src/codemie/rest_api/routers/vendor.py:359`
   — body is `list[ImportAgentcoreRuntime]` (each item has `setting_id`, `id`, `agentcoreRuntimeEndpointName`,
   `configuration_json`).

2. **Service dispatch**: `BedrockAgentCoreRuntimeService.import_entities()` (line 270) →
   `BedrockAgentCoreEndpointService.import_entities()`.

3. **Core import** (`_process_endpoint_import()`, endpoint_service.py:373):
   - Validates `configuration_json` — must be valid JSON and contain `__QUERY_PLACEHOLDER__` as a value.
   - Calls `get_agent_runtime_endpoint` (boto3 `bedrock-agentcore-control`) to confirm the endpoint
     is `READY`.
   - Builds `BedrockAgentcoreRuntimeData` and upserts an `Assistant` with
     `type=BEDROCK_AGENTCORE_RUNTIME`.

4. **Stored fields** (in `bedrock_agentcore_runtime` JSONB column):
   - `runtime_id`, `runtime_arn` (= `agentRuntimeArn` from AWS)
   - `runtime_endpoint_id`, `runtime_endpoint_arn` (= `agentRuntimeEndpointArn` from AWS)
   - `runtime_endpoint_name`, `runtime_endpoint_live_version`
   - `aws_settings_id`, `invocation_json` (= user's `configuration_json`)

5. **Idempotent**: re-import updates the existing assistant keyed on `(aws_settings_id, runtime_id, runtime_endpoint_id)`.

### How assistants are called (invocation flow)

1. `assistant_service.py:509` → `BedrockOrchestratorService.invoke_bedrock_assistant()`
2. → `BedrockAgentCoreRuntimeService.invoke_agentcore_runtime()` (runtime_service.py:353)
3. Replaces `__QUERY_PLACEHOLDER__` in `invocation_json` with the user's query.
4. → `_bedrock_invoke_runtime()` → `client.invoke_agent_runtime(agentRuntimeArn=..., qualifier=..., ...)`
5. Response is parsed by content-type: `text/event-stream` → SSE line parser; `application/json` → JSON `.get("response")`.

## Acceptance Criteria

### AC1 — Fix invocation guard
The guard in `invoke_agentcore_runtime()` (runtime_service.py:361) checks
`runtime_endpoint_arn` but the actual boto3 call uses `runtime_arn`. The guard must
check the same field that the invocation uses (`runtime_arn`) to avoid a false-safe pass
that masks a missing ARN at call time.

### AC2 — Configurable accept header (is_stream)
Add an `is_stream: bool` option to the stored response config (default: `False` → JSON).
When `is_stream=True`, send `accept="text/event-stream"`; when `False`, send `accept="application/json"`.
Currently the service always sends `text/event-stream`, which fails for endpoints
that return `application/json`.

### AC3 — Configurable response field path
Add `response_path: Optional[str]` (default: `"response"`) to the response config.
The JSON response parser must use this value as the key/path to extract the answer text
instead of the hardcoded `response_json.get("response", ...)`.

### AC4 — Configurable thought field path
Add `thought_path: Optional[str]` (default: `None`) to the response config.
When set, extract and surface reasoning/thinking content from that key/path in the JSON
response or SSE chunk payload.

### AC5 — Configurable chunk field path (streaming)
Add `chunk_path: Optional[str]` (default: `None`) to the response config.
When set, the SSE line parser must parse each SSE data line as JSON and extract
the text from `chunk_path` rather than treating the raw data line as text.

### AC6 — Import API updated
The `ImportAgentcoreRuntime` request model gains an optional `response_config` block:
```json
{
  "setting_id": "...",
  "id": "...",
  "agentcoreRuntimeEndpointName": "...",
  "configuration_json": "{\"message\": \"__QUERY_PLACEHOLDER__\"}",
  "response_config": {
    "is_stream": false,
    "response_path": "response",
    "thought_path": null,
    "chunk_path": null
  }
}
```
`response_config` is fully optional; omitting it means all defaults apply.

### AC7 — DB model updated
`BedrockAgentcoreRuntimeData` gains `response_config: Optional[AgentcoreResponseConfig] = None`.
The `AgentcoreResponseConfig` Pydantic model has:
- `is_stream: bool = False`
- `response_path: Optional[str] = "response"`
- `thought_path: Optional[str] = None`
- `chunk_path: Optional[str] = None`
All fields are optional with defaults, so existing JSONB records remain valid without migration.

### AC8 — Endpoint detail API exposes response_config
`GET /v1/vendors/aws/agentcore-runtimes/{runtimeId}/{endpointName}` response entity
includes `responseConfig` (camelCase) alongside existing `configurationJson`.

## Context

- Current branch: `EPMCDME-12240_agentcore` — this work extends that branch.
- Key files:
  - `src/codemie/rest_api/models/assistant.py` — `BedrockAgentcoreRuntimeData` (line 252)
  - `src/codemie/rest_api/models/vendor.py` — `ImportAgentcoreRuntime` (line 40)
  - `src/codemie/service/aws_bedrock/bedrock_agentcore_runtime_service.py` — invocation (line 353)
  - `src/codemie/service/aws_bedrock/agentcore/bedrock_agentcore_endpoint_service.py` — import + parsing (line 373)
  - `src/codemie/rest_api/routers/vendor.py` — API router (line 359)
- The `invocation_json` is the request payload template; response config is separate metadata.
- No DB migration needed if all new fields are `Optional` with defaults in the JSONB schema.

## Open questions

(none)
