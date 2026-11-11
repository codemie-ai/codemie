# EPMCDME-16481: Per-request assistant analytics API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add read-only `GET /v1/analytics/assistants-requests` that returns one `TabularResponse` row per `conversation_assistant_usage` ES document.

**Architecture:** Reuse `AnalyticsQueryPipeline.execute_search_rows` (the pattern used by `RoutingHandler.get_decision_timeline`, `handlers/routing_handler.py:50-105`). Extend the pipeline and repository with optional `sort`/`track_total_hits`, and add an `assistant_id` term filter to `SecureQueryBuilder`. Access scoping comes unchanged from `SecureQueryBuilder`.

**Tech Stack:** FastAPI, Elasticsearch search API, pytest (`AsyncMock`/`MagicMock`).

**Spec:** `docs/superpowers/tasks/2026-10-30-epmcdme-16481/spec.md`

Commit per task using the repository's existing convention (`EPMCDME-16481: <summary>`, see `.ai-run/guides/standards/git-workflow.md`). Run tests with `poetry run pytest <path> -v`.

## Global Constraints

- Columns, in this exact order: `request_id, timestamp, conversation_id, assistant_id, assistant_name, user_id, user_name, user_email, project, llm_model, client_source, input_tokens, output_tokens, total_tokens, cache_read_input_tokens, money_spent, cached_tokens_money_spent, execution_time, status`.
- `total_tokens = input_tokens + output_tokens`, with a missing side counted as 0. Any other missing attribute is `null`.
- Sort is `@timestamp` desc. `track_total_hits: true`. `from = page*per_page`, `size = per_page`.
- `(page+1)*per_page > 10000` returns HTTP 400 without querying ES.
- The `routing/decisions` ES body must stay `{"query", "size", "from"}` only.

## Review Focus

1. Paging boundary: `(page+1)*per_page == 10000` is allowed and `10001` is rejected (Task 2).
2. Missing attributes: a document lacking `request_id` or `output_tokens` still yields all 19 keys. Unlike the routing parser, this parser never omits keys (Task 2).
3. Plain user plus `assistant_id`: the term is appended and the access-scope filter survives (Task 1).
4. Empty `assistant_id` (`""`/None): no assistant term is added (Task 1).
5. Both token sides missing: `total_tokens == 0`, not `null` (Task 2).

---

### Task 1: Query plumbing — optional sort/track_total_hits and assistant_id filter

**Files:**
- Modify: `src/codemie/repository/metrics_elastic_repository.py:228-248`
- Modify: `src/codemie/service/analytics/query_pipeline.py:43-55, 77-102, 522-542`
- Modify: `src/codemie/service/analytics/query_builder.py:37-39, ~231` (new constant and method)
- Test: `tests/codemie/repository/test_metrics_elastic_repository.py`, `tests/codemie/service/analytics/test_query_pipeline.py`, `tests/codemie/service/analytics/handlers/test_routing_handler.py`

**Interfaces — Produces:**
- `MetricsElasticRepository.execute_search_query(query: dict, size: int = 20, from_: int = 0, sort: list[dict] | None = None, track_total_hits: bool | None = None) -> dict`. Each key is added to the body only when it is not None.
- `AnalyticsQueryPipeline.execute_search_rows(result_parser, columns, filters=None, sort: list[dict] | None = None, track_total_hits: bool | None = None) -> dict`. It forwards `sort`/`track_total_hits` as kwargs only when not None, so existing `assert_called_once_with(query, size=…, from_=…)` assertions still hold.
- `AnalyticsQueryFilters.assistant_id: str | None = None`
- `ASSISTANT_ID_FIELD = "attributes.assistant_id.keyword"` and `SecureQueryBuilder.add_assistant_filter(assistant_id: str) -> SecureQueryBuilder`. It appends `{"term": {ASSISTANT_ID_FIELD: assistant_id}}` only when the value is truthy. `_build_query` calls it when `filters.assistant_id` is set. `_build_query_without_time_filter` is untouched.

Test-first: yes — repository body includes `sort`/`track_total_hits` only when passed; pipeline forwards them; `_build_query` with `assistant_id` adds the term alongside the security filter; routing decisions body has no `sort`/`track_total_hits`.

- [ ] **Step 1: Write failing tests.**
  - `test_execute_search_query_omits_sort_and_track_total_hits_by_default`: the body passed to `client.search` equals `{"query": q, "size": 20, "from": 0}`.
  - `test_execute_search_query_includes_sort_and_track_total_hits`: the body contains `sort=[{"@timestamp": {"order": "desc"}}]` and `track_total_hits=True`.
  - `test_execute_search_rows_forwards_sort_and_track_total_hits` (pipeline).
  - `test_build_query_adds_assistant_term_and_keeps_access_filter`: plain user, `assistant_id="a-1"`. Both the term and the user/project scope filter are present.
  - `test_build_query_skips_assistant_term_when_empty` (`""` and None).
  - In `test_routing_handler.py`, `test_decision_timeline_es_body_has_no_sort_or_track_total_hits`: use a real pipeline with a mocked ES client and assert the body keys are exactly `{"query", "size", "from"}`.
- [ ] **Step 2: Run the three test files.** Expect FAIL on the new tests.
- [ ] **Step 3: Implement the signatures above.**
- [ ] **Step 4: Re-run the three files plus `tests/codemie/service/analytics/`.** Expect PASS.

---

### Task 2: `AssistantHandler.get_assistants_requests` and row parser

**Files:**
- Modify: `src/codemie/service/analytics/handlers/assistant_handler.py`. Add the method after `get_assistants_chats` (~line 120).
- Test: `tests/codemie/service/analytics/handlers/test_assistant_handler.py`

**Interfaces:**
- Consumes: Task 1 `execute_search_rows(..., sort=, track_total_hits=)` and `AnalyticsQueryFilters.assistant_id`.
- Produces: `AssistantHandler.get_assistants_requests(time_period=None, start_date=None, end_date=None, users=None, projects=None, page: int = 0, per_page: int = 20, client_source: str | None = None, assistant_id: str | None = None) -> dict`, `@staticmethod _parse_assistants_requests(result: dict) -> list[dict]`, and the module constant `MAX_RESULT_WINDOW = 10_000`.

Behaviour:
- If `(page + 1) * per_page > MAX_RESULT_WINDOW`, raise `ExtendedHTTPException(code=400, message=…, details=…, help="Narrow the date range…")` before any pipeline call.
- Otherwise call `execute_search_rows` with:
  - metric filter `MetricName.CONVERSATION_ASSISTANT_USAGE.value`;
  - every filter, `assistant_id` included;
  - `sort=[{"@timestamp": {"order": "desc"}}]`, `track_total_hits=True`.
- Column types: `timestamp` is `date`; the token, cost and `execution_time` columns are `number`; all other columns are `string`. Labels are the title-cased ids.
- Parser: `timestamp` comes from `_source["@timestamp"]` and every other column from `_source["attributes"]`, read with `.get` so a missing value is None. `total_tokens = (input or 0) + (output or 0)`.

Test-first: yes — tests below fail with `AttributeError: get_assistants_requests`.

- [ ] **Step 1: Write failing tests.**
  - Shared module fixture `REQUEST_DOCS`: 3 hits, 1 assistant, 2 conversations. One hit lacks `request_id` and `output_tokens`.
  - `test_assistants_requests_columns_exact_order`: the column ids equal the Global Constraints list.
  - `test_assistants_requests_query_shape`: the metric filter, `assistant_id`, sort and `track_total_hits=True` are forwarded.
  - `test_parse_assistants_requests_nulls_and_total_tokens`: every row has 19 keys. The missing `request_id` is None, and `total_tokens` uses 0 for the missing side. A hit with no tokens at all gives `total_tokens == 0`.
  - `test_assistants_requests_reconcile_with_chats_totals`: `len(rows) == 3`. `sum(money_spent)` and `sum(total_tokens)` equal literal fixture totals computed per the `assistants-chats` definitions (spec AC8).
  - `test_assistants_requests_page_window_rejected`: `page=10, per_page=1000` raises 400 and the ES mock is not awaited.
  - `test_assistants_requests_page_window_boundary_allowed`: `page=9, per_page=1000` succeeds.
- [ ] **Step 2: Run the file.** Expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the file.** Expect PASS.

---

### Task 3: Service delegation, route, OpenAPI description, baselines

**Files:**
- Modify: `src/codemie/service/analytics/analytics_service.py`. Add the method after `get_assistants_chats` (line 362-383).
- Modify: `src/codemie/rest_api/routers/analytics/usage.py`. Add the route after `assistants-chats` (line 130-154).
- Regenerate: `tests/codemie/rest_api/router_guards/baselines/openapi.json`, and `route_precedence.json` if it changes.
- Test: `tests/codemie/service/analytics/test_analytics_service.py`, `tests/codemie/rest_api/routers/test_analytics.py`

**Interfaces:**
- Consumes: the Task 2 handler signature.
- Produces: `AnalyticsService.get_assistants_requests(...)`, with the same parameters as the handler, delegating by keyword. Route `GET /assistants-requests` with `response_model=TabularResponse`, decorated with `@handle_analytics_errors("assistants requests analytics")`. Its parameters are `user=Depends(authenticate)`, `params: AnalyticsQueryParams = Depends()`, `client_source: ClientSource | None`, and `assistant_id: str | None = Query(None, description="Exact assistant id")`. The body mirrors `get_assistants_chats` and passes `assistant_id`.
- The route's `description=` covers each of these:
  - chat (conversation) vs request (one turn, `request_id`; "runs" is reserved for workflows);
  - error message and error class are unavailable, only `status`;
  - `save_history=False` and virtual-assistant requests are absent;
  - the 10,000-row paging bound;
  - the five reconciliation bullets from spec §Reconciliation.

Test-first: yes — the service test asserts handler delegation with `assistant_id`. The route test calls `get_assistants_requests` directly with a mocked `AnalyticsService` and asserts that `assistant_id`, `client_source.value` and the parsed user/project lists are forwarded. Both fail until the methods exist.

- [ ] **Step 1: Write failing tests** `test_get_assistants_requests_delegates_to_handler` and `test_assistants_requests_route_forwards_filters`.
- [ ] **Step 2: Run both files.** Expect FAIL.
- [ ] **Step 3: Implement the service method and the route.**
- [ ] **Step 4: Regenerate baselines.** Run `poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines`, then `poetry run pytest tests/codemie/rest_api/router_guards tests/codemie/rest_api/routers/test_analytics.py tests/codemie/service/analytics/test_analytics_service.py -v`. Expect PASS. The baseline diff adds only the new route.

---

## Negative constraints (from spec Non-goals)

- No changes to metric emission: no task touches `service/monitoring/`.
- No change to the `assistants-chats` aggregation: Task 2 adds a method and leaves `get_assistants_chats` alone.
- `GET /v1/assistants/metrics/{conversation_id}` is untouched: no task edits `routers/assistant/`.
- No `conversation_id`/`request_id` filter, free-text search or `search_after`: Task 3's route adds only `assistant_id`, and paging is from/size.
- No workflow analytics, no SDK or docs-site changes, no PostgreSQL reads, no index or mapping changes, no UI: every file is in `codemie/src` or `codemie/tests` and the data comes from ES only.
- The `routing/decisions` body is unchanged: Task 1 adds the keys only when passed, and its test pins this.
