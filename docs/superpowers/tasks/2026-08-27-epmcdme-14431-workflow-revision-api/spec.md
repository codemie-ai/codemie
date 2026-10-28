# Spec: Workflow Revision Listing and Rollback API (EPMCDME-14431)

**Date**: 2026-08-27 (updated 2026-09-24 to match shipped code)
**Branch**: EPMCDME-14431_workflow-revision-api-v2
**Scope**: REST API only. SDK is out of scope.

---

## Overview

Extend workflow versioning (EPMCDME-8827) by exposing two new REST API endpoints:

1. `GET /v1/workflows/{workflow_id}/versions` — list prior revisions from a workflow's `yaml_config_history`
2. `POST /v1/workflows/{workflow_id}/versions/rollback` — synchronously roll a workflow back to a prior revision

The design mirrors the existing Assistant versioning API pattern (`AssistantVersionService`, endpoints at `/v1/assistants/{id}/versions` and `/rollback`): access failures are 401, bad revision identifiers are 400, and there is no gate on the entity's state. No new database columns or migrations are required.

---

## Background

`WorkflowConfig` stores a `yaml_config` field (current active YAML) and a `yaml_config_history: List[YamlConfigHistory]` JSONB column (immutable prepend-on-update history). Each `YamlConfigHistory` entry has:
- `yaml_config: str` — the config snapshot
- `date: datetime` — when the snapshot was created
- `created_by: Optional[UserEntity]` — who made the change
- `change_notes: str | None` — **new in this story**; optional note recorded on rollback. Defaults to `None`, so existing JSONB rows deserialize unchanged.

Currently this history is only accessible through the workflow editing UI (EPMCDME-8827). This spec adds programmatic access.

---

## New Components

### 1. Response / Request Models — `src/codemie/rest_api/models/workflow_version.py`

```python
CHANGE_NOTES_MAX_LENGTH = 1000


class WorkflowVersionHistoryResponse(BaseModel):
    revisions: list[YamlConfigHistory]
    total_revisions: int
    workflow_name: str
    workflow_id: str


class WorkflowRollbackRequest(BaseModel):
    revision_index: int  # 0-based index into yaml_config_history (0 = most recent prior revision)
    change_notes: str | None = Field(default=None, max_length=CHANGE_NOTES_MAX_LENGTH)
```

There is no `revision_date` field. An earlier draft used `date` as the rollback identifier; the shipped contract is the positional index.

`YamlConfigHistory` is imported from `codemie.core.workflow_models.workflow_config` and reused as the revision item type.

### 2. Service — `src/codemie/service/workflow/workflow_version_service.py`

New file inside a new `src/codemie/service/workflow/` package (`__init__.py` holds only the license header). The module also owns the `WORKFLOW_CONFIGURATION_ERROR = "Workflow Configuration error"` constant, which the workflow router now imports instead of defining it.

```python
class WorkflowVersionService:
    @classmethod
    def get_version_history(cls, workflow: WorkflowConfig) -> WorkflowVersionHistoryResponse: ...

    @classmethod
    def rollback_to_version(
        cls,
        workflow: WorkflowConfig,
        revision_index: int,
        user: User,
        change_notes: str | None = None,
    ) -> WorkflowConfig: ...

    @staticmethod
    def _collect_mcp_servers(workflow: WorkflowConfig) -> list[MCPServerDetails]: ...

    @classmethod
    def _validate_restored_workflow(cls, workflow: WorkflowConfig, user: User) -> None: ...

    @staticmethod
    def _run_async[T](coro: Coroutine[Any, Any, T]) -> T: ...
```

`get_version_history` returns `yaml_config_history` as-is (empty list when `None`); the live `yaml_config` is never included.

`rollback_to_version` steps, in order:

1. **Pre-checks (400, nothing mutated):**
   - history empty → `"No revision history available for this workflow."`
   - `revision_index < 0` or `>= len(history)` → `"Invalid revision index."`, with `Valid range: 0 to N-1. Received: …` in `details`
   - target revision's `yaml_config` empty or whitespace-only → `"Revision has empty yaml_config."`
2. **Build the audit entry:** `YamlConfigHistory(yaml_config=<current yaml_config or "">, date=now, created_by=user.as_user_model(), change_notes=change_notes)`.
3. **Restore and reparse:** set `workflow.yaml_config = target.yaml_config`, clear `workflow.states` (so a snapshot that omits `states` cannot keep the live graph), call `workflow.parse_execution_config()`, set `workflow.updated_by = user.as_user_model()`.
4. **Validate like the save path** (`_validate_restored_workflow`):
   - `MCPAccessControlService.validate_on_save` over every MCP server in the restored assistants and tools (`_collect_mcp_servers`)
   - strip inline MCP config from the restored assistants (`strip_inline_config`) and tools (`_strip_one`)
   - `WorkflowMarketplaceService().validate(workflow, user)` **only when `workflow.is_global`**, run from sync code through `_run_async`, which uses a worker thread when an event loop is already running
   - `WorkflowExecutor.validate_workflow(workflow_config=workflow, user=user)`
5. **On failure in steps 3–4:** restore `workflow.yaml_config` to its previous value and do not persist. `ExtendedHTTPException`, `ValidationException` and `NotFoundException` propagate unchanged; any other exception becomes 400 `"Workflow Configuration error"` with `details` = the exception's dict payload when `args[0]` is a dict, otherwise `str(exc)`.
6. **Persist atomically in one call:** `workflow.update(yaml_config_history=[new_entry, *history])`. `update()` writes the whole row, so the restored `yaml_config` and the new history are saved together.
7. Return the updated workflow.

There is **no workflow-`mode` check** in rollback. `parse_execution_config` never reads `mode` from YAML, so rollback cannot switch a workflow into autonomous mode.

### 3. Router additions — `src/codemie/rest_api/routers/workflow.py`

The two endpoints are appended at the end of the file under a `# Workflow Versioning Endpoints` section header. `WorkflowVersionService`, `WORKFLOW_CONFIGURATION_ERROR`, `WorkflowRollbackRequest` and `WorkflowVersionHistoryResponse` are imported at module top. The service is called via classmethods; there is no module-level singleton.

Other router changes:
- `WORKFLOW_CONFIGURATION_ERROR` is no longer defined in the router; it is imported from the service module.
- `_collect_workflow_mcp_servers` now delegates to `WorkflowVersionService._collect_mcp_servers`, so the save path and rollback collect MCP servers the same way.

Both endpoints load the row through `_load_workflow_record(workflow_id)`, which calls `WorkflowConfig.get_by_id` **without parsing the live YAML**. Revisions can therefore be listed, and a rollback run, even when the current `yaml_config` is corrupt. Only `KeyError` maps to 404 via `raise_not_found(resource_id=workflow_id, resource_type="Workflow")`; any other exception propagates.

#### `GET /v1/workflows/{workflow_id}/versions`

- Tags: `["Workflow Versioning"]`
- Auth: `Depends(authenticate)`, then `Ability(user).can(Action.READ, workflow)`, else `raise_access_denied("view")`
- Response model: `WorkflowVersionHistoryResponse`
- No pagination (MVP)

#### `POST /v1/workflows/{workflow_id}/versions/rollback`

- Tags: `["Workflow Versioning"]`
- Auth: `Depends(authenticate)`, then `Ability(user).can(Action.WRITE, workflow)`, else `raise_access_denied("update")`
- Body: `WorkflowRollbackRequest`
- Response: `BaseResponse(message="Workflow {workflow_id} rolled back to revision {revision_index}.")`
- After a successful rollback, schedules `update_workflow_schema(rolled_back, user)` as a `BackgroundTasks` job through `run_in_thread_pool`, the same way the save path refreshes the schema.

---

## Authorization

Denied access uses `raise_access_denied`, which raises **HTTP 401** (`Unauthorized`). This matches assistant rollback (`_check_user_can_access_assistant` → `raise_access_denied`) and the existing workflow GET/PUT/DELETE handlers. Do not use `raise_forbidden` (403) on these endpoints.

| Operation | Required Action | Error |
|---|---|---|
| Any call without authentication | — | 401 from `authenticate` |
| List revisions | `Action.READ` | 401 via `raise_access_denied("view")` |
| Rollback | `Action.WRITE` | 401 via `raise_access_denied("update")` |

---

## Error Cases

| Condition | HTTP Status | Message |
|---|---|---|
| Not authenticated | 401 | from `authenticate` |
| Workflow not found (`KeyError` from `get_by_id`) | 404 | `raise_not_found(resource_id=..., resource_type="Workflow")` |
| No READ permission | 401 | `raise_access_denied("view")` |
| No WRITE permission | 401 | `raise_access_denied("update")` |
| History is empty (no prior revisions) | 400 | `"No revision history available for this workflow."` |
| `revision_index` out of range (negative or `>= N`) | 400 | `"Invalid revision index."`; valid range in `details` (`Valid range: 0 to N-1. Received: …`) |
| Target revision has empty/whitespace `yaml_config` | 400 | `"Revision has empty yaml_config."` |
| Restored YAML fails to parse or fails workflow validation | 400 | `"Workflow Configuration error"`; parser/validator payload in `details` |
| Restored config fails MCP access control or marketplace validation (`ValidationException`), or raises `NotFoundException` / `ExtendedHTTPException` | as raised | propagated unchanged |
| `revision_index` missing, or `change_notes` longer than 1000 characters | 422 | Pydantic validation on `WorkflowRollbackRequest` |

There is no `revision_date` request field and no 404 for an unknown revision identifier. In every 400 case `update` is not called and nothing is persisted.

Workflow `mode` is not an error condition: rolling back a workflow stored as `AUTONOMOUS` succeeds like any other (no 403). This mirrors assistant rollback, which checks only access (401) and version range (400), never the entity's state.

---

## Rollback Behavior

- Rollback is synchronous: `workflow.update(...)` returns before the API responds. The schema refresh runs afterwards as a background task.
- Rollback is auditable: the current config is prepended to `yaml_config_history` as a new entry, with `created_by` set to the authenticated user and the request's `change_notes`. The history grows by one entry on every rollback, the same as on every save.
- The restored config goes through the same checks as a save: MCP access control and inline-config stripping, marketplace validation for global workflows, and `WorkflowExecutor.validate_workflow`.
- `yaml_config` and `yaml_config_history` are written in a single `update()` call; a validation failure persists neither.
- Rollback does not change `mode`: `parse_execution_config` never reads it from the restored YAML, so rollback cannot switch a workflow into autonomous mode. The `PUT` save-path guard (403 when the *proposed* mode is `AUTONOMOUS`) therefore has nothing to check here and is intentionally not applied. Rows already stored as `AUTONOMOUS` can be rolled back; running them stays blocked by the 403 guard in `workflow_executions.py`.

---

## Revision Identifier Design

The positional index into `yaml_config_history` is the revision identifier for this story. Index `0` is the most recent prior revision; index `N-1` is the oldest. This is intentionally temporary: a future story will introduce a stable identifier (e.g. a UUID or sequential revision number) to avoid the stale-index problem when history grows between a list call and a rollback call.

The current active `yaml_config` is **not** included in the list response. It is the live config, not a prior revision.

---

## Known Limitations (deferred)

Both are shared with the existing workflow save path and are out of scope for this story:
- `yaml_config_history` grows without bound (one entry per save or rollback).
- History is updated with an unlocked read-modify-write, so two concurrent writes can drop an entry.

---

## File Changes

| File | Change |
|---|---|
| `src/codemie/rest_api/models/workflow_version.py` | **New**: `CHANGE_NOTES_MAX_LENGTH`, `WorkflowVersionHistoryResponse`, `WorkflowRollbackRequest` |
| `src/codemie/service/workflow/__init__.py` | **New**: license header only |
| `src/codemie/service/workflow/workflow_version_service.py` | **New**: `WorkflowVersionService`, `WORKFLOW_CONFIGURATION_ERROR` |
| `src/codemie/core/workflow_models/workflow_config.py` | **Modified**: `YamlConfigHistory.change_notes: str \| None = None` |
| `src/codemie/rest_api/routers/workflow.py` | **Modified**: `_load_workflow_record` and two new endpoints; `WORKFLOW_CONFIGURATION_ERROR` now imported; `_collect_workflow_mcp_servers` delegates to the service |
| `tests/codemie/service/test_workflow_version_service.py` | **New**: service unit tests |
| `tests/codemie/rest_api/routers/test_workflow_versions.py` | **New**: router, request-model and authentication tests |

---

## Out of Scope

- SDK (`listVersions` / `rollbackToVersion` methods): separate repo, separate ticket.
- Rolling back an assistant to a version that references a specific workflow revision.
- Visual/graphical diff of workflow differences (covered by a separate story).
- UI rollback (covered by EPMCDME-8827).
- Pagination on the versions list endpoint (MVP without pagination).
