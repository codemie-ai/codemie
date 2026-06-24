# AgentCore Runtime Endpoint Entity Import

## Context

Bedrock AgentCore runtimes are AWS entities imported into AI.Run as `Assistant` records with
`type = BEDROCK_AGENTCORE_RUNTIME`. An agentcore runtime has one or more **endpoints**; the
endpoint is the importable unit (not the runtime itself). Each imported endpoint is stored as
an `Assistant` with a `bedrock_agentcore_runtime` JSONB field (`BedrockAgentcoreRuntimeData`).

The vendor router at `src/codemie/rest_api/routers/vendor.py` provides generic import/delete
routes for all AWS entity types. `BedrockAgentCoreRuntimeService` in
`src/codemie/service/aws_bedrock/bedrock_agentcore_runtime_service.py` implements the
agentcore-specific logic.

## What Already Works

- `GET /v1/vendors/aws/agentcore-runtimes` — lists agentcore runtimes (main entities)
- `GET /v1/vendors/aws/agentcore-runtimes/{runtimeId}/endpoints` — lists endpoints for a
  runtime, marks already-imported ones with `aiRunId`
- `GET /v1/vendors/aws/agentcore-runtimes/{runtimeId}/{endpointName}` — returns detailed
  AWS-side endpoint info
- `POST /v1/vendors/aws/agentcore-runtimes` — imports an endpoint; validates invocation JSON,
  fetches AWS endpoint, checks READY status, creates or updates `Assistant`
- `DELETE /v1/vendors/aws/agentcore-runtimes/{localId}` — deletes an imported entity

## Gaps and Fixes

### 1. `/endpoints` list missing `invocationJson` for already-imported endpoints

**File:** `bedrock_agentcore_runtime_service.py` → `list_importable_entities_for_main_entity`

When an endpoint is already imported, the list currently adds only `aiRunId`. The frontend
needs `invocationJson` to pre-fill the import form on re-import.

**Fix:** when `endpoint_id` is in `existing_entities_map`, also include `invocationJson` from
`assistant.bedrock_agentcore_runtime.invocation_json`.

Response shape per endpoint item (already-imported):
```json
{
  "id": "endpoint-id",
  "name": "endpoint-name",
  "status": "PREPARED",
  "description": "...",
  "liveVersion": "...",
  "targetVersion": "...",
  "createdAt": "...",
  "updatedAt": "...",
  "aiRunId": "local-assistant-uuid",
  "invocationJson": "{\"message\": \"__QUERY_PLACEHOLDER__\"}"
}
```

### 2. `get_importable_entity_detail` missing import status

**File:** `bedrock_agentcore_runtime_service.py` → `get_importable_entity_detail`

Currently returns only AWS-side fields. The single-endpoint detail view also needs to show
whether the endpoint is already imported.

**Fix:** after fetching the AWS endpoint, look up existing assistants for the setting by
`runtime_endpoint_id`. If matched, add `aiRunId` and `invocationJson` to the response.

Response shape (already-imported):
```json
{
  "id": "endpoint-id",
  "name": "endpoint-name",
  "status": "PREPARED",
  "description": "...",
  "liveVersion": "...",
  "targetVersion": "...",
  "agentRuntimeEndpointArn": "arn:...",
  "agentRuntimeArn": "arn:...",
  "failureReason": null,
  "createdAt": "...",
  "updatedAt": "...",
  "aiRunId": "local-assistant-uuid",
  "invocationJson": "{\"message\": \"__QUERY_PLACEHOLDER__\"}"
}
```

### 3. `import_vendor_entities` — no change needed

`_process_endpoint_import` is complete: validates `invocation_json`, fetches endpoint from
AWS, enforces READY status, creates or updates `Assistant` with full
`BedrockAgentcoreRuntimeData`. The `import_entities` dispatcher is correctly wired via
`ENTITY_KEY_MAP` / `ENTITY_MODEL_MAP`.

### 4. `delete_vendor_entity` — missing guardrail cleanup for agentcore

**File:** `vendor.py` → `delete_vendor_entity`

The agentcore branch falls into the `else: entity_model.delete()` path. Unlike the bulk
`delete_entities` method in the service (which calls
`GuardrailService.remove_guardrail_assignments_for_entity` after delete), the router does
not. This leaves orphaned guardrail assignments.

This is resolved by the refactor in section 5.

### 5. Refactor: `unimport_entity(entity_id, user)` per service (Approach B)

Add `unimport_entity(entity_id: str, user: User) -> None` as an abstract method on
`BaseBedrockService`. Each of the five service classes implements it: entity lookup → 404 if
missing → permission check (`Ability(user).can(Action.DELETE, entity_model)`) → 403 if
denied → type-specific cleanup + delete. Services raise `ExtendedHTTPException` directly
(no router utilities imported into service layer).

`delete_vendor_entity` in the router becomes:

```python
service = get_service_or_404(origin, entity)
service.unimport_entity(entity_id, user)
return {"success": True}
```

Per-service delete logic:

| Service | Entity lookup | Cleanup before delete |
|---|---|---|
| `BedrockAgentService` | `Assistant.find_by_id` | `GuardrailService.remove_guardrail_assignments_for_entity` |
| `BedrockAgentCoreRuntimeService` | `Assistant.find_by_id` | `GuardrailService.remove_guardrail_assignments_for_entity` |
| `BedrockKnowledgeBaseService` | `IndexInfo.find_by_id` | none |
| `BedrockFlowService` | `WorkflowService().get_workflow` | `WorkflowService().delete_workflow` (replaces `.delete()`) |
| `BedrockGuardrailService` | `Guardrail.find_by_id` | `GuardrailService.remove_guardrail_assignments_for_guardrail` |

The `workflow_service` module-level instance in the router is removed once all workflow
deletion moves into `BedrockFlowService.unimport_entity`.

## Frontend API Contract

Full agentcore endpoint import flow:

### List runtimes
```
GET /v1/vendors/aws/agentcore-runtimes?setting_id={settingId}&page=0&per_page=12
```
Response: `{data: [{id, name, status, description, version, updatedAt}], pagination: {next_token}}`

### List endpoints for a runtime
```
GET /v1/vendors/aws/agentcore-runtimes/{runtimeId}/endpoints?setting_id={settingId}
```
Response: `{data: [{id, name, status, description, liveVersion, targetVersion, createdAt,
updatedAt, aiRunId?, invocationJson?}], pagination: {next_token}}`

`aiRunId` and `invocationJson` are present only when the endpoint is already imported.

### Get endpoint detail
```
GET /v1/vendors/aws/agentcore-runtimes/{runtimeId}/{endpointName}?setting_id={settingId}
```
Response: `{id, name, status, description, liveVersion, targetVersion, agentRuntimeEndpointArn,
agentRuntimeArn, failureReason, createdAt, updatedAt, aiRunId?, invocationJson?}`

### Import endpoint
```
POST /v1/vendors/aws/agentcore-runtimes
Body: [
  {
    "setting_id": "...",
    "id": "<agentRuntimeId>",
    "agentcoreRuntimeEndpointName": "<endpointName>",
    "invocation_json": "{\"message\": \"__QUERY_PLACEHOLDER__\", \"sessionId\": \"...\"}"
  }
]
```
Success response per item: `{runtimeId, endpointName, aiRunId}`
Error response per item: `{runtimeId, endpointName, error: {statusCode, message}}`

The `invocation_json` must be valid JSON containing the string
`__QUERY_PLACEHOLDER__` as a value somewhere in the structure.

Endpoint must be in READY/PREPARED status to import. Re-importing an existing endpoint
(same `setting_id` + `runtimeId` + `endpointName`) updates the record rather than creating
a duplicate.

### Delete imported endpoint
```
DELETE /v1/vendors/aws/agentcore-runtimes/{aiRunId}
```
Response: `{success: true}`

`{aiRunId}` is the local AI.Run `Assistant` ID returned in the import summary. The delete
also removes all guardrail assignments for the deleted assistant.
