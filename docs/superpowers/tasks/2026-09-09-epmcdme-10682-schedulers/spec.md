# Spec — EPMCDME-10682: Schedulers REST API

## Overview

Expose a Schedulers REST API in three phases: scheduler list + toggle (Phase 1), run history list and stats (Phase 2), run detail and paginated logs (Phase 3). Introduces a greenfield `scheduler_runs` table and extends the APScheduler cron binding to persist run outcomes.

---

## Data Model

### `SchedulerRun` SQLModel

Table: `codemie.scheduler_runs`

| Column | Type | Constraints |
|---|---|---|
| `id` | `str` UUID | PK, default `uuid4` |
| `scheduler_id` | `str` | FK → `settings.id` ON DELETE CASCADE, indexed |
| `status` | `str` | `running` \| `completed` \| `failed` \| `cancelled` |
| `trigger` | `str` | `scheduled` |
| `started_at` | `datetime` | not null, indexed |
| `finished_at` | `datetime` | nullable |
| `duration_ms` | `int` | nullable |
| `execution_id` | `str` | nullable — assistant conversation ID or workflow exec ID |
| `conversation_id` | `str` | nullable — assistant runs only |
| `resource_execution_id` | `str` | nullable — workflow runs only |
| `input_data` | `dict` JSONB | nullable — `{initialPrompt}` |
| `result_data` | `dict` JSONB | nullable — `{available: bool, content}` |
| `error_data` | `dict` JSONB | nullable — `{code, message, details, timestamp}` |
| `logs` | `list[dict]` JSONB | nullable — inline log entries |
| `metrics` | `dict` JSONB | nullable — `{inputTokens, outputTokens, cost}` |

One Alembic migration off HEAD revision `fa14587c0de1`. `src/external/alembic/env.py` receives an import of `SchedulerRun` before `target_metadata`.

Scheduler configuration continues to live in the existing `settings` table (`credential_type = SCHEDULER`). No new `schedulers` table is introduced.

---

## API Endpoints

### Phase 1 — Schedulers

#### `GET /v1/schedulers/filter-options`

Auth: none (open endpoint). Returns `SchedulerFilterOptions`.

Fetches all `settings WHERE credential_type = SCHEDULER` via Elasticsearch, then batch-resolves canonical resource names from their source models (`IndexInfo`, `Assistant`, `WorkflowConfig`). Deduplicates by `resource_id` / `project_name` and returns both lists sorted case-insensitively by name.

```json
{
  "resources": [{"id": "...", "name": "...", "type": "..."}],
  "projects": [{"id": "...", "name": "..."}]
}
```

#### `GET /v1/schedulers`

Auth: `Depends(authenticate)`. Returns `PaginatedListResponse[SchedulerListItem]`.

Query parameters: `page` (0-based), `pageSize`, `search` (name or resource name), `resourceType` (`Assistant` | `Workflow` | `Datasource`), `projectId`, `resourceId`, `status` (`enabled` | `disabled`), `lastRunStatus` (`completed` | `failed` | `running` | `never`).

Query strategy:
1. Filter `settings WHERE credential_type = 'Scheduler'` using JSONB extraction for `is_enabled` (`status`), resource fields, and text search.
2. For each page result, attach `lastRun` via a `DISTINCT ON (scheduler_id)` subquery on `scheduler_runs ORDER BY started_at DESC`. The `lastRunStatus` filter is applied as a WHERE on this subquery result; `never` maps to rows with no matching run record.
3. Resource name enrichment is batched: collect all `(resource_type, resource_id)` pairs from the page, run three IN-queries (`assistants`, `workflows`, `index_info`), assemble a lookup dict, and annotate each item. No N+1 queries.

Response item shape matches the FE backend contract (`id`, `name`, `resource`, `project`, `schedule`, `isEnabled`, `lastRun`). `schedule.nextRunAt` is read from the JSONB `credential_values`. When `lastRun` is `null` the field is omitted or set to `null`.

#### `PATCH /v1/schedulers/{id}`

Auth: `Depends(authenticate)`. Request body: `{"isEnabled": bool}`.

Calls `SchedulerSettingsService.patch_is_enabled(id, is_enabled)`:
- Fetches the `settings` record; raises `raise_not_found` if absent.
- Mutates `credential_values[0].is_enabled`.
- Calls `flag_modified(obj, "credential_values")` before `.update()`.

Returns `200` with the updated `SchedulerListItem`.

---

### Phase 2 — Run History

#### `GET /v1/scheduler-runs`

Auth: `Depends(authenticate)`. Returns `PaginatedListResponse[SchedulerRunListItem]`.

Query parameters: `page`, `pageSize`, `search` (execution ID, scheduler name, resource ID/name), `status` (comma-separated, e.g. `completed,failed`), `project`, `resourceType`, `resourceId`, `schedulerId` (required when loading the run history for a specific scheduler), `dateFrom` / `dateTo` (ISO-8601, inclusive `started_at` range), `sortBy` (`startedAt`), `sortDirection` (`asc` | `desc`).

`SchedulerRunRepository` builds a single parameterised SQL query with optional WHERE clauses. Multiple `status` values are passed as a list and translated to `IN (...)` SQL. All filters are applied server-side; no client-side filtering.

#### `GET /v1/scheduler-runs/stats`

Auth: `Depends(authenticate)`. Accepts the same filter parameters as the list endpoint excluding `page`, `pageSize`, `sortBy`, `sortDirection`.

Returns:
```json
{
  "total": int,
  "completed": int,
  "failed": int,
  "running": int,
  "cancelled": int,
  "successRate": float,
  "averageDurationMs": float
}
```

`successRate = completed / (completed + failed) * 100`. Running and cancelled are excluded from the denominator. `averageDurationMs` excludes rows with `null` `duration_ms`. Both are computed by the repository in a single aggregation query over the filtered result set — not from the current page.

> **Resolved open question (FE contract):** Stats are pre-aggregated by the backend via this endpoint. The frontend does not need to fetch the full unfiltered run list and compute locally.

---

### Phase 3 — Run Details

#### `GET /v1/scheduler-runs/{runId}`

Auth: `Depends(authenticate)`. Returns `SchedulerRunDetail`.

Fetches the `scheduler_runs` row by ID. `schedulerConfig` is assembled at read time from the parent `settings` JSONB (`cron`, `humanReadableSchedule`, `timezone`). `result.available` is `true` when `result_data` is non-null.

Metrics resolution (in priority order):
1. If `metrics` column is set on the run record, use it directly.
2. Else if `conversation_id` is present, query `ConversationMetrics.get_by_conversation_id`.
3. Else if `resource_execution_id` is present, query `WorkflowExecution.get_by_execution_id`.

Additional flat fields on the response for FE convenience: `workflowExecutionId` (mirrors `resourceExecutionId`), `inputTokens`, `outputTokens`, `executionCost`, `error` (mirrors `error_data`).

`error_data` is also appended to `logs` as an `ERROR`-level entry when present.

Result section link resolution by resource type:

| `resource.type` | Link target |
|---|---|
| `Assistant` | `/chats/<conversationId>` |
| `Workflow` | `/workflows/<resource.id>/workflow-executions/<resourceExecutionId>` |
| `Datasource` | `/data-sources/<resource.id>` |

If `result.available === false`, the link is omitted.

#### `DELETE /v1/scheduler-runs/{run_id}`

Auth: none (open endpoint). Returns `204 No Content`.

Deletes the run record. Returns `404` if the run does not exist. Returns `409 Conflict` if the run has `status = "running"` (active run cannot be deleted).

#### `GET /v1/scheduler-runs/{runId}/logs`

Auth: `Depends(authenticate)`. Query parameters: `page`, `pageSize`.

Returns a paginated slice of the inline `logs` JSONB array. Implemented as an array slice on the existing row (no separate table). Switch to a dedicated table only when the backend indicates the inline payload is too large.

---

## APScheduler Integration (`cron.py`)

Two persistence strategies, one per actor type:

### Async actors (assistant, workflow) — `_tracked_run`

Wraps the actor coroutine: INSERT a run record before calling the actor, UPDATE it on success or exception. The actor's return value carries `conversation_id`, `resource_execution_id`, and/or `metrics` which are persisted on completion. Workflow actors additionally populate `metrics` from the `tokens_usage` field in the response body.

### Sync actors (datasource) — `_tracked_run_sync`

Mirror of `_tracked_run` for datasource reindex actors that run on the thread-pool executor. After the actor returns, metrics are read from `IndexInfo.tokens_usage` via `_extract_datasource_metrics` — `DatasourceMonitoringCallback` overwrites this field after each run, so reloading immediately gives run-scoped figures.

All datasource scheduler jobs (`reindex_svn`, `reindex_code`, `reindex_jira`, `reindex_confluence`, `reindex_google`, `reindex_azure_devops_wiki`, `reindex_azure_devops_work_item`, `reindex_xwiki`, `reindex_xray`, `reindex_sharepoint`) are wrapped with `_tracked_run_sync`. `scheduler_id` and `resource_id` are passed as kwargs.

---

## Layer Map

### New files

| File | Purpose |
|---|---|
| `src/codemie/rest_api/models/scheduler_run.py` | `SchedulerRun` SQLModel + Pydantic response/request models (incl. `SchedulerFilterOptions`) |
| `src/codemie/repository/scheduler_run_repository.py` | All DB access for runs (incl. `delete_run`) |
| `src/codemie/service/scheduler_run_service.py` | Business logic for runs (incl. `delete_run`, `get_run_detail` with metric enrichment) |
| `src/codemie/rest_api/routers/schedulers.py` | All 8 endpoints |
| `src/external/alembic/versions/<hash>_add_scheduler_runs_table.py` | Migration |
| `tests/codemie/rest_api/routers/test_schedulers.py` | Router-level and unit tests |

### Modified files

| File | Change |
|---|---|
| `src/codemie/service/settings/scheduler_settings_service.py` | Add `list_schedulers`, `patch_is_enabled`, `get_filter_options`, `_fetch_all_scheduler_settings`, `_build_resource_name_map` |
| `src/codemie/triggers/bindings/cron.py` | Add `_tracked_run_sync`, `_extract_datasource_metrics`; wire all datasource jobs |
| `src/codemie/triggers/actors/assistant.py` | Return `{"conversation_id": ...}` on success |
| `src/codemie/triggers/actors/workflow.py` | Return `{"resource_execution_id": ..., "metrics": ...}` on success |
| `src/codemie/rest_api/routers/utils.py` | Add `raise_conflict` helper |
| `src/codemie/rest_api/main.py` | `app.include_router(schedulers.router)` |
| `src/external/alembic/env.py` | Import `SchedulerRun` before `target_metadata` |

---

## Error Handling

- Scheduler not found: `raise_not_found(id, "Scheduler")`
- Run not found: `raise_not_found(run_id, "SchedulerRun")`
- Access denied: `raise_access_denied(action)`
- All exceptions follow `ExtendedHTTPException` conventions.

---

## Testing

All 8 endpoints and supporting logic covered in `tests/codemie/rest_api/routers/test_schedulers.py` and `tests/codemie/triggers/bindings/test_cron.py`:

**Router tests:**
- `GET /v1/schedulers/filter-options` — returns deduped, sorted resources and projects
- `GET /v1/schedulers` — list returns paginated items; filters narrow results correctly
- `PATCH /v1/schedulers/{id}` — toggle updates `isEnabled`; 404 on unknown ID
- `GET /v1/scheduler-runs` — list returns paginated items; comma-separated status split
- `GET /v1/scheduler-runs/stats` — aggregates match the filtered run set; multi-status IN clause
- `GET /v1/scheduler-runs/{runId}` — returns full detail; 404 on unknown ID
- `DELETE /v1/scheduler-runs/{runId}` — 204 on success; 404 on missing; 409 on running status
- `GET /v1/scheduler-runs/{runId}/logs` — returns paginated log slice

**Unit tests:**
- `SchedulerRunRepository.list_runs` — multi-status IN clause; single-status IN clause
- `SchedulerSettingsService.get_filter_options` — deduplication and case-insensitive sorting
- `_tracked_run_sync` — creates run, updates on success, updates on exception (cron tests)

Auth mocked via `app.dependency_overrides[authenticate]`. Services mocked via `unittest.mock.patch` at the router's import path. Framework: pytest-asyncio + httpx `ASGITransport`.

---

## Out of Scope

- Manual run trigger (Phase 1–3 are read + toggle + delete only)
- Dedicated `logs` table (use inline JSONB until backend signals otherwise)
- New environment variables
- Scheduler creation/deletion (handled by existing settings endpoints)
