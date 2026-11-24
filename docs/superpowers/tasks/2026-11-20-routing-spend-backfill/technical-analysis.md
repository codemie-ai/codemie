# Technical Research

**Task**: spend backfill elasticsearch ledger routing
**Generated**: 2026-11-20
**Research path**: filesystem

---

## 1. Original Context

implement C:\Users\KostiantynPshenychny\Documents\epm-cdme\repos\codemie\docs\superpowers\specs\2026-11-20-routing-spend-backfill-design.md. no ticket for now.

(Caller notes: the spec is the requirements document, v3. Trust code over spec where they disagree. Fixtures at `.tmp/routing-spend-backfill-fixtures/`. Branch `routing-spend-backfill` is checked out in `repos/codemie`; its only diffs vs `main` are unrelated local edits to `config/customer/customer-config.yaml`, `litellm_config.yaml`, `repository/hosted_applications/filesystem.py`.)

Paths below are relative to `repos/codemie/src/codemie/` unless prefixed.

---

## 2. Codebase Findings

### Existing Implementations

**Purpose A — routing widgets show money per routed call.**
- `service/analytics/handlers/routing_handler.py:51-71` field constants; sums at `:350` (paths) and `:386-388` (summary) via `_money_agg` (`:74`), filtered by `COUNTERFACTUAL_BEARING = exists attributes.counterfactual_model` (`:60`). Reads `config.ELASTIC_METRICS_INDEX` (`configs/config.py:193`). UI: `codemie-ui/src/pages/analytics/components/RoutingAnalyticsSection.tsx`.
- Money is computed once at write time: `core/routing_costs.py:20 with_counterfactual_costs` (savings = est_max - actual), called from `agents/callbacks/tokens_callback.py:284 _apply_counterfactual_costs` (chat) and `enterprise/litellm/proxy_router.py:1598 _apply_proxy_counterfactual_costs` (proxy). Proxy rules: cache hit → classifier 0, est_max 0 (`:1624-1626`); tier `complex` → est_max = money_spent (`:1627-1630`); actual = money_spent + classifier (`:1641`). When LiteLLM gives no cost, both price locally via `calculate_token_cost`.
- Emission: `service/monitoring/routing_monitoring_service.py:42 send_routing_metric` (already takes `llm_run_id`, `:52`, writes it at `:74`; pops classifier token fields for LiteLLM family `:109-112`) → `BaseMonitoringService` logs JSON (`service/monitoring/base_monitoring_service.py:77`; only `cached_tokens_money_spent` is float-cast, `:73`). Callers: `service/conversation_service.py:428` (per run, `llm_run_id=run.run_id`) and `:440` (collapsed fallback, no run id); `proxy_router.py:1664` (no `llm_run_id`).
- Distance: no later pass exists; nothing rewrites these fields after emission.

**Purpose B — replace an estimated money figure with the ledger's after the fact.**
- `service/spend/hydration.py:68 hydrate_request_cost` — settles Postgres conversation/workflow records (callers: `conversation_service.py`, `workflow_service.py`) by **request id**, only when `pending`, only writes a settled figure; "Nothing re-prices (AD-22)" (`hydration.py:17`).
- `service/spend/spend_service.py:565 cost_for_request` / `:600 costs_for_requests` → `enterprise/litellm/spend_ledger.py:672/692` → `repository/spend_logs_repository.py:1034 sum_for_correlation_id` (deliberately **no time bound**) and `:1063 sum_for_request_ids` (`.in_()` via `request_resolved`). Consumers: `service/analytics/handlers/request_spend.py:203`, `assistant_handler.py:323`, `hydration.py:86`.
- Distance: returns one summed figure per id; no grouping by `model_group`/`status`, no row count, no per-run attempts, not applied to Elasticsearch documents.

**Purpose C — run work after each uploader feed.**
- `service/spend_uploader/scheduler.py:29 SpendUploaderScheduler` owns its own `AsyncIOScheduler` (`rest_api/main.py:735`); `_build_and_run_spend_uploader` (`:89`, staticmethod) returns `service.run_feed()`; `_run_spend_uploader` (`:100`) runs it in `asyncio.to_thread` and only logs. No post-feed hook exists.
- Registered only when `LITELLM_SPEND_UPLOADER_ENABLED` and `LLM_PROXY_ENABLED` (`main.py:720-738`).

**Purpose D — update documents in `codemie_metrics_logs*`.**
- None found. Searched `src/` for `async_bulk`, `if_seq_no`, `seq_no_primary_term`, `search_after`, `put_mapping` on metrics. `MetricsIndexRotationService.rotate` (`service/metrics_rotation/es_index_rotation_service.py:41`) only creates indices (`indices.create(index=target)`, no body, `:56`) and swaps alias `codemie_metrics_logs_write`.
- Nearest precedents: PIT lifecycle in `repository/metrics_aggregation_snapshot.py:44-64` (`open_point_in_time(keep_alive="1m", allow_partial_search_results=False)`, close in `__aexit__`, rejects shard failures); sync `helpers.bulk` in `rest_api/utils/state_import.py:44`; `BaseElasticRepository.update` (`repository/base_elastic_repository.py:25`) for other indices.

**Other touched code**
- `repository/metrics_elastic_repository.py:72 MetricsElasticRepository` — `fail_open_on_unavailable` default False raises `ExtendedHTTPException` 503/400/500 (`:95-105`, `:138-166`); client from `ElasticSearchClient.get_async_client()` (`clients/elasticsearch.py:48`).
- `repository/spend_logs_model.py` — `start_time TIMESTAMP(timezone=True)` PK (`:88`), `status` (`:101`), `model_group` (`:108`), tokens (`:114-115`), `codemie_run_id` (`:136`), `ix_spend_logs_run_id` (`:207`), `ix_spend_logs_request_id` (`:210`). No schema on the model; `search_path={DEFAULT_DB_SCHEMA},public` is set on engines (`clients/postgres.py:156,190`).
- `clients/postgres.py:219 get_ledger_async_engine`; `:427 get_ledger_async_session()` context manager already exists.
- `service/dynamic_config_service.py:594 get_bool_value_safe` (sync classmethod).
- Enterprise (installed `codemie_enterprise` 2.3.47 in `.venv`; `pyproject.toml:57` pins 2.3.49; checkout is 2.3.49): `SpendUploaderService.config` property (`service.py:27`), `SpendUploaderConfig.end_margin` default 180 s (`config.py:20,55`), `RunSummary` (`models.py:10-21`: `since`, `until`, `max_start_time`, …), `until = source_now()` = naive UTC (`runner.py:139,149`; `source.py:126-130`).

### Decisions Already in the Code
- Every ledger read is bounded by half-open `start_time` (`spend_logs_repository.py:23-28`); helper `within(TimeRange)` (`repository/spend_projection.py:160`). Exception: correlation-id lookups are deliberately unbounded (`:30-31`, `:1041-1056`) — the spec's two queries are bounded, i.e. they follow the general rule, not this exemption.
- Id sets: module convention is `.in_()` (`service/spend/traffic_class.py:189`, `spend_logs_repository.py:1070`); a precedent for one `ARRAY(Text)` bindparam (`= ANY(...)`) exists in `spend_projection.py:465`. Spec mandates `unnest(CAST(:ids AS text[]))`.
- Module docstring: "This module reads. It never writes" (`spend_logs_repository.py:44`) — the spec's queries are reads.
- "Money never comes from Elasticsearch (AD-29)" (`service/leaderboard/collector.py:21,1101`); routing widgets already sum ES money and the spec keeps that.
- Dynamic-config pattern: key constants live in `service/constants.py` (e.g. `:49`), read as `get_bool_value_safe(KEY, default=config.X)` (`agents/assistant_agent.py:160-164`).
- Repository raises HTTP-shaped `ExtendedHTTPException` on ES errors, not domain exceptions.
- Scheduler jobs catch and log every exception (`scheduler.py:109-113`).

### Task Statements Checked Against the Code
- Enterprise needs no change; `SpendUploaderService` exposes config at `service.py:27` — **holds** (also in installed 2.3.47).
- `_run_spend_uploader` at `scheduler.py:100`, calls `_build_and_run_spend_uploader` in a thread — **holds** (`:110`).
- `RunSummary.until` is naive UTC; `end_margin` default 180 s; `None` on lock held/empty — **holds** (`source.py:127`, `config.py:20`, `runner.py:133-142`).
- Widgets sum at `routing_handler.py:336`, `:378` — **holds, line numbers drifted** (`:350`, `:386-388`); widgets also require `attributes.counterfactual_model` to exist (`:60`).
- `send_routing_metric` at `routing_monitoring_service.py:42` — **holds**; it already accepts `llm_run_id`.
- `_queue_routing_metric` at `proxy_router.py:1652`; run id minted at `:416` — **holds** (`request_info[MetricsAttributes.RUN_ID] = run_id` at `:417`). `_prepare_proxy_headers` is defined at `:1316` and called at `:2221`; the spec's `:1339` was not verified.
- `llm_proxy_monitoring_service.py:502` reads `request_info.get(MetricsAttributes.RUN_ID)` — **holds**.
- Chat fallback without run id at `conversation_service.py:439` — **holds** (`:439-447`).
- Docstrings "Elasticsearch has no later pass" at `tokens_callback.py:297`, `proxy_router.py:1603` — **holds, ±2 lines** (`:299`, `:1604`).
- Chat header `llm_factory.py:224` carries run id — **holds** (`:224-229`, `current_run_id`).
- Pattern "module-level key constant" from `assistant_agent.py:160` — **contradicted in detail**: the constant is in `service/constants.py:49`, not in the agent module.
- `SPEND_LEDGER_EMISSION_LAG_HOURS` `config.py:837`, `SPEND_LEDGER_COVERAGE_FLOOR` `:825` — **holds**. `LITELLM_SPEND_UPLOADER_*` block is at `:1084-1096`.
- `get_ledger_async_engine` at `postgres.py:219` — **holds**; `get_ledger_async_session` (`:427`) is the existing session helper.
- "Table lives in schema `DEFAULT_DB_SCHEMA`; use the model's table object" — **holds via search_path**, the model carries no schema.
- "Bind the array as one parameter, never `.in_()`" — **departs from module convention** (see Decisions); precedent exists in `spend_projection.py:465`.
- Indices are plain, dynamic mapping, money as `float` — **holds locally**: one index `codemie_metrics_logs` (61 docs), no aliases; `original_cost_usd`/`potential_savings_usd` mapped `float`, `llm_run_id`/`endpoint` `text`+`keyword`.
- elasticsearch-py 8.19.3, `helpers.async_bulk` — **holds** (`pyproject.toml:111`; `aiohttp` 3.14.5 installed).
- "Raise on unavailable ES" — **already the default** of `MetricsElasticRepository` (fail-closed).
- Docs entry at `docs/docs/admin/configuration/codemie/api-configuration.md` — file **exists**; it has no `LITELLM_SPEND_UPLOADER_*` or `SPEND_LEDGER_*` entries; nearest are `METRICS_ROTATION_ENABLED` (`:109`) and `ELASTIC_METRICS_INDEX` (`:269`). No `.env.example` in codemie.
- Spend backfill keeps write-time estimation (`with_counterfactual_costs`, both `_apply_*counterfactual_costs`) in place — **keeps mechanism A**; consumers listed in Purpose A.
- Multiplying `M` by attempts vs "Nothing re-prices (AD-22)" (`hydration.py:17`) — no price lookup is performed by the formula; recorded here without verdict.
- Spike `.tmp/routing-settler-spike/spike.py` — **exists** (191 lines; `async_bulk` with `raise_on_error=False`; no PIT).

### Consumers of Changed Contracts
- `_build_and_run_spend_uploader` return value: sole caller `scheduler.py:110` (log line reads `summary.rows_read` etc. at `:118-121`). No tests reference `SpendUploaderScheduler` (searched `tests/`).
- `MetricsIndexRotationService.rotate` create call: callers `service/metrics_rotation/scheduler.py`; 7 tests in `tests/codemie/service/metrics_rotation/test_es_index_rotation_service.py` (incl. `test_rotate_works_without_an_index_template`, `:115`) that assert on `indices.create`.
- `MetricsElasticRepository`: new methods are additive; existing constructors — AnalyticsService (fail-open), Leaderboard, StaleDatasource (`metrics_elastic_repository.py:80-83`).
- Overwritten fields `original_cost_usd`, `estimated_max_cost_usd`, `potential_savings_usd`, `classifier_cost_usd` on `routing_call_usage`: `routing_handler.py:350,386-388` and classifier overhead agg; `RoutingAnalyticsSection.tsx`. Not affected: `codemie-ui/.../ChatHeader.tsx` and `types/entity/conversation.ts`, which read routing money from the Postgres conversation record (`rest_api/models/conversation.py`), not ES.
- Proxy `llm_run_id` (task 1): no reader of `attributes.llm_run_id` in `src/` (searched); tests asserting proxy `add_task` kwargs at `tests/enterprise/litellm/test_proxy_router.py:1880-2040`.
- `rest_api/main.py` lifespan (`:1298`, schedulers `:1388-1401`): startup mapping call would join this sequence.

### Architecture and Layers Affected
- Scheduler/service: `service/spend_uploader/scheduler.py`; new `service/spend_backfill/` package.
- Core (pure): `core/routing_costs.py`.
- Repository: `repository/spend_logs_repository.py` (Postgres), `repository/metrics_elastic_repository.py` (ES).
- Integration: `enterprise/litellm/proxy_router.py`, `service/metrics_rotation/es_index_rotation_service.py`.
- Config/bootstrap: `configs/config.py`, `rest_api/main.py`. Docs repo: `api-configuration.md`.

### Integration Points
- Postgres `litellm_spend_logs` (monthly partitions on `start_time`, timestamptz) via ledger engine.
- Elasticsearch `codemie_metrics_logs*` via shared async client; Fluent Bit writes docs.
- Enterprise `SpendUploaderService` / `RunSummary` (`codemie/enterprise/spend_uploader/__init__.py` re-exports builder only).
- `DynamicConfigService` for runtime dry-run flag.

### Patterns and Conventions
- Schedulers: one `AsyncIOScheduler` per feature, `add_job(..., replace_existing=True, max_instances=1, coalesce=True)`.
- Repositories take injected `AsyncSession`; compile-to-SQL tests with PostgreSQL dialect.
- License header on every file; `logger` from `codemie.configs`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/data/elasticsearch-integration.md` (repository wrappers; mentions `BaseElasticRepository` `update`), `data/repository-patterns.md`, `data/database-patterns.md`, `development/configuration-patterns.md`, `testing/testing-patterns.md`, `quality-gates.md` (`make ruff`, `make build`, `make test`, `make license-check`, `make verify`).
- Prior routing design docs: `docs/superpowers/tasks/2026-09-14-epmcdme-14888-routing-metrics/spec.md` (deferred ledger join), `docs/superpowers/plans/2026-09-16-routing-source-parity.md`.

### Architectural Decisions
- AD-29 (join keys on both sides; ES money not summed for ledger figures) cited at `proxy_router.py:411`, `llm_proxy_monitoring_service.py:493`, `leaderboard/collector.py:21`. AD-22 "nothing re-prices" at `hydration.py:17`. Half-open bound / no-BETWEEN rule in `spend_logs_repository.py` docstring.

### Derived Conventions
- Commits `EPMCDME-<id>: <summary>`; no ticket given ("no ticket for now").
- `docs` repo is a fork: branch from `upstream/main`, push to `origin` (workspace CLAUDE.md).

---

## 4. Testing Landscape

### Existing Coverage
- `tests/core/test_routing_costs.py` — 3 tests for `with_counterfactual_costs`.
- `tests/codemie/repository/test_spend_logs_repository.py` — compiled-SQL helper at `:72` and `:673-677` (`postgresql.dialect()`, `literal_binds`).
- `tests/codemie/repository/test_metrics_elastic_repository.py` — `MagicMock` client with `AsyncMock` `search`/`esql.query`, patched `ElasticSearchClient` and `config`.
- `tests/codemie/service/metrics_rotation/test_es_index_rotation_service.py`, `test_scheduler.py` (MagicMock scheduler backend, `add_job.assert_called_once()`).
- `tests/enterprise/litellm/test_proxy_router.py:1880-2040` (routing metric `add_task`), `tests/codemie/service/monitoring/test_routing_monitoring_service.py`, `tests/codemie/service/test_conversation_service.py`.
- Fixtures: `.tmp/routing-spend-backfill-fixtures/` (`expected.json`, `verify.py`, `reference.py`).

### Testing Framework and Patterns
- pytest 8.4.2, `unittest.mock` (`AsyncMock`, `patch` on module paths), no DB/ES in unit tests.

### Coverage Gaps
- `SpendUploaderScheduler` has no tests at all.
- No test exercises writing to metrics ES documents, PIT paging, or mapping updates.

---

## 5. Configuration and Environment

### Environment Variables
- Existing: `LITELLM_SPEND_UPLOADER_ENABLED`, `_SCHEDULE` (`*/2 * * * *`) (`config.py:1084-1085`), `SPEND_LEDGER_COVERAGE_FLOOR` (`2026-11-01T00:00:00Z`, `:825`), `SPEND_LEDGER_EMISSION_LAG_HOURS` (24, `:837`), `SPEND_LEDGER_POOL_SIZE`/`_STATEMENT_TIMEOUT_MS` (engine isolation), `ELASTIC_METRICS_INDEX` (`:193`), `METRICS_ROTATION_ENABLED`, `LLM_PROXY_ENABLED`, `DEFAULT_DB_SCHEMA`. No `SPEND_BACKFILL_*` settings exist.

### Configuration Files
- `src/codemie/configs/config.py`; local `repos/codemie/.env`.

### Feature Flags and Deployment Concerns
- Uploader job requires proxy + LiteLLM DB; rotation behind `METRICS_ROTATION_ENABLED` (off locally: no write alias). One ES user (`ELASTIC_USERNAME`) for all ES access (`clients/elasticsearch.py:56-61`).

---

## 6. Risk Indicators

- `start_time` is `timestamptz` (`spend_logs_model.py:88`) while `RunSummary.until` is naive UTC: Speculative: `hi = summary.until` needs the same `tzinfo=UTC` treatment the spec applies to `ready_bound`.
- `MetricsElasticRepository` error handling is HTTP-shaped (`ExtendedHTTPException`); a scheduled job catching it is a new use of that class.
- `async_bulk(raise_on_error=False)` reports failures as an error list, not per-item statuses; counting 409 vs others relies on parsing it.
- Existing rotation tests assert `indices.create(index=target)` without a body (`test_es_index_rotation_service.py`); changing the create call touches them.
- Write-time estimation (Purpose A) stays in place; settled and estimated documents coexist in widget sums (see Task Statements).
- Classification depends on `model_group` equalling `requested_model`/`classifier_model`; Switchyard family docs match by model name only.
- No tests guard `SpendUploaderScheduler`; changing its return contract has no safety net today.
- Installed enterprise 2.3.47 differs from pinned 2.3.49 (property exists in both).
- First ES writes into `codemie_metrics_logs*` from the backend; restricted ES users would lack `write`/`manage` (spec open question 4).
- Spec line references drifted by a few lines in several places (see Task Statements).

---

## 7. Summary for Complexity Assessment

The change touches five layers in `codemie`: a pure formula in `core/routing_costs.py`; two bounded read queries in `repository/spend_logs_repository.py`; five new methods on `repository/metrics_elastic_repository.py` (PIT, paged search, bulk update, mapping); a new `service/spend_backfill/` package (engine plus one target); and wiring in `service/spend_uploader/scheduler.py`, `configs/config.py`, `rest_api/main.py`, `service/metrics_rotation/es_index_rotation_service.py`, plus a one-argument change in `enterprise/litellm/proxy_router.py` and two docstring edits. It also adds an entry in the `docs` repo. The enterprise package needs no change: `SpendUploaderService.config.end_margin` and `RunSummary.until` already exist.

The new part is that the backend writes to the metrics Elasticsearch indices for the first time: optimistic-concurrency bulk partial updates, point-in-time paging, and explicit mappings. Nothing in `src/` does any of that today. There are precedents to copy: PIT handling in `metrics_aggregation_snapshot.py`, sync `helpers.bulk` in `state_import.py`, an ARRAY bindparam in `spend_projection.py`, and the time-bounded ledger read convention. A ledger-to-record settle flow exists only for Postgres records, by request id and on read (`hydration.py`). The matching and formula rules are fully specified and backed by an independent reference implementation and a 24-document fixture with `verify.py`.

Tests exist for routing costs, compiled ledger SQL, the metrics repository (mocked client), rotation and the proxy routing metric. `SpendUploaderScheduler` has no tests at all, and nothing tests ES writes. Main risks: timezone handling of the ledger bounds, parsing bulk errors into 409 and other counts, the rotation tests that assert a body-less `indices.create`, and keeping every written money value a float.

---

## 8. External References

- `C:\Users\KostiantynPshenychny\Documents\epm-cdme\repos\codemie\docs\superpowers\specs\2026-11-20-routing-spend-backfill-design.md` — resolved, read in full (v3). It is the requirements source: trigger with `ready_bound = until(UTC) - end_margin - SKEW`; one-shot `add_job(trigger="date", id="spend_backfill", max_instances=1, coalesce=True, misfire_grace_time=60)`; selection by PIT and `search_after` sorted `[@timestamp asc, _shard_doc asc]`, `must_not exists attributes.ledger_settlement`; outcome table (pending `no_rows`/`no_routed_success`/`no_classifier_row`/`no_run_id_on_rows`, `ambiguous`, `no_basis`, `settled`); formula `attempts = 1 if paid<=1 else routed_tokens/T`, `actual = total + (0 if litellm else C)`, `est_max = routed if complex else M*attempts`, `clf = classifier if litellm else C`; fields written with float/int typing; 9 `SPEND_BACKFILL_*` settings; 7 ordered tasks.
- `C:\Users\KostiantynPshenychny\Documents\epm-cdme\.tmp\routing-spend-backfill-fixtures\` — resolved. `README.md`: 24 routing docs, 29 ledger rows, all four routers, chat (run id) and proxy (request id); every doc is expected `settled` with unchanged figures; run `../../repos/codemie/.venv/Scripts/python.exe verify.py` after a write run with `SPEND_BACKFILL_HISTORY_FROM=2026-11-19T00:00:00Z`; it prints `24/24 documents match` (0/24 before). `reference.py`: `classify()` treats a group as ignored when `spend == 0 and (status != "success" or not model_group)`; `ledger_rows` sums all groups including ignored ones; `ledger_spend_usd` = routed + classifier. `verify.py` checks `ledger_settlement`, `ledger_settlement_version == 1`, the four money fields as JSON floats (`isinstance(..., float)`), the `*_estimated` copies of three fields, `ledger_attempts`, `ledger_spend_usd` and `ledger_rows`, and unchanged money on non-settled docs. It queries `codemie_metrics_logs*/_search` by `_id`.
- `C:\Users\KostiantynPshenychny\Documents\epm-cdme\.tmp\routing-settler-spike\spike.py` — resolved (named by the spec). A 191-line throwaway using `elasticsearch.helpers.async_bulk(es, actions, raise_on_error=False)` on scratch indices.

## Facts Added by Review

Researched for the names the spec relies on, after the analysis above was written. Read like the facts layer; where a line here contradicts one above, this one was read later.

- `typing.Protocol` interfaces in services: `SpendLedger(Protocol)` at `src/codemie/service/spend/ledger.py:636` (imports `Protocol, runtime_checkable` at `:41`), with implementations in `default_ledger.py`, `legacy_ledger.py`, `cutover.py`, `enterprise/litellm/spend_ledger.py`, wired through `service/spend/ledger_registry.py`. `LLMProxyProvider(Protocol)` at `src/codemie/service/llm_proxy/provider.py:28`. Other Protocol users: `service/budget/provider.py`, `service/iac_export/...`, `service/mcp/auth_protocol.py`.
- The metric name a routing target would carry: `MetricName.ROUTING_CALL_USAGE = "routing_call_usage"` (`src/codemie/service/analytics/metric_names.py:106`). It is used by `routing_monitoring_service.py` (writer) and `routing_handler.py:40` (reader, `ROUTING_METRIC`).
- Served by `GET /v1/analytics/routing/summary` (`src/codemie/rest_api/routers/analytics/routing.py:50-65`).
- UI consumers: `codemie-ui/src/types/analytics.ts:261` (`ROUTING_SUMMARY`), `codemie-ui/src/pages/settings/administration/projectsManagement/ProjectOverviewSection.tsx:331` (`getRoutingMetric(..., 'request_count')`).
- Pinned by test `tests/codemie/service/analytics/handlers/test_routing_handler.py:463-468`, which asserts it uses `value_count`. Changing it to distinct (cardinality) would break that assertion.
- Defined: `RoutingInfo.routed_model: str | None` (`src/codemie/core/routing_info.py:151`, field around `:159`). `requested_model` is a field on the same model.
- LiteLLM router responses: header `x-litellm-router-routed-model` (`enterprise/litellm/litellm_router_headers.py:26,61`; `litellm_custom_callbacks.py:40`), mapped into `RoutingInfo` in `enterprise/litellm/router.py:83,98`.
- Proxy: `_resolve_routed_model` (`enterprise/litellm/proxy_router.py:1494-1522`) reads `ROUTED_MODEL_HEADER`. It keeps `llm_model` when the header is missing or equals the requested model; otherwise it overwrites `request_info[LLM_MODEL]`. Called at `:1701`.
- Switchyard: set by the Switchyard router (`tests/enterprise/switchyard/test_router.py:143`).
- Written to Elasticsearch: `RoutingMonitoringService.send_routing_metric` writes `"routed_model": routing.routed_model` (`src/codemie/service/monitoring/routing_monitoring_service.py:79`). The source comment says it is "what every routing query groups by". It is skipped when `routing.is_empty()` (`:65`). It can be `None` (`tests/enterprise/litellm/test_router.py:86`).
- Readers of `attributes.routed_model` in ES: `routing_handler.py` `ROUTED_MODEL_KEYWORD` (`:44`). It is used by the decision timeline (`:133,164`), the paths aggregation (`:345,369`), the group-by at `:484-487,519,535`, and `get_routed_model_distribution` (`:572-586`, endpoint `rest_api/routers/analytics/routing.py:191`).
- Other uses of the in-memory `RoutingInfo.routed_model`, not the ES field: counterfactual pricing (`tokens_callback.py:306-312,415`; `proxy_router.py:1608-1617`), thought metadata `llm_tier` (`agent_streaming_callback.py:194-212,396-406`; `agent_invoke_callback.py:85-95`), conversation aggregation (`rest_api/models/conversation.py:502-550`), assistant response (`rest_api/handlers/assistant_handlers.py:434,471`), `LLM_MODEL` metric attribute (`conversation_monitoring_service.py:77`), and the OpenAPI baseline (`tests/codemie/rest_api/router_guards/baselines/openapi.json:26685`).
- Unrelated name: `routed_model_name()` in `repository/cli_analytics/postgres/route_identity.py:53` (CLI SQL rollups).
- `routed_model_label` is deliberately not emitted to ES (`routing_monitoring_service.py:76-78`). Only `routed_model` is an analytics dimension.
- The proxy path stores the routed model only when it differs from the requested one (`proxy_router.py:1509`). Otherwise `request_info[LLM_MODEL]` stays the requested model. The reason found is the log text at `:1513`.
- Routing summary `request_count` uses `value_count` instead of `cardinality`. A test pins this (`test_routing_handler.py:463`), and its docstring says the reason is "not hits.total". No reason was found for choosing it over a distinct count.
- Field table "`attributes.routed_model` — The model that answered" (:54): holds for the LiteLLM and Switchyard paths. On the proxy path, when the header is missing or equals the request, `RoutingInfo.routed_model` comes from the extractor and can be `None` (`test_router.py:86`).
- "`request_count` ... should count distinct requests" (:484, follow-up, not in scope): this is true. Today it counts documents with a `request_id` (`routing_handler.py:384`). The spec keeps that mechanism in place, and its consumers are the routing summary endpoint, `ProjectOverviewSection.tsx:331`, and the pinning test.
