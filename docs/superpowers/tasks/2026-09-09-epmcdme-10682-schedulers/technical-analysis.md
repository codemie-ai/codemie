# Technical Research

**Task**: scheduler runs API response model tokens cost workflow execution
**Generated**: 2026-09-09T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

The /v1/scheduler-runs/{id} GET endpoint is missing these fields in its response: input_tokens, output_tokens, execution_cost, workflow_execution_id. We need to find where the scheduler-run response model is defined, what data is stored in the database, and how the response is built so we can add these missing fields.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/rest_api/models/scheduler_run.py` — All response Pydantic models and the `SchedulerRun` SQLModel (DB table). Key classes:
  - `SchedulerRun` (table=True, `__tablename__ = "scheduler_runs"`, schema `codemie`): DB columns: `id`, `scheduler_id`, `status`, `trigger`, `started_at`, `finished_at`, `duration_ms`, `execution_id`, `conversation_id`, `resource_execution_id`, `input_data` (JSONB), `result_data` (JSONB), `error_data` (JSONB), `logs` (JSONB), `metrics` (JSONB). **No `workflow_execution_id` column exists in the DB model.**
  - `RunMetrics` (Pydantic): fields `inputTokens: Optional[int]`, `outputTokens: Optional[int]`, `cost: Optional[float]`. Field name is `cost`, not `executionCost`.
  - `SchedulerRunDetail` (Pydantic): the response shape for `GET /v1/scheduler-runs/{id}`. Contains `metrics: Optional[RunMetrics]` as a nested sub-object. Does NOT expose `inputTokens`, `outputTokens`, or `cost` at the top level. Does NOT have a `workflowExecutionId` field. Has `resourceExecutionId: Optional[str]` (mapped from DB `resource_execution_id`).

- `src/codemie/service/scheduler_run_service.py` — `SchedulerRunService.get_run_detail()` builds the `SchedulerRunDetail` response:
  - Populates `metrics` from `run.metrics` JSONB (keys: `"inputTokens"`, `"outputTokens"`, `"cost"`) or falls back to `ConversationMetrics` via `run.conversation_id`.
  - Maps `run.resource_execution_id` → `resourceExecutionId`.
  - Maps `run.execution_id` → `executionId`.
  - No reference to `workflow_execution_id` anywhere in the service or model.

- `src/codemie/rest_api/routers/schedulers.py` — Router for all scheduler endpoints. `GET /scheduler-runs/{run_id}` (line 219–221) delegates entirely to `SchedulerRunService().get_run_detail(run_id)`. **No `response_model` is declared on this endpoint**, unlike other endpoints in the same router.

- `src/codemie/repository/scheduler_run_repository.py` — Data access layer for `SchedulerRun`; `get_by_id` returns a `SchedulerRun` ORM object directly.

- `src/external/alembic/versions/x1y2z3a4b5c6_add_scheduler_runs_table.py` — Migration that created the `scheduler_runs` table. Would need a new follow-on migration to add `workflow_execution_id` column.

### Architecture and Layers Affected

| Layer | File | Change needed |
|---|---|---|
| Response model | `src/codemie/rest_api/models/scheduler_run.py` | Add fields to `SchedulerRunDetail`; rename `cost` → `executionCost` in `RunMetrics` OR add alias |
| Service | `src/codemie/service/scheduler_run_service.py` | Populate new top-level fields in `get_run_detail()` |
| DB model | `src/codemie/rest_api/models/scheduler_run.py` | Add `workflow_execution_id: Optional[str]` to `SchedulerRun` if a new DB column is required |
| Migration | `src/external/alembic/versions/` | New migration to add the column (only if DB column required) |
| Router | `src/codemie/rest_api/routers/schedulers.py` | Add `response_model=SchedulerRunDetail` to the `GET /scheduler-runs/{run_id}` endpoint |

### Integration Points

- `ConversationMetrics` model (imported from `codemie.rest_api.models.conversation`) — fallback source for `total_input_tokens`, `total_output_tokens`, `total_money_spent` when `run.metrics` JSONB is absent. Any promotion of tokens/cost to top-level fields must keep both code paths consistent.
- `SchedulerSettingsService` — resolves scheduler/resource metadata; not involved in the missing fields.
- Alembic migration chain — required only if `workflow_execution_id` is a new column. Current HEAD revision inferred from existing migration file naming.

### Patterns and Conventions

- Response models are plain Pydantic `BaseModel` subclasses; DB models use `SQLModel(table=True)`.
- All response model fields use camelCase (FastAPI serialises from Pydantic field names directly).
- Optional fields default to `None`.
- JSONB columns on `SchedulerRun` store arbitrary dicts; access via `.get("key")`.
- Token/cost data is either in `run.metrics` JSONB dict or in `ConversationMetrics`; access pattern established: `run.metrics.get("inputTokens")`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/api/rest-api-patterns.md` — FastAPI router patterns including `response_model` usage.
- `.ai-run/guides/data/database-patterns.md` — SQLModel patterns; Alembic for all schema changes.
- `.ai-run/guides/data/repository-patterns.md` — Repository access patterns.

### Architectural Decisions

No ADRs specific to scheduler-run field naming. The pattern of storing token/cost metrics in a JSONB `metrics` column (rather than normalised columns) is established by the existing `SchedulerRun` model and service code.

### Derived Conventions

- New response fields must be camelCase: `workflowExecutionId`, `inputTokens`, `outputTokens`, `executionCost`.
- If `cost` in `RunMetrics` is renamed to `executionCost`, that is a breaking change for any consumer already reading `metrics.cost`. A Pydantic `Field(alias="cost")` or a serialisation alias may be safer.
- New optional DB columns follow `Optional[str] = Field(default=None)` pattern with no index unless queried.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/rest_api/routers/test_schedulers.py` — Tests for repository, service stats, and run-not-found behavior. A `_make_run()` helper creates minimal `SchedulerRun` instances. No test exercises `get_run_detail()` end-to-end.

### Testing Framework and Patterns

- pytest with `unittest.mock.patch` and `MagicMock`.
- Service-level tests patch `SchedulerRunRepository` entirely at import path.
- No fixture for `SchedulerRunDetail` shape assertions currently exists.

### Coverage Gaps

- No test asserts that `GET /v1/scheduler-runs/{id}` returns `inputTokens`, `outputTokens`, `executionCost`, or `workflowExecutionId`.
- No test verifies the metrics fallback path (from `ConversationMetrics`) includes these fields.
- No test for `workflow_execution_id` column presence or mapping after a potential migration.

---

## 5. Configuration and Environment

### Environment Variables

No environment variables specific to the missing fields. DB connection governed by `Settings.get_engine()`.

### Configuration Files

- `src/external/alembic/` — migration chain; a new migration file required if `workflow_execution_id` is a new DB column.
- `src/external/alembic/env.py` — imports `SchedulerRun`; no changes unless new model is introduced.

### Feature Flags and Deployment Concerns

None. Adding nullable columns to `scheduler_runs` is a backward-compatible migration. Renaming `cost` → `executionCost` in `RunMetrics` is a breaking API change that must be coordinated with frontend consumers.

---

## 6. Risk Indicators

- **`workflow_execution_id` has no DB column**: `SchedulerRun` has `resource_execution_id` and `execution_id` but no `workflow_execution_id`. Two possible resolutions: (1) `workflow_execution_id` is a new DB column requiring a migration; (2) it is an alias or derived value of `resource_execution_id` when `resource_type = "Workflow"`. This ambiguity must be clarified before implementation to avoid an unnecessary migration.
- **`cost` vs. `executionCost` naming conflict**: `RunMetrics.cost` is already in the live response. Renaming it is a breaking change. If the frontend contract requires `executionCost`, a Pydantic field alias should be used, or a new top-level field added alongside the existing `metrics.cost` to avoid breaking current consumers.
- **Tokens/cost already exist nested under `metrics`**: `inputTokens` and `outputTokens` are already in the response at `metrics.inputTokens` and `metrics.outputTokens`. The task may intend promotion to top-level fields on `SchedulerRunDetail`, or may only require a renaming/aliasing within `RunMetrics`. This needs clarification to avoid duplicating data in the response.
- **No `response_model` declared on `GET /scheduler-runs/{run_id}`**: FastAPI will not validate or document the response. Adding `response_model=SchedulerRunDetail` is a side fix that should accompany this change.
- **Two-path metrics resolution must stay consistent**: `get_run_detail()` populates metrics from either `run.metrics` JSONB or `ConversationMetrics`. Any new top-level fields must be populated in both paths.
- **No test coverage for new fields**: tests must be added asserting all four fields appear correctly in the detail response.

---

## 7. Summary for Complexity Assessment

The task touches two primary layers: the Pydantic response model (`SchedulerRunDetail` and `RunMetrics` in `models/scheduler_run.py`) and the service layer (`get_run_detail()` in `scheduler_run_service.py`). The router needs a minor `response_model` fix. If `workflow_execution_id` requires a new DB column, the DB model layer and an Alembic migration are also affected. Total file change surface: 2–4 source files plus optionally one migration file.

Three of the four requested fields (`inputTokens`, `outputTokens`, cost) already exist in the live response nested under `metrics`. The work for these is likely a promotion to top-level fields on `SchedulerRunDetail` and/or a rename of `cost` to `executionCost` in `RunMetrics`. Both are pattern-following changes with no technical novelty. The fourth field, `workflowExecutionId`, is the key unknown: it has no DB column and no existing mapping in the service, so a decision is needed on whether it maps to an existing field or requires a new column and migration.

Test coverage for `get_run_detail()` is absent. New tests must assert the four fields appear in the response under their correct camelCase names and that the two-path metrics resolution (JSONB vs. `ConversationMetrics` fallback) populates them correctly. Overall complexity is low: the pattern is fully established, the change surface is small, and the only design decision is the `workflowExecutionId` source — which is the single item that could escalate scope if a new migration is required.
