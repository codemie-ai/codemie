# Handoff — EPMCDME-12240 AgentCore Vendor Installations

**Branch:** `EPMCDME-12240_agentcore`  
**Base:** `main` (42 commits ahead)  
**Tests:** 205 pass, 0 fail (service + repository layer)

---

## What this branch delivers

A complete install/uninstall flow for AWS Bedrock AgentCore runtime **endpoints** surfaced as a new vendor entity type (`agentcore-runtime-endpoints`) alongside the existing runtimes entity (`agentcore-runtimes`).

### Key new pieces

| File | What it does |
|---|---|
| `src/codemie/rest_api/models/vendor.py` | `InstallationState` enum (`not_installed`, `installed`, `version_drift`, `deleted_on_aws`); `ImportAgentcoreRuntimeEndpoint` request model |
| `src/codemie/repository/vendor_installation_repository.py` | `VendorInstallationRepository` — upsert, get_by_id, get_by_entity; tracks per-vendor-entity installation state in Postgres |
| `src/codemie/service/aws_bedrock/agentcore_runtime_service.py` | AWS API wrappers `_bedrock_list_runtime_endpoints`, `_bedrock_get_runtime_endpoint` added to `BedrockAgentCoreRuntimeService` |
| `src/codemie/service/aws_bedrock/agentcore_runtime_endpoints_service.py` | `AgentCoreRuntimeEndpointsService` — implements `BaseBedrockService`; merged list (AWS + local DB), import (install), delete by id (uninstall) |
| `src/alembic/versions/*_add_vendor_entity_installation_table.py` | Migration adding `vendor_entity_installation` table |
| `src/codemie/rest_api/routers/vendor.py` | Generic vendor router extended: `**kwargs` forwarded from query params to `list_main_entities`; `page` removed; new install/uninstall routes |

### Entities enum entry

`Entities.AWS_AGENTCORE_RUNTIME_ENDPOINTS = "agentcore-runtime-endpoints"` added to the vendor models and wired into `SERVICE_MAPPING`.

---

## Uncommitted changes (need one more commit)

The following files have local changes that are **not yet committed**. All tests pass; they just need to be staged and committed.

```
M src/codemie/repository/vendor_installation_repository.py
M src/codemie/service/aws_bedrock/agentcore_runtime_endpoints_service.py
M src/codemie/service/aws_bedrock/bedrock_guardrail_service.py
M src/codemie/service/aws_bedrock/bedrock_knowledge_base_service.py
M tests/codemie/service/aws_bedrock/test_bedrock_agent_service.py
M tests/codemie/service/aws_bedrock/test_bedrock_flow_service.py
M tests/codemie/service/aws_bedrock/test_bedrock_guardrail_service.py
```

**Summary of what those changes contain:**

- `agentcore_runtime_endpoints_service.py` — full refactor completed in this session:
  - Module constants renamed: `_VENDOR` → `VENDOR`, `_ENTITY_TYPE` → `ENTITY_TYPE`, `_RUNTIME_ID_PARAM` → `RUNTIME_ID_PARAM`
  - `EndpointListItem(TypedDict)` return type (replaces opaque `dict`)
  - `InstallationState` enum used throughout (replaces raw strings)
  - `page` parameter removed from `list_main_entities` / `list_importable_entities_for_main_entity`
  - `**kwargs` pattern for `runtime_id` query param
  - Single `get_session()` block for batch `deleted_on_aws` upserts
  - Unsupported stubs moved to bottom of class, just before private helpers
  - snake_case keys in all result dicts
  - Docstrings on public methods
- `bedrock_guardrail_service.py`, `bedrock_knowledge_base_service.py` — `get_all_settings_overview` signature made `page: int = 0` (was required positional)
- `vendor_installation_repository.py` — minor cleanup
- Test files — `page=0` removed from `get_all_settings_overview` call sites

**Suggested commit message:**

```
refactor: clean up agentcore-runtime-endpoints service and fix page param signatures

- Rename module constants (remove _ prefix)
- Use EndpointListItem TypedDict and InstallationState enum throughout
- Remove page from list_main_entities; forward runtime_id via **kwargs
- Make page optional (default=0) in get_all_settings_overview across all services
- Move unsupported stubs to bottom of AgentCoreRuntimeEndpointsService
```

---

## Architecture notes for the next agent

### `**kwargs` pattern in `list_main_entities`

The vendor router collects any query params not in `{"setting_id", "per_page", "next_token"}` and forwards them as `**kwargs` to `service.list_main_entities(...)`. `AgentCoreRuntimeEndpointsService` pulls `runtime_id` from kwargs; all other services ignore kwargs. This is how entity-specific params (like `runtime_id`) are passed without polluting the base interface.

### `BaseBedrockService` interface (relevant methods)

```python
def get_all_settings_overview(user, per_page, page=0)  # offset pagination, page optional
def list_main_entities(user, setting_id, per_page, next_token=None, **kwargs)  # cursor pagination
def list_importable_entities_for_main_entity(user, main_entity_id, setting_id, per_page, next_token=None)
def import_entities(user, import_payload)
def delete_entity_by_id(entity_id, setting_id, user)
```

### `InstallationState` values

| Value | Meaning |
|---|---|
| `not_installed` | No local record or explicitly uninstalled |
| `installed` | Local version matches live AWS version |
| `version_drift` | Local version differs from live AWS version |
| `deleted_on_aws` | Was installed locally; endpoint no longer exists in AWS |

### DB table: `vendor_entity_installation`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `setting_id` | str | Links to AWS integration setting |
| `vendor` | str | e.g. `"aws"` |
| `entity_type` | str | e.g. `"agentcore-runtime-endpoints"` |
| `entity_id` | str | Parent entity (runtime id) |
| `sub_entity_id` | str | The endpoint name |
| `state` | str | `InstallationState` value |
| `resource_id` | str | Optional CodeMie assistant/resource id |
| `version` | str | Version at install time |
| `vendor_metadata` | JSON | Snapshot of AWS metadata at install time |

### API endpoints added

```
GET    /v1/vendors/aws/agentcore-runtime-endpoints?setting_id=&runtime_id=&per_page=&next_token=
POST   /v1/vendors/aws/agentcore-runtime-endpoints/import
DELETE /v1/vendors/aws/agentcore-runtime-endpoints/{installation_id}?setting_id=
```

---

## What is NOT done (out of scope for this branch)

- Frontend UI for the merged runtime list
- Any "update available" notification beyond the `version_drift` state flag
- Cleanup/reconciliation of `deleted_on_aws` records older than N days
