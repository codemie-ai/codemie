# AgentCore Runtime Endpoints — Flat Entity Refactor

## Goal

Replace the nested `/endpoints/installations` sub-resource API with a flat `agentcore-runtime-endpoints` entity type that follows the existing vendor entity pattern (`assistants`, `guardrails`, `knowledgebases`, `workflows`). Remove `AgentCoreEndpointInstallationService` and integrate installation state tracking (`VendorEntityInstallation`) into the existing import/delete flow.

## Background

The current implementation introduced:
- 4 nested routes: `GET/POST/PUT/DELETE /vendors/aws/agentcore-runtimes/{id}/endpoints/installations`
- A separate `AgentCoreEndpointInstallationService` class
- An isolated installation CRUD that duplicates the existing vendor import/delete pattern

This is inconsistent with how agents, flows, guardrails, and knowledge bases work. The frontend already has `installVendorEntity` (`POST /vendors/{origin}/{entity}`) which should be reused.

## Design

### New entity type

Add `AWS_AGENTCORE_RUNTIME_ENDPOINTS = "agentcore-runtime-endpoints"` to the `Entities` enum in `src/codemie/rest_api/models/vendor.py`.

### Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/vendors/aws/agentcore-runtime-endpoints?setting_id=X&runtime_id=Y` | List endpoints for a runtime, enriched with installation state |
| `POST` | `/vendors/aws/agentcore-runtime-endpoints` | Install an endpoint (creates CodeMie assistant + writes VendorEntityInstallation) |
| `DELETE` | `/vendors/aws/agentcore-runtime-endpoints/{id}` | Uninstall (deletes assistant + updates VendorEntityInstallation state to `not_installed`) |

The GET takes `runtime_id` as a query param (instead of a URL path segment) since endpoints are sub-entities of runtimes.

### Installation state integration

`VendorEntityInstallation` rows are written by the existing import/delete flow:

- **On import success** (`_process_endpoint_import`): async-upsert row with `state=installed`, `resource_id=<assistant_uuid>`, `version=liveVersion`, `vendor_metadata=endpoint_info`
- **On delete** (`delete_vendor_entity` router): async-upsert row with `state=not_installed`, `resource_id=None`, `version=None`

Fields mapping:
- `setting_id` = integration setting ID
- `vendor` = `"aws"`
- `entity_type` = `"agentcore-runtime-endpoints"`
- `entity_id` = runtime ID
- `sub_entity_id` = endpoint name (e.g. `"DEFAULT"`)

### GET enrichment

`list_installable_entities` enriches each endpoint dict with its `VendorEntityInstallation` row:
- `installation_state`: `not_installed | installed | version_drift`
- `installation_id`: UUID of the installation row
- `ai_run_id`: CodeMie assistant UUID (if installed)

Version drift is detected when `version != liveVersion` and `resource_id` is set.

### Removals

- All 4 `/vendors/{origin}/{entity}/{id}/endpoints/installations` routes
- `/vendors/{origin}/{entity}/{id}/endpoints` route (replaced by flat GET)
- `AgentCoreEndpointInstallationService` (`src/codemie/service/aws_bedrock/agentcore_endpoint_installation_service.py`)
- `SERVICE_MAPPING` entry for `AWS_AGENTCORE_RUNTIMES` endpoints sub-routing

### Service mapping

```python
SERVICE_MAPPING[Vendor.AWS][Entities.AWS_AGENTCORE_RUNTIME_ENDPOINTS] = BedrockAgentCoreRuntimeService
ENTITY_KEY_MAP[Entities.AWS_AGENTCORE_RUNTIME_ENDPOINTS] = "agentcoreRuntimeEndpointName"
ENTITY_MODEL_MAP[Entities.AWS_AGENTCORE_RUNTIME_ENDPOINTS] = ImportAgentcoreRuntime
```

### Async consideration

`_process_endpoint_import` is currently sync. The `VendorInstallationRepository.upsert` is async. The upsert will be called using `asyncio.run()` from a new async wrapper, or `_process_endpoint_import` will be converted to async. Since `import_entities` is called from a sync FastAPI route, we'll use `asyncio.get_event_loop().run_until_complete()` pattern or convert the route to async.

## Out of scope

- `update_installation` (PUT) — not needed in the new design; version drift is detected on read
- Changes to the `VendorEntityInstallation` table schema or Alembic migration
- Changes to how runtimes themselves are listed (`agentcore-runtimes` entity remains unchanged)
