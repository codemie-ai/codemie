# Routing Spend Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After each successful spend uploader feed, a one-shot job overwrites the money on `routing_call_usage` Elasticsearch documents with spend-ledger figures.

**Architecture:** A pure formula in `core/routing_costs.py`, two bounded ledger reads, a fail-closed point-in-time reader plus a separate Elasticsearch writer, and a generic engine (`service/spend_backfill/engine.py`) driving one target (`RoutingCallUsageTarget`). The spend uploader scheduler triggers the engine.

**Tech Stack:** Python 3, SQLAlchemy async (PostgreSQL), elasticsearch-py 8.19.3 async client, APScheduler, pytest with `unittest.mock`.

**Spec:** `docs/superpowers/tasks/2026-11-20-routing-spend-backfill/spec.md`. It amends the binding design `docs/superpowers/specs/2026-11-20-routing-spend-backfill-design.md`, and where they disagree the spec wins. Paths are relative to `src/codemie/`.

## Global Constraints

- Repo `repos/codemie`, branch `routing-spend-backfill`. Commit per task using the repository's existing convention, with the placeholder key `EPMCDME-0000: <summary>` (no ticket yet; the user amends it later). Stage only the task's own files, by explicit path. Never stage `config/customer/customer-config.yaml`, `litellm_config.yaml` or `src/codemie/repository/hosted_applications/filesystem.py`.
- Test runs: only the touched areas, `poetry run pytest <paths>`. No whole-suite runs.
- `.tmp/routing-spend-backfill-fixtures/reference.py` is the yardstick for classification and the formula. Unit cases may mirror it. Production code never imports it.
- Every money value written goes through `float()` and every count through `int()`, zeros included.
- License header on new files. Use `logger` from `codemie.configs`.
- Use current line numbers from the analysis ("Task Statements Checked Against the Code"), not the design's.

## Review Focus

1. Ties on `@timestamp` across runs: a resumed scan must neither skip nor repeat documents that share the last timestamp (Task 5 test `test_resume_skips_seen_ids_at_same_timestamp`).
2. `RunSummary.until` arrives naive: both bounds must be UTC-aware before they reach `TimeRange` (Task 6 test asserts `tzinfo is UTC` on both args).
3. An inverted recent window (`flushed_bound` < window start), or a window wholly below the coverage floor, must skip without building a `TimeRange` (Task 5 test `test_recent_window_skipped_when_inverted`).
4. A bulk response that mixes 200, 409 and 5xx items: counts stay apart and the run continues (Task 4 and Task 5 tests).
5. A document with `*_estimated` already present (a re-settle at a higher version) uses those values as inputs and never overwrites them (Task 5 test `test_estimated_copies_read_and_not_rewritten`).

---

### Task 1: Proxy routing metric carries the run id

**Files:** Modify `enterprise/litellm/proxy_router.py:1652` (`_queue_routing_metric`). Test `tests/enterprise/litellm/test_proxy_router.py` (near `:1880-2040`).

Test-first: yes — a test that drives `_prepare_proxy_headers` then `_queue_routing_metric` on the same `request_info`, and asserts the `send_routing_metric` call kwargs include `llm_run_id` equal to the run id in the spend-logs header. It fails because the kwarg is absent today.

- [ ] Write the test and confirm it fails.
- [ ] Pass `llm_run_id=request_info.get(MetricsAttributes.RUN_ID)` to `send_routing_metric`. Update the existing assertions on the `add_task` kwargs.
- [ ] Run `tests/enterprise/litellm/test_proxy_router.py` and confirm it passes. Commit.

### Task 2: Backfill formula and the shared write-time rules

**Files:** Modify `core/routing_costs.py`, `enterprise/litellm/proxy_router.py:1627-1641`, `agents/callbacks/tokens_callback.py:318-333`. Test `tests/core/test_routing_costs.py`.

**Produces:**
```python
def counterfactual_is_actual(tier: str | None) -> bool: ...          # tier == "complex"
def actual_cost(routed_spend: float, classifier_cost: float) -> float: ...

@dataclass(frozen=True)
class LedgerGroup:
    model_group: str | None
    status: str | None
    spend: Decimal | float
    rows: int
    tokens: int

@dataclass(frozen=True)
class RoutingBackfillInput:
    routing_family: str | None
    requested_model: str | None
    routed_model: str | None
    classifier_model: str | None
    tier: str | None
    routed_cache_hit: bool
    routed_tokens: int                      # T
    original_cost: float | None             # O
    estimated_max_cost: float | None        # M
    classifier_cost: float                  # C, 0.0 when missing

@dataclass(frozen=True)
class RoutingBackfill:
    outcome: str                            # "pending" | "ambiguous" | "no_basis" | "settled"
    reason: str | None                      # pending: no_rows | no_routed_success | no_classifier_row; no_basis: missing_estimate | cache_hit | no_routed_tokens
    ledger_spend_usd: float | None = None
    ledger_rows: int | None = None
    attempts: float | None = None
    actual: float | None = None
    estimated_max: float | None = None
    savings: float | None = None
    classifier: float | None = None

def backfill_routing_costs(doc: RoutingBackfillInput, groups: Sequence[LedgerGroup]) -> RoutingBackfill: ...
```

Test-first: yes — tests fail on import: one per outcome-table row, one per formula branch (paid ≤ 1, paid > 1, tier `complex`, LiteLLM vs Switchyard `actual`/`clf`), the worked example (attempts 2 → 0.0204 / 0.1000 / 0.0796, classifier 0.0004), `Decimal` inputs giving `float` outputs, and the shared-model case (`routed_model == classifier_model`: LiteLLM settles with the classifier row as classifier, Switchyard settles with one row as routed).

- [ ] Write the tests and confirm they fail.
- [ ] Implement it as the spec's "Classification and formula" describes. The ignored rule comes first. Then one rule per family: LiteLLM routed = `requested_model`, classifier = `classifier_model`. Any other family routes by `routed_model` and has no classifier class. Everything else is foreign. The outcome order is the design's table. Build `actual` from `actual_cost(...)` and the `complex` branch from `counterfactual_is_actual(...)`. Convert every `Decimal` first.
- [ ] Switch the two write-time call sites to the two rule functions, with no behaviour change.
- [ ] Run `tests/core/test_routing_costs.py tests/codemie/agents/callbacks/test_tokens_callback.py tests/enterprise/litellm/test_proxy_router.py` and confirm they pass. Commit.

### Task 3: Bounded ledger reads

**Files:** Modify `repository/spend_logs_repository.py` (`SpendLogsRepository`). Test `tests/codemie/repository/test_spend_logs_repository.py` (compile helper at `:72`).

**Produces:**
- `async def resolve_request_runs(self, session: AsyncSession, request_ids: Sequence[str], period: TimeRange) -> dict[str, tuple[int, str | None]]` maps request id to (distinct run ids, `min(run_id)`). A request id with no rows is absent.
- `async def run_groups(self, session: AsyncSession, run_ids: Sequence[str], period: TimeRange) -> dict[str, list[LedgerGroup]]`

Test-first: yes — compile tests fail because the methods are missing. They assert, with the PostgreSQL dialect, `unnest(CAST(... AS TEXT[]))` with one bound array parameter, no `IN (`, `start_time >= ` and `start_time < ` (half-open, no `BETWEEN`), and the design's `GROUP BY` columns.

- [ ] Write the tests and confirm they fail.
- [ ] Implement both as the design's two SQL statements, built with the model's table. Each docstring says why these id-keyed reads are time-bounded, unlike the correlation-id exemption at `:30-31`: a page-sized id set from the metric store, partition pruning, and `lo` taken from the emission lag. Use `within(period)` from `repository/spend_projection.py:160` if it fits.
- [ ] Run the test file and confirm it passes. Commit.

### Task 4: Point-in-time reader and ledger writer

**Files:** Modify `repository/metrics_elastic_repository.py`. Create `repository/metrics_ledger_writer.py`. Tests `tests/codemie/repository/test_metrics_elastic_repository.py` and `tests/codemie/repository/test_metrics_ledger_writer.py`.

**Produces:**
- On `MetricsElasticRepository`: `async def open_pit(self, *, timeout: float, keep_alive: str = "2m") -> str`, `async def close_pit(self, pit_id: str) -> None`, `def scan(self, *, timeout: float, keep_alive: str = "2m") -> AbstractAsyncContextManager[str]` (opens and closes the point-in-time), and `async def search_page(self, query: dict, source: Sequence[str], size: int, pit_id: str, search_after: list | None, timeout: float) -> list[dict]`. Hits carry `_index`, `_id`, `_seq_no`, `_primary_term`, `sort`. `search_page` sorts `[{"@timestamp":"asc"},{"_shard_doc":"asc"}]` and sets `seq_no_primary_term: true`.
- In the new module: `LEDGER_ATTRIBUTES_MAPPING: dict` (the `attributes` properties listed under the spec's "Mapping") and `class MetricsLedgerWriter` with `async def bulk_update(self, actions: Sequence[UpdateAction], timeout: float) -> list[int]` (per-item `update.status`) and `async def put_attributes_mapping(self) -> None` (`indices.put_mapping` on `config.ELASTIC_METRICS_INDEX`). `UpdateAction` is a frozen dataclass: `index, id, seq_no, primary_term, attributes: dict`.

Test-first: yes — mocked-client tests fail because the methods are missing. Cover: the point-in-time opened with `allow_partial_search_results=False`; `_shards.failed > 0` or `timed_out` raising `ExtendedHTTPException`; `close_pit` called when the body raises; an unconfigured client raising; `bulk_update` sending `client.bulk(operations=..., refresh=False)` with `if_seq_no`/`if_primary_term` and the hit's own `_index`, and returning `[200, 409, 500]` from a mixed response; the mapping body equal to `LEDGER_ATTRIBUTES_MAPPING`.

- [ ] Write the tests and confirm they fail.
- [ ] Implement them, reusing `MetricsAggregationSnapshot._require_complete` (`repository/metrics_aggregation_snapshot.py:81`) and `_handle_unavailable`. Pass each call's `request_timeout` from its `timeout` argument.
- [ ] Run both test files and confirm they pass. Commit.

### Task 5: Settings, engine and routing target

**Files:** Modify `configs/config.py` (the nine `SPEND_BACKFILL_*` settings from the design's Configuration table, after `:1096`). Create `service/spend_backfill/__init__.py`, `engine.py`, `targets.py`. Tests in `tests/codemie/service/spend_backfill/test_engine.py` and `test_targets.py`.

The settings move here from the spec's step 7 because this is their first consumer. `SPEND_BACKFILL_DRY_RUN` is a plain environment setting, default `True`. No dynamic-config key.

**Produces (targets.py):**
```python
@dataclass(frozen=True)
class Outcome:
    status: str
    reason: str | None
    doc: dict[str, Any] | None            # partial `attributes`; None = no write
    sums: Mapping[str, float] = field(default_factory=dict)
    peaks: Mapping[str, float] = field(default_factory=dict)

class BackfillTarget(Protocol):
    metric_name: str
    source_fields: tuple[str, ...]
    async def decide(self, hits: Sequence[dict], ledger_period: TimeRange, timeout: float) -> list[Outcome]: ...

class RoutingCallUsageTarget: ...          # metric_name = MetricName.ROUTING_CALL_USAGE
TARGETS: Mapping[str, Callable[[], BackfillTarget]]
```

**Produces (engine.py):** `ScanPositions`, the per-process holder. It keeps a position per (target, window): the last `@timestamp` read and the set of `_id`s seen at it, plus a history-done flag. `SpendBackfillSettings.from_config()`. `SpendBackfillService(targets, positions, reader, writer, coverage, settings)`, with `async def run(self, flushed_bound: datetime, until: datetime) -> None`.

Test-first: yes — tests with mocked reader, writer and repository fail because the package is missing. Target tests cover: run grain; request grain with one, several and no run ids, and rows without a run id (`pending no_run_id_on_rows`); a resolved run with no groups (`pending no_rows`); neither grain (`no_basis`); the settled `doc` holding the design's "Fields written" (version `1`, `*_estimated` only when absent, classifier fields for LiteLLM only); `test_estimated_copies_read_and_not_rewritten`; every written field present in `LEDGER_ATTRIBUTES_MAPPING`; and the doc serialized with `elasticsearch.serializer.JSONSerializer` giving JSON floats for money and ints for counts. Engine tests cover: the selection query (version missing or `< 1`, recent `lte flushed_bound`, history `lt`, project `terms`); `test_recent_window_skipped_when_inverted`; the skip with one log line when `coverage.activated` is false; `test_resume_skips_seen_ids_at_same_timestamp`; the budget stop (`budget_hit=true`); 409s counted as `conflicts` and other statuses as `item_errors`; the write-mode wrap, at most once per run; dry run writing nothing and not wrapping; dry run over two runs sharing one holder, counting each document once; "history pass complete" logged once, with no re-scan after it; a second write run writing nothing; the log line holding `pod=`, `cursor_reset=true` only on the first run, and `<status>_<reason>` counts.

- [ ] Write the tests and confirm they fail.
- [ ] Implement `RoutingCallUsageTarget.decide`. Read O, M and C from the `*_estimated` copies when present. Request grain goes through `resolve_request_runs` and run grain through `run_groups`. Each query gets its own `get_ledger_async_session()` (`clients/postgres.py:427`), opened with `SELECT set_config('statement_timeout', :ms, true)` from the remaining budget. Call `backfill_routing_costs`. Settled outcomes return sums `old_actual`, `new_actual`, `old_savings`, `new_savings` and the peak `max_rel_change`.
- [ ] Implement the engine as the spec's "Bounds", "Scan position", "Selection" and "Engine and target" describe. Clip each window with `coverage.admitted(...)`. Open one point-in-time per window per run through `reader.scan()`. Resume with `gte last_ts` and drop the seen ids; `_shard_doc` `search_after` is used only inside that point-in-time. The ledger period runs from the page's oldest `@timestamp` minus `coverage.emission_lag` to `until` (recent) or the history end plus the skew. Send each call's timeout from the remaining budget. Catch `ExtendedHTTPException` per window. Log and skip an unknown name in `SPEND_BACKFILL_TARGETS`. The engine holds no routing names, and the log line holds no user or project names.
- [ ] Run `tests/codemie/service/spend_backfill/` and confirm it passes. Commit.

### Task 6: Trigger from the spend uploader scheduler

**Files:** Modify `service/spend_uploader/scheduler.py:89-121`. Create `service/spend_backfill/job.py`. Test `tests/codemie/service/spend_uploader/test_scheduler.py` (new, with `__init__.py`).

**Produces:** `async def run_spend_backfill(flushed_bound: datetime, until: datetime) -> None` in `job.py`. It builds the service around a module-level `ScanPositions` and `ledger_coverage()` (`service/spend/ledger_registry.py:97`). It catches and logs every exception and never re-raises. `_build_and_run_spend_uploader()` returns `(summary, end_margin) | None`, with `end_margin` taken from `service.config.end_margin`.

Test-first: yes — scheduler tests fail against the current single return value. Cover: with backfill enabled, a summary leads to one `add_job(run_spend_backfill, trigger="date", args=[flushed_bound, until], id="spend_backfill", replace_existing=True, max_instances=1, coalesce=True, misfire_grace_time=60)`, where `until = summary.until.replace(tzinfo=UTC)`, `flushed_bound = until - end_margin - SKEW`, and both are UTC-aware; `None` schedules nothing; a disabled setting schedules nothing; the feed coroutine returns without awaiting the backfill. A job test runs `run_spend_backfill` twice and asserts the same holder both times, and that a raising service is logged, not raised.

- [ ] Write the tests and confirm they fail.
- [ ] Implement it, keeping the feed's existing log line.
- [ ] Run the new test file and `tests/codemie/service/spend_backfill/` and confirm they pass. Commit.

### Task 7: Startup mapping, rotation mapping, docstrings

**Files:** Modify `rest_api/main.py` (lifespan, near `:1390`), `service/metrics_rotation/es_index_rotation_service.py:56`, and the docstrings at `agents/callbacks/tokens_callback.py:299` and `enterprise/litellm/proxy_router.py:1604`. Tests `tests/codemie/rest_api/test_main_lifespan.py` and `tests/codemie/service/metrics_rotation/test_es_index_rotation_service.py`.

Test-first: yes — tests fail because no mapping is applied today. A lifespan test asserts `put_attributes_mapping` is awaited only when `SPEND_BACKFILL_ENABLED` is set, and that its failure logs a warning while startup continues. The rotation tests assert `indices.create(index=target, mappings={"properties": {"attributes": {"properties": LEDGER_ATTRIBUTES_MAPPING}}})` whatever the backfill setting; the existing body-less assertions are updated.

- [ ] Write the tests and confirm they fail.
- [ ] Implement it. Replace "Elasticsearch has no later pass" with a pointer to `service/spend_backfill`.
- [ ] Run both test files plus `tests/codemie/agents/callbacks/test_tokens_callback.py` and confirm they pass. Commit.

### Task 8: Settings entry in the docs repo (separate repo: `repos/docs`)

**Files:** Modify `repos/docs/docs/admin/configuration/codemie/api-configuration.md` (near `METRICS_ROTATION_ENABLED`, `:109`).

Test-first: no — a documentation table.

- [ ] In the fork `repos/docs`, branch from `upstream/main` and add the nine `SPEND_BACKFILL_*` settings with type, default and purpose, as the design's Configuration table amended by the spec's dry-run change (environment only, restart to change). Commit with no ticket key in the subject or body.

<!-- negative-constraints pass:
read side unchanged — no task touches routing_handler, UI or Kibana;
write-time estimation behaviour unchanged — Task 2 extraction, existing tests kept;
no dynamic-config dry-run key — Task 5 config only;
no guard on ledger ids — Task 5 trusts them;
no codemie-enterprise change — Task 6 reads service.config;
no update_by_query/Painless/read-time join/second document/unmatched — Task 4 bulk partial updates;
no other targets — Task 5 TARGETS holds routing_call_usage only;
no Switchyard classifier header, cache-hit alignment, request_count fix, ledger columns, settled-share UI, retry-rule change — no task;
never .in_() — Task 3; never write to pattern or alias — Task 4 uses hit _index;
_shard_doc only inside one PIT — Task 5; *_estimated never overwritten — Task 5;
engine has no routing names — Task 5; no user or project names logged — Task 5;
feed never awaits backfill, job never re-raises — Task 6;
reference.py never imported by production code — Global Constraints;
no full suite run — Global Constraints. -->
