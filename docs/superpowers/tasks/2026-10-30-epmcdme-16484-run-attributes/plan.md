# Run Attributes (EPMCDME-16484) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Callers attach up to 4 immutable string attributes to one conversation or workflow execution; analytics filters and groups usage by them.

**Architecture:** One shared module validates attributes and builds record fields. Attributes are stored as nullable JSONB on `conversations` and `workflow_executions`, copied onto usage records via a log-only path, and read back through a new `attributes` analytics filter, a group-by endpoint and a per-execution workflow endpoint.

**Tech Stack:** FastAPI, SQLModel/PostgreSQL, Alembic, Pydantic, Elasticsearch DSL via `SecureQueryBuilder`.

**Spec:** `docs/superpowers/tasks/2026-10-30-epmcdme-16484-run-attributes/spec.md`.

Commit per task using the repository's existing convention. No explanatory code comments; copyright header on new source files. Tests: Tasks 1-7 were first implemented without tests under a misread repo rule; Task 12 added them afterwards. Every later task is test-first.

## Global Constraints

- Max keys: new `config.py` setting, default 4. Key regex `^[a-z0-9][a-z0-9._-]{0,62}[a-z0-9]$`; prefixes `codemie.`, `otel.` reserved. Value 1-128 UTF-16 code units, no control characters or unpaired surrogates.
- Invariant: max key (64) + 1 + max value (128) = 193 < 256 (ES `ignore_above`); assert it from the constants.
- Record fields `run_attribute_keys` (sorted keys), `run_attribute_pairs` (sorted `key=value`); log/ES only, never the OTel counter; runs without attributes produce byte-identical records.
- Analytics-side validation failures must raise `ExtendedHTTPException(code=422)`; `handle_analytics_errors` (`routers/analytics/_common.py:47`) turns any `ValueError` into 400 and must not change.
- Every new optional parameter defaults to `None`; existing callers unchanged.

## Review Focus

- Value of exactly 128 chars with a 64-char key accepted; 129 rejected.
- `attributes` filter given as non-JSON, a JSON array, or non-string values returns 422, not 400.
- `attribute_key` containing `.` or `-` must not match sibling keys sharing a prefix.
- Workflow metrics after `delete_on_completion` still carry attributes.

Each of these is covered by a test (see Task 12 and the spec's Tests section).

---

### Task 1: Shared validation module, config, request models

**Files:**
- Create: `src/codemie/core/run_attributes.py`
- Modify: `src/codemie/configs/config.py` (new `RUN_ATTRIBUTES_MAX_KEYS: int = 4`), `src/codemie/core/models.py:642` (`AssistantChatRequest`), `src/codemie/core/workflow_models/workflow_execution.py:321` (`CreateWorkflowExecutionRequest`)

**Interfaces:**
- Produces: `class RunAttributesError(Exception)`; `MAX_KEY_LEN=64`, `MAX_VALUE_LEN=128`, `KEY_PATTERN`; `validate_run_attributes(attrs: dict[str, str] | None) -> dict[str, str] | None`; `validate_attribute_key(key: str) -> str`; `parse_attributes_filter(raw: str | None) -> dict[str, str] | None` (JSON object string, same key/value rules); `build_record_fields(attrs: dict[str, str] | None) -> dict[str, list[str]]` (empty dict when none/empty); `PAIRS_FIELD = "attributes.run_attribute_pairs.keyword"`, `KEYS_FIELD = "attributes.run_attribute_keys.keyword"`.

**Tests (added in Task 12):** `tests/codemie/core/test_run_attributes.py`, `tests/codemie/core/test_models_assistant_chat_request.py`, `tests/codemie/core/workflow_models/test_workflow_execution_request.py`.

- [ ] **Step 1:** Implement the module. `RunAttributesError` must not subclass `ValueError`. Add a module-level assert that `MAX_KEY_LEN + 1 + MAX_VALUE_LEN < 256`. Max-keys read from `config`.
- [ ] **Step 2:** Add `attributes: Optional[dict[str, str]] = None` to both request models with a `field_validator` that calls `validate_run_attributes` and re-raises `RunAttributesError` as `ValueError` (precedent: `FileNamesCountValidatorMixin`, `core/models.py:592`).
- [ ] **Step 3:** Run existing tests for `tests/codemie/core/` and fix any broken by the change.

### Task 2: Persistence, migration, response DTOs

**Files:**
- Modify: `src/codemie/rest_api/models/conversation.py:248` (`Conversation`), `:946` (`ConversationResponse`); `src/codemie/core/workflow_models/workflow_execution.py:149` (`WorkflowExecution`), `:300` (`WorkflowExecutionResponse`)
- Create: `src/external/alembic/versions/<rev>_add_run_attributes.py`

**Interfaces:**
- Produces: `attributes: Optional[dict[str, str]]` on all four classes.

**Tests (added in Task 12):** `tests/codemie/rest_api/models/test_run_attributes_responses.py`. The migration test Task 12 added was removed later (see the spec's AC 8 row).

- [ ] **Step 1:** Add nullable JSONB `attributes` to both tables, modelled on `file_names` (`workflow_execution.py`, `SQLField(sa_column=Column(JSONB, nullable=True))`, no server default, no index). Add explicit `attributes` to both response DTOs and confirm the two read endpoints populate it (`GET /v1/conversations/{id}`, `GET /v1/workflows/{id}/executions/{execution_id}`).
- [ ] **Step 2:** Write the Alembic migration: resolve the single current head by scanning `down_revision` across `src/external/alembic/versions/` (several merge files exist); `upgrade` adds both columns, `downgrade` drops them.

### Task 3: Log-only metric fields

**Files:**
- Modify: `src/codemie/service/monitoring/base_monitoring_service.py:128-152`, `conversation_monitoring_service.py:49`, `workflow_monitoring_service.py:33`

**Interfaces:**
- Consumes: `build_record_fields` (Task 1).
- Produces: `send_count_metric(..., extra_log_fields: Optional[dict] = None)`; `send_conversation_metric(..., run_attributes: Optional[dict[str, str]] = None)`; `send_workflow_execution_metric(..., run_attributes: Optional[dict[str, str]] = None)`.

**Tests (added in Task 12):** `tests/codemie/service/monitoring/test_run_attribute_metrics.py`.

- [ ] **Step 1:** In `send_count_metric`, merge `extra_log_fields` only into the dict passed to `send_log_metric` (line 141), never into `attributes` that reaches `counter.add`.
- [ ] **Step 2:** In both monitoring methods, pass `build_record_fields(run_attributes)` as `extra_log_fields`. For the workflow one, thread it through `_send_per_run_metrics` and `_send_aggregated_metric` (L108), not through `additional_attributes` (which reaches OTel).

### Task 4: Chat flow

**Files:**
- Modify: `src/codemie/rest_api/routers/assistant/_ask.py` (pre-flight between L299 and L301), `src/codemie/service/conversation_service.py:158,369,413` (`UpsertChatHistoryParams`, `_handle_turn_completion`, `upsert_chat_history`), `src/codemie/rest_api/handlers/assistant_handlers.py` (carry resolved attributes into `UpsertChatHistoryParams`)

**Interfaces:**
- Consumes: `send_conversation_metric(..., run_attributes=)` (Task 3), `Conversation.attributes` (Task 2).
- Produces: `_validate_run_attributes_and_raise(request: AssistantChatRequest) -> None` in `_ask.py` that sets `request.attributes` to the effective value (superseded by Task 18: `validate_run_attributes_and_raise(request, user)` in `routers/utils.py`, which never copies stored attributes); `UpsertChatHistoryParams.attributes: Optional[dict[str, str]] = None`.

**Tests (added in Task 12):** `tests/codemie/rest_api/routers/test_assistant_run_attributes.py`, `tests/codemie/service/test_conversation_service_run_attributes.py`, `tests/codemie/rest_api/handlers/test_assistant_handlers.py`.

- [ ] **Step 1:** Add `_validate_run_attributes_and_raise` next to the other `_validate_*_and_raise` helpers and call it after `_validate_assistant_supports_model_change_and_raise` (L299), before the guardrail block and before the `try` at L323. Load the conversation by `request.conversation_id`: no row or no history keeps the request's attributes; with history, omitted/identical resolves to stored attributes; different raises `ExtendedHTTPException(code=422)`. Do not raise inside the `try` (L363-365 would write an error turn). Skip `_ask_virtual_assistant`. Task 18 changed the order and dropped the copy of stored attributes.
- [ ] **Step 2:** Add `attributes` to `UpsertChatHistoryParams`; set it on the `Conversation` only when creating a row or filling a history-less row. No conflict check in the service.
- [ ] **Step 3:** In `_handle_turn_completion`, pass the stored conversation row's `attributes` as `run_attributes` to `send_conversation_metric` so tool-resume and `in_progress` upserts are labelled.

### Task 5: Workflow flow

**Files:**
- Modify: `src/codemie/service/workflow_service.py:233,287`, `src/codemie/rest_api/routers/workflow_executions.py:238`, `src/codemie/workflows/nodes/sub_workflow_node.py:102`, `src/codemie/service/workflow_execution/workflow_execution_service.py:54` (and its 4 `send_workflow_execution_metric` call sites)

**Interfaces:**
- Consumes: `send_workflow_execution_metric(..., run_attributes=)` (Task 3).
- Produces: `WorkflowService.create_workflow_execution(..., attributes: Optional[dict[str, str]] = None)`.

**Tests (added in Task 12):** `tests/codemie/rest_api/routers/test_workflow_executions.py`, `tests/codemie/service/test_workflow_service.py`, `tests/codemie/workflows/nodes/test_sub_workflow_node.py`, `tests/codemie/service/workflow_execution/test_workflow_execution_service.py`.

- [ ] **Step 1:** Add the `attributes` parameter and store it on the `WorkflowExecution` built at L287. The router passes `request.attributes`.
- [ ] **Step 2:** `_create_child` passes the parent's attributes, read from the parent execution row. `_resume_child` and resume reuse the stored row unchanged.
- [ ] **Step 3:** `WorkflowExecutionService` caches `self._run_attributes` from the execution row at init and refresh (kept when `workflow_execution` becomes None after `delete_on_completion`) and passes it to every `send_workflow_execution_metric` call.

### Task 6: Analytics `attributes` filter

**Files:**
- Modify: `src/codemie/service/analytics/query_pipeline.py:43,522,544`, `src/codemie/service/analytics/query_builder.py:42`, `src/codemie/service/analytics/analytics_service.py`, `src/codemie/service/analytics/handlers/summary_handler.py`, `assistant_handler.py`, `workflow_handler.py`, `src/codemie/rest_api/routers/analytics/usage.py:45`

**Interfaces:**
- Consumes: `parse_attributes_filter`, `PAIRS_FIELD` (Task 1).
- Produces: `AnalyticsQueryFilters.attributes: Optional[dict[str, str]] = None`; `SecureQueryBuilder.add_attributes_filter(attributes: Optional[dict[str, str]]) -> SecureQueryBuilder` (one `term` filter on `PAIRS_FIELD` per pair, modelled on `add_client_source_filter`); a router helper `_parse_attributes_param(raw: str | None) -> dict | None` mapping `RunAttributesError` to `ExtendedHTTPException(code=422)`.

**Tests (added in Task 12):** `tests/codemie/service/analytics/test_run_attributes_filter.py`, `tests/codemie/rest_api/routers/analytics/test_usage_run_attributes.py`.

- [ ] **Step 1:** Add the field and builder method; call it in both `_build_query` and `_build_query_without_time_filter`.
- [ ] **Step 2:** Add an `attributes: Optional[str] = Query(None)` param to `/summaries` (removed in Task 15), `/assistants-chats`, `/workflows`; parse in the router via the helper and pass the dict through `AnalyticsService` (same way as `client_source`) into every `AnalyticsQueryFilters` built in the three handlers.
- [ ] **Step 3:** In `summary_handler.get_summaries`, apply to the unique-users query, the summary aggregation and `get_cli_costs_with_adjustment`. Reverted in Task 15.

### Task 7: Group-by endpoints

**Files:**
- Create: `src/codemie/service/analytics/handlers/attributes_handler.py`
- Modify: `src/codemie/service/analytics/analytics_service.py` (lazy `_attributes_handler`, two methods), `src/codemie/rest_api/routers/analytics/usage.py`

**Interfaces:**
- Consumes: `AnalyticsQueryFilters.attributes` (Task 6), `validate_attribute_key`, `PAIRS_FIELD`, `KEYS_FIELD` (Task 1).
- Produces: `GET /v1/analytics/attribute-keys` (columns `key, conversations, workflow_executions, last_used`; removed in Task 15); `GET /v1/analytics/attributes-usage?attribute_key=` (columns `value, conversations, workflow_executions, total_tokens, total_cost, unique_users, last_used`); both `TabularResponse` under `@handle_analytics_errors`.

**Tests (added in Task 12):** `tests/codemie/service/analytics/handlers/test_attributes_handler.py`, `tests/codemie/rest_api/routers/analytics/test_usage_run_attributes.py`.

- [ ] **Step 1:** Implement the handler on `AnalyticsQueryPipeline` / `AggregationBuilder.build_terms_agg` (nearest template: `workflow_handler.get_top_workflow_usage`). Filter metric types to `conversation_assistant_usage` and `workflow_execution_total` for the conversations/workflow_executions counts. `attribute-keys`: `terms` agg on `KEYS_FIELD`. `attributes-usage`: `term` filter on `KEYS_FIELD` and `terms` agg on `PAIRS_FIELD` with an `include` regex of `<escaped key>=.*`; escape every regex-special char (notably `.` and `-`); strip the `key=` prefix to produce `value`.
- [ ] **Step 2:** Router: validate `attribute_key` first; missing or invalid raises `ExtendedHTTPException(code=422)` explicitly (never a bare `ValueError`). `attributes-usage` also accepts the `attributes` filter param (Task 6 helper).

### Task 8: Documentation (removed 2026-11-02)

Feature docs are not kept in this repository; `docs/run-attributes.md` was deleted. Kept below for history.

**Files:**
- ~~Create: `docs/run-attributes.md`~~

**Test-first: no — documentation only.**

- [ ] **Step 1:** Write the "Run attributes" page covering: format and limits (value max 128 chars, with the 193 < 256 pair-length reason), immutability, inheritance for conversations with history and sub-workflows, visibility, no-secrets/personal-data warning (values appear in logs), uncovered cases (`save_history=false`, virtual assistants, webhook/scheduler/A2A, tool/CLI/datasource metrics, no backfill), and a comparison with chat `metadata`, `prompt_variables`/`user_input` and workflow `tags`. Include the API contract (request fields, read endpoints, `attributes` filter, two group-by endpoints, 422 errors).


### Task 9: Merge EPMCDME-16481 (MR !4690)

- [x] Merge `EPMCDME-16481_per-run-assistant-analytics` into this branch. Conflicts in `_common.py`, `query_builder.py` and `query_pipeline.py` are adjacent additions: keep both sides (`attributes` and `assistant_id` fields, both builder filters, both imports).

### Task 10: Attributes on per-request rows and in `filters_applied`

**Files:**
- Modify: `src/codemie/core/run_attributes.py` (`attributes_from_pairs`), `src/codemie/service/analytics/handlers/assistant_handler.py` (`_parse_assistants_requests`, `_get_assistants_requests_columns`), `src/codemie/service/analytics/query_pipeline.py` (`_build_filters_applied`), `src/codemie/rest_api/routers/analytics/usage.py` (endpoint description)
- Test: `tests/codemie/core/test_run_attributes.py`, `tests/codemie/service/analytics/handlers/test_assistant_handler.py`, `tests/codemie/service/analytics/test_query_pipeline.py`

**Test-first: yes.**

- [x] **Step 1:** Failing tests: pairs → `{key: value}` round-trip with `build_record_fields`, value containing `=`; `None`/empty/non-list/no separator → `None`; row `attributes` set from `run_attribute_pairs` and `null` without; `attributes` column `{"id": "attributes", "label": "Attributes", "type": "json"}` last in column order; `filters_applied["attributes"]` set and defaulting to `None`. Run → RED.
- [x] **Step 2:** Implement. Run analytics, repository and router tests → GREEN.

### Task 11: OpenAPI baseline

- [x] Update `tests/codemie/rest_api/router_guards/baselines/openapi.json` for the new request/response fields, the `attributes` query parameter and the two group-by endpoints. A local regeneration without `codemie-enterprise` drops the LLM proxy routes, so apply only the paths and schemas that differ between local dumps of `origin/main` and this branch.


### Task 12: Tests for Tasks 1-7

- [x] **Step 1:** Write unit and seam tests for every behaviour of Tasks 1-7, in each area's existing test directory and style; the spec's Tests section maps them to the acceptance criteria.
- [x] **Step 2:** Check each test by mutation: break the behaviour it covers in the source, run the test, expect a failure, restore. 22 of 22 mutations were caught.
- [x] **Step 3:** Fix the defects the tests found (below) and run the affected suites → GREEN.

Files:

- `tests/codemie/core/test_run_attributes.py`: key format and reserved prefixes, value length in UTF-16 units and control characters, max keys from config, the 193-char boundary, filter parsing, record fields.
- Request models: `test_models_assistant_chat_request.py`, `workflow_models/test_workflow_execution_request.py` (422 on invalid attributes).
- Chat: `routers/test_assistant_run_attributes.py` (inherit, identical, change → 422 before any handler, summary or error write); `service/test_conversation_service_run_attributes.py` (new and pre-created empty conversations store attributes, conversations with history keep them, the usage metric uses the stored ones); `handlers/test_assistant_handlers.py` (request attributes forwarded).
- Metrics: `monitoring/test_run_attribute_metrics.py` (keys and pairs in the log record, never in the OTel counter; records without attributes unchanged; aggregated and per-LLM-run workflow records).
- Workflows: router (forwarded, 422), `test_workflow_service.py` (stored), `test_sub_workflow_node.py` (inherited), `test_workflow_execution_service.py` (metric labelled, cached after row deletion).
- Analytics: `test_run_attributes_filter.py` (term per pair; ES|QL filter of `assistants-chats`, `workflows`, all three `summaries` queries, DAU and MAU; the `summaries` tests were removed in Task 15), `handlers/test_attributes_handler.py`, `routers/analytics/test_usage_run_attributes.py` (422, not 400; forwarding).
- Migration: `migrations/test_aad1456a4f17_add_run_attributes.py` (columns, downgrade, single head). Removed 2026-11-02: migration tests are not a project standard (see the spec's AC 8 row).
- Response models: `rest_api/models/test_run_attributes_responses.py`.

Found and fixed while writing them:
- `AttributesHandler` passed `MetricName.to_list(MetricName.USAGE_METRICS)`, so `attribute-keys` and `attributes-usage` failed on every call. Now uses `to_list_from_group`.
- After merging `main`, the migration was a second Alembic head next to `633929ee02fa`. It now revises `633929ee02fa`.

### Task 13: OpenAPI descriptions (AC 9)

**Tests:** the OpenAPI baseline (`tests/codemie/rest_api/router_guards/baselines/openapi.json`) pins the descriptions.


- [x] `RUN_ATTRIBUTES_REQUEST_DESC` / `RUN_ATTRIBUTES_RESPONSE_DESC` in `core/run_attributes.py` on the request, response and table fields; extended `ATTRIBUTES_FILTER_DESC`; summary and description on `attribute-keys` (removed in Task 15) and `attributes-usage`. Baseline updated with the same main-vs-branch delta as Task 11.


### Task 14: `attributes` filter on `assistants-requests`

**Files:**
- Modify: `src/codemie/rest_api/routers/analytics/usage.py` (`get_assistants_requests` parameter and description), `src/codemie/service/analytics/analytics_service.py`, `src/codemie/service/analytics/handlers/assistant_handler.py` (pass `attributes` into `AnalyticsQueryFilters`)
- Test: `tests/codemie/service/analytics/handlers/test_assistant_handler.py`, `tests/codemie/service/analytics/test_analytics_service.py`, `tests/codemie/rest_api/routers/test_analytics.py`, `tests/codemie/rest_api/routers/analytics/test_usage_run_attributes.py`

**Test-first: yes.**

- [x] **Step 1:** Failing tests: the handler query carries one pair term per attribute; the service and route forward `attributes`; an invalid filter returns 422 without calling the service. Run → RED (8 failures).
- [x] **Step 2:** Implement; run analytics service, handler and router tests → GREEN. Mutation check: dropping `attributes` at the route, service or handler fails a test (3 of 3).
- [x] **Step 3:** Update the OpenAPI baseline with the same main-vs-branch delta as Task 11.

### Task 15: Remove `attribute-keys` and the `/summaries` filter (2026-11-02)

Both were beyond the ticket and redundant: `attributes-usage` already groups by a key, and `/summaries` mixes usage types that carry no attributes.

**Files:**
- Modify: `src/codemie/rest_api/routers/analytics/usage.py` (drop the `/attribute-keys` route and description, drop `attributes` from `/summaries`), `src/codemie/rest_api/routers/analytics/_common.py` (`ATTRIBUTES_FILTER_DESC` no longer mentions other usage types), `src/codemie/service/analytics/analytics_service.py`, `src/codemie/service/analytics/handlers/attributes_handler.py`
- Revert to their pre-feature state: `summary_handler.py`, `cli_cost_processor.py`, `engagement_handler.py`
- Test: `test_attributes_handler.py` (`attribute-keys` test dropped; its metric-type assertion moved to an `attributes-usage` test), `test_run_attributes_filter.py` and `test_usage_run_attributes.py` (`summaries` cases dropped), `test_analytics.py` and `test_analytics_service.py` (`summaries` assertions back to their pre-feature form)

- [x] **Step 1:** Remove the code and the tests above; `/v1/analytics/summaries` in the OpenAPI baseline is identical to `main` again.
- [x] **Step 2:** Update the OpenAPI baseline: drop `/v1/analytics/attribute-keys`, refresh the five analytics paths whose `attributes` parameter or description changed.

### Task 16: Per-execution workflow analytics `workflows-executions` (2026-11-02)

The ticket asks analytics to show the attributes next to each workflow run. `/workflows` groups by workflow name, and the workflow execution read endpoints are not analytics (one workflow at a time, PostgreSQL, no date or `attributes` filter, workflow access rules, executions removed by `delete_on_completion` missing).

**Files:**
- Modify: `src/codemie/service/analytics/handlers/workflow_handler.py` (`get_workflows_executions`, its aggregation, parser and columns), `src/codemie/service/analytics/analytics_service.py` (pass-through), `src/codemie/rest_api/routers/analytics/usage.py` (`GET /workflows-executions` and `WORKFLOWS_EXECUTIONS_DESCRIPTION`)
- Create: `tests/codemie/service/analytics/handlers/test_workflow_handler.py`
- Test: `tests/codemie/service/analytics/test_run_attributes_filter.py`, `tests/codemie/service/analytics/test_analytics_service.py`, `tests/codemie/rest_api/routers/analytics/test_usage_run_attributes.py`

**Test-first: yes.**

- [x] **Step 1:** Failing tests: the aggregation groups `workflow_execution_total` documents by `attributes.execution_id.keyword`, ordered by the newest timestamp; run fields come from the newest document (`top_hits`, size 1); the `attributes` filter reaches the query; totals are global sums in the same query; each row sums usage and maps the pairs to `attributes`; the service and route forward every filter; an invalid filter returns 422. Run → RED (13 failures).
- [x] **Step 2:** Implement on `execute_tabular_query` with `totals_aggs` (one Elasticsearch query per call); run → GREEN. Mutation check: dropping the `attributes` filter, reading the oldest document, dropping the `attributes` column, or ordering by document count each fails a test (4 of 4).
- [x] **Step 3:** Update the OpenAPI baseline: add `/v1/analytics/workflows-executions` only (the local regeneration differs from the committed baseline by that path and by the LLM-proxy routes, which are not mounted locally).

### Task 17: Complete per-execution aggregation across shards (2026-11-02)

Supersedes Task 16's single-query implementation. A newest-first `terms` aggregation selects the correct execution IDs but may omit older documents on other shards, so its per-bucket sums cannot satisfy AC12.

**Files:** `service/analytics/handlers/workflow_handler.py`, `service/analytics/query_pipeline.py`, `repository/metrics_elastic_repository.py`, new `repository/metrics_aggregation_snapshot.py`; handler, filter and snapshot tests.

- [x] Split selection from details: query one selects execution IDs and global totals; query two reapplies all filters plus only the current page's IDs and aggregates every document, ordering by `_key` with all selected groups fitting on every shard.
- [x] Restore newest-first page order; keep global totals independent of pagination; skip query two for empty pages; retain the 10,000-execution pagination bound and existing response columns.
- [x] Use a short-lived PIT, reuse refreshed PIT IDs and close it after success or failure. Reject unavailable/expired snapshots, failed shards, timeouts and missing detail buckets with 503. Other analytics endpoints keep their existing behavior.
- [x] Update focused tests for two-phase filtering, ordering, totals and PIT lifecycle. No commit or push is part of this task.
- [x] Run quality gates and validate against the real local Docker Compose backend, including an adversarial cross-index execution that the previous query undercounts. Results below.

**Validation evidence (2026-11-02):**

- `poetry run pytest -q -n 0 tests/codemie/service/analytics tests/codemie/repository/test_metrics_aggregation_snapshot.py tests/codemie/repository/test_metrics_elastic_repository.py tests/codemie/rest_api/routers/analytics/test_usage_run_attributes.py tests/codemie/rest_api/routers/test_analytics.py`: **1,024 passed, 8 skipped**. Focused snapshot/handler rerun: **17 passed**; the opt-in live regression skips unless enabled.
- `make ruff`, `make build`, `make license-check`, `git diff --check`: passed. License gate: 2,851 files, none missing headers.
- Actual running stack: Compose project `codemie-dev`, API `http://localhost:8080`, Elasticsearch 8.19.12 on `http://localhost:9200`. The backend source mount points at this checkout; `execute_exact_tabular_query` was confirmed present inside the container.
- `uvx --offline codemie-test-harness run sanity-api -n 2 --reruns 2 --results-dir /tmp/epmcdme-16484-validation/harness` with local API/auth environment: **196 passed, 4 skipped in 522.03s**. Logs and JUnit: `/tmp/epmcdme-16484-validation/harness.log` and `harness/junit.xml`.
- `poetry run python /tmp/epmcdme-16484-validation/run-live.py` supplies local credentials in memory and invokes `poetry run pytest -q -n 0 tests/codemie/service/analytics/handlers/test_workflow_executions_live.py -o junit_family=legacy --junitxml=/tmp/epmcdme-16484-validation/live.xml`: **1 passed** against the actual API and ES.
- Live fixture: two temporary single-shard metrics indexes, 101 executions, one execution resumed across indexes. The previous aggregation returned **10** input tokens; the fixed public API returned **110 input / 55 output / 165 total tokens**, **2 usage records**, **$1.10**, and **90 seconds** for that execution at page sizes **1, 20 and 1,000**. Global totals stayed **210 input / 255 output / $2.10**; second and empty pages were correct. Invalid attribute filters returned **422**.
- Live PIT check: usage inserted and refreshed between searches was excluded from the shared snapshot, and visible to a fresh search. Searching the closed PIT returned **503** through the snapshot wrapper. Unit tests additionally reject simulated timeouts, failed shards, partial PIT opens and missing detail buckets, and verify cleanup when the second query raises.
- Backend logs confirmed successful API calls at the tested page sizes and a 422 for the invalid filter. Cleanup inspection found **no temporary validation indexes and zero open search contexts**. Pre-existing Docker services remain running.
- Initial live fixture used an end-of-day timestamp later than the current time and correctly received 400. The fixture now uses the previous completed UTC day; the regression was rerun successfully. These small-fixture requests are correctness evidence, not a production performance benchmark.

### Task 18: Architecture review fixes (2026-11-02)

Fixes from the architecture review in `docs/superpowers/reviews/2026-11-02-arch-run-attributes/` (findings 1, 3, 5, 6, 7, 10, 11, 12, 13, 15, 17). The other findings are deferred.

**Files:** `rest_api/routers/utils.py`, `rest_api/routers/assistant/_ask.py`, `rest_api/routers/ide.py`, `core/run_attributes.py`, `rest_api/models/conversation.py`, `rest_api/routers/analytics/usage.py`, `rest_api/routers/analytics/_common.py`, `service/analytics/query_builder.py`, `service/analytics/query_pipeline.py`, `service/analytics/handlers/attributes_handler.py`, `assistant_handler.py`, `workflow_handler.py`; tests for the chat and IDE routes, the conversation service, `core/run_attributes.py`, the filter and the attributes handler; the OpenAPI baseline.

- [x] **Access before history (finding 1).** The pre-flight check runs READ before it looks at history. For a conversation the caller cannot read, it drops the request's attributes, so a refused caller can no longer store their attributes on someone else's empty conversation through the error turn.
- [x] **No needless conversation read (finding 3).** A request without attributes skips the check. Stored attributes are no longer copied onto the request: `conversation_service` already keeps them for rows with history, and the metric reads them from the row. The IDE virtual route runs the check with `asyncio.to_thread`.
- [x] **One home for the check (finding 13).** `validate_run_attributes_and_raise(request, user)` moved from `_ask.py` to `routers/utils.py`. The IDE route no longer imports a private helper.
- [x] **Shared pair encoding (finding 6).** `KEYS_RECORD_FIELD`, `PAIRS_RECORD_FIELD`, `PAIR_SEPARATOR`, `encode_pair`, `pair_prefix` and `attributes_from_record` live in `core/run_attributes.py`, and the field paths derive from them. The filter, the `attributes-usage` handler, and the per-request and per-execution rows use them.
- [x] **Single key validation (finding 11).** `AttributesHandler.get_attributes_usage` no longer re-validates `attribute_key`; the route does it once and returns 422.
- [x] **Dead filter removed (finding 12).** `_build_query_without_time_filter` no longer applies the `attributes` filter left over from `/summaries`.
- [x] **Deep nesting is 422 (finding 17).** `parse_attributes_filter` maps `RecursionError` from `json.loads` to `RunAttributesError`.
- [x] **Descriptions (findings 5, 7, 15).** The chat request says attributes are ignored when `save_history` is false. `Conversation` and `ConversationResponse` use `CONVERSATION_RUN_ATTRIBUTES_RESPONSE_DESC` (null for workflow conversations). The request, filter and `attributes-usage` descriptions say values are caller-declared and unverified; `attributes-usage` says keys are not listed and an unknown key returns an empty table. In the OpenAPI baseline only these descriptions changed: the live schema was copied in description by description, because the local app does not mount the enterprise LLM-proxy routes.
- [x] **Docs (finding 10).** Spec and plan citations of AC 3 and AC 6 that pointed at the wrong criteria were replaced with plain wording, and stale plan lines (value rule, migration test) corrected.
- [x] **Tests.** The chat and IDE tests cover: no conversation read without attributes; identical attributes accepted; attributes dropped for an unreadable conversation, empty or with history, through `_ask_assistant`; the IDE check off the event loop. The conversation service test owns its conversation, matching the READ guard on usage labels from `9aea1b82b5` (it failed since that commit), and adds an unreadable case. Also covered: the shared encoding, `attributes_from_record`, deeply nested filters, and the all-time query ignoring the filter. The handler test for re-validation was removed with that check; the route test still covers 422.

---

## Self-review notes

- negative-constraints: honored. Tests: Tasks 1-7 were first built without tests (a misread repo rule); Task 12 added them, Tasks 10 and 14 are test-first. No `handle_analytics_errors` change (Task 6/7 raise `ExtendedHTTPException(422)` explicitly). No OTel path (Task 3 uses log-only `extra_log_fields`, not `additional_attributes`). No ES mapping or JSONB index (Task 2). No pre-flight inside the `try` (Task 4). No virtual-assistant, `metadata` or `tags` reuse. No LiteLLM/Langfuse forwarding, UI or SDK.
- Spec coverage: AC1 T1-2; AC2 T1; AC3 T4; AC4 T3-5; AC5 T3; AC6 T6; AC7 T7; AC8 T2; AC9 superseded (T8 removed); AC10-11 T10; AC6 for `assistants-requests` T14; `attribute-keys` and the `/summaries` filter removed T15; per-run workflow rows and AC12 T16-17; tests for AC1-8 T12; AC 9 docs T13; architecture review fixes to AC3, AC6, AC7 and the Docs topics T18.
