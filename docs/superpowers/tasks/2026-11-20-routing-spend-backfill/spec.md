# Spec: routing spend backfill

Source of requirements: `docs/superpowers/specs/2026-11-20-routing-spend-backfill-design.md` (v3, "the design"). Its "Decisions already made", Behaviour, Formula, Writing, Configuration, Failure modes and task order are binding and are not restated here, **except where [Changes to the design](#changes-to-the-design) supersedes them**: the user's answers at the spec gate override every design section they touch, including design decisions 8 and 9. This spec fixes scope, acceptance, and the calls made where the technical analysis (`technical-analysis.md`) found the code differs from the design. Paths are relative to `src/codemie/`.

## What and why

The routing widgets (`service/analytics/handlers/routing_handler.py:350`, `:386-388`) sum money written once at emission time, which is often CodeMie's estimate and misses retries and LiteLLM pricing extras. A scheduled **spend backfill job** runs after each successful spend uploader feed. It overwrites the money on `routing_call_usage` Elasticsearch documents with the figures from the Postgres spend ledger (`litellm_spend_logs`) using concurrency-checked bulk partial updates. The read side does not change. The code is split into an engine and targets so later document types can use the same engine.

## Scope (design task order, one commit each)

1. Proxy routing metric passes `llm_run_id=request_info.get(MetricsAttributes.RUN_ID)` (`enterprise/litellm/proxy_router.py:1652`; the value is set at `:417`).
2. Pure `backfill_routing_costs` with group classification, the outcome table and formula in `core/routing_costs.py`, with small input and output dataclasses, plus the two existing write-time rules defined once there (actual = routed + classifier; tier `complex` ⇒ estimated max = the routed spend). `proxy_router.py:1627-1641` and `agents/callbacks/tokens_callback.py:318-333` switch to them with unchanged behaviour.
3. Two bounded ledger reads (request id to run id resolution, run groups) in `repository/spend_logs_repository.py`.
4. Repository layer, fail-closed: `open_pit`, `close_pit`, `search_page` on `repository/metrics_elastic_repository.py`; `bulk_update`, `put_attributes_mapping` and the one attributes mapping on a separate writer class.
5. `service/spend_backfill/engine.py` (`SpendBackfillService`, the per-process scan-position holder) and `targets.py` (target protocol, `RoutingCallUsageTarget`).
6. `service/spend_uploader/scheduler.py`: `_build_and_run_spend_uploader` returns `(summary, end_margin)`; a successful feed schedules the one-shot `spend_backfill` job with both bounds; `run_spend_backfill` reuses the process's holder and catches and logs everything.
7. Seven `SPEND_BACKFILL_*` settings next to `LITELLM_SPEND_UPLOADER_*` (`configs/config.py:1084-1096`), the job applying the mapping once per process before its first writes (`service/spend_backfill/job.py`), mapping on index creation in `service/metrics_rotation/es_index_rotation_service.py:56`, docstring pointers replacing "Elasticsearch has no later pass" (`tokens_callback.py:299`, `proxy_router.py:1604`), and the entry in the `docs` repo `docs/docs/admin/configuration/codemie/api-configuration.md`.

## Changes to the design

- **Dry run** (supersedes One run step 2, the `SPEND_BACKFILL_DRY_RUN` config row, Rollout step 3): `config.SPEND_BACKFILL_DRY_RUN`, an environment setting, default `False`, read once per run. Rollout step 3 becomes "set `SPEND_BACKFILL_DRY_RUN=false` and restart".
- **Bounds** (supersedes Trigger and run steps 3-5, Matching "Bounds", the reused settings): the job gets `args=[flushed_bound, until]`, with `until = summary.until.replace(tzinfo=UTC)` and `flushed_bound = until - end_margin - CLOCK_SKEW` (the design's `ready_bound`). Each run reads `ledger_coverage()` (`service/spend/ledger_registry.py:97`) once and skips with one log line when it is not `activated` (`service/spend/coverage.py:205-213`). Both windows are clipped with `admitted(TimeRange)` (`coverage.py:215-225`). Ledger bounds are a `TimeRange`: `lo` = page's oldest `@timestamp` minus `coverage.emission_lag`; `hi` = `until` for the recent window, history window end plus `CLOCK_SKEW` (120 s, a constant in `engine.py`) for history.
- **Scan position** (supersedes Selection "Cursors" and "Continuous mode"): one holder per process, reused by every `run_spend_backfill`, keeps a position per target and window: the last `@timestamp` read plus the `_id`s already seen at it. Each run opens its own point-in-time and resumes from the position; `_shard_doc` in `search_after` is only used inside one point-in-time. A position older than the window start is clamped to it. In write mode the recent window is a wrapping sweep: on reaching the window end the position goes back to the window start, at most once per run. In dry run it does not wrap. History is swept once per process (see Decisions); a pass with failed or conflicting bulk items is not complete and starts over next run. The window log line has no `pod=` or `cursor_reset=` field; the settings are logged once when the job is registered.
- **Selection** (supersedes the `must_not` clause): documents whose `attributes.ledger_settlement_version` is missing or lower than the current version, `1`.
- **Classification and formula** (supersedes the design's classification table, Formula `actual` and `clf`): after the existing ignored rule, one rule per family, because each names its ledger rows differently (local fixture, all 24 documents and 29 rows). **LiteLLM:** routed = `model_group == requested_model` (the router alias), classifier = `model_group == classifier_model`, anything else foreign. **Switchyard:** routed = `model_group == routed_model` (the picked model); there is no classifier class, because its classifier call has no ledger row (it is sent without CodeMie ids); anything else foreign. `clf` = the ledger classifier spend for LiteLLM (pending `no_classifier_row` as before when the document reports a classifier cost and no classifier row exists), the document's `C` for Switchyard; `actual = routed + clf`; `est_max` = the routed spend for tier `complex`, else `M * attempts`. `O`, `M` and `C` are read from their `*_estimated` copies when present.
- **Fields written** (supersedes the `*_estimated` rows): `*_estimated` copies are written only when absent. A pending document gets `ledger_settlement: pending`, `ledger_pending_reason` and `ledger_settled_at`, only when its status or reason changes, never a version or money, so it stays selected; every other outcome writes `ledger_pending_reason: null`.
- **Mapping** (supersedes design decision 8's mapping and Writing "Explicit mappings"): one mapping in the repository layer, used by the job once per process before it writes, never in dry run (if the call fails, the run is skipped and retried next run), in the rotation `indices.create` body (always) and by a target test. Fields under `attributes`: `ledger_settlement` and `ledger_pending_reason` text with a `keyword` subfield; `ledger_settlement_version`, `ledger_rows` long; `ledger_settled_at` date; float: `ledger_spend_usd`, `ledger_attempts`, the four money fields the cards sum (`original_cost_usd`, `estimated_max_cost_usd`, `potential_savings_usd`, `classifier_cost_usd`) and their four `*_estimated` copies.
- **Engine and target** (supersedes design decision 9, One run steps 4.2-4.3, Implementation task 5's split, the Logging keys): the target supplies its metric name, `_source` fields, ledger key and the ledger read for its grain, request-to-run resolution included. For each hit it returns an outcome: status, reason, partial `doc` or none, and named sums for the log line. The engine owns windows, point-in-time paging, scan positions, budget, project allow-list, dry run, bulk writes, counting `<status>_<reason>` generically, summing the named sums and logging. The engine has no routing-specific code or names. Method names are left to the plan.
- **Rollout and open questions** (supersedes Rollout steps 2 and 6, Open question 3): step 2 compares the ratios new/old of actual and savings from the window lines, not absolute totals. Step 6 adds a recount of `ledger_rows` against the ledger. The presence rule only delays settling for the routed and classifier rows; a retry row that lands after settlement is never applied unless the version is raised.

## Constraints for the plan

- Open one ledger session per query, not per run, and give the ledger reads a statement timeout. Bound each page and each Elasticsearch call's timeout by the remaining budget.
- Open the point-in-time with partial shard results disallowed, treat shard failures as errors (`repository/metrics_aggregation_snapshot.py:44-64`), and close it through a context manager.
- Both ledger query docstrings say why these id-keyed reads are time-bounded, unlike the correlation-id exemption (`spend_logs_repository.py:30-31`).

## Acceptance criteria

1. With the job disabled, the only changes in behaviour are that new proxy routing documents carry `llm_run_id` and that newly created indices carry the attributes mapping; a test asserts the routing metric gets the run id sent in the spend header.
2. With the job enabled and dry run off, every ready routing document whose ledger rows are present gets whatever the outcome table decides other than pending; settled documents carry the formula's values and the fields of the design's "Fields written" table as amended here.
3. Unit tests cover every row of the outcome table, every formula branch, the worked example (attempts 2: 0.0204 / 0.1000 / 0.0796, classifier 0.0004), Decimal-to-float conversion and the shared-model case (`routed_model == classifier_model`): for LiteLLM it settles with the classifier row counted as classifier; for Switchyard it settles with the single row counted as routed.
4. A second run over the same window writes nothing.
5. Dry run writes nothing. With the holder reused across runs, each document is counted in one pod's log sums once per process; a test runs the job twice and checks this.
6. The feed's timing and behaviour do not change: the backfill is scheduled with `add_job(trigger="date", …)` and never awaited; `None` from the feed schedules nothing; scheduler tests cover both and check both bounds.
7. Every money field written is a JSON float and every count an int, checked by a unit test that serializes an update with the client's JSON serializer.
8. Both ledger statements compile against the PostgreSQL dialect in `tests/codemie/repository/test_spend_logs_repository.py`, bounded by half-open `start_time`, with ids bound as one array parameter.
9. Local stack: after a write run with `SPEND_BACKFILL_HISTORY_START=2026-11-19T00:00:00Z`, `.tmp/routing-spend-backfill-fixtures/verify.py` (run with `repos/codemie/.venv/Scripts/python.exe`) prints `24/24 documents match`.
10. Quality gates from `.ai-run/guides/quality-gates.md` pass, plus the tests of the touched areas (routing costs, ledger SQL, metrics repository, rotation, proxy router, tokens callback, new backfill and scheduler tests).
11. Engine and repository tests cover each matching outcome (one run id, several run ids, no rows, rows without a run id, no basis), 409s and other item errors counted separately, the budget stop, the project allow-list, the write-mode wrap and the skip when the ledger is not activated.
12. Selection takes documents with a missing or lower version; present `*_estimated` copies are used as inputs and never overwritten.
13. The job applies the mapping once per process before its first writes; a failure logs a warning and skips that run, which retries; a test checks every field the target writes is in the shared mapping.
14. The `docs` repo entry exists and lists the nine settings.

## Non-goals

- No change to the read side: routing handlers, UI and Kibana queries stay as they are.
- No change in the behaviour of write-time estimation (`with_counterfactual_costs`, `_apply_counterfactual_costs`, `_apply_proxy_counterfactual_costs`); settled and estimated documents coexist.
- No dynamic-config key, runtime switch or UI for dry run.
- No guard on run or request ids in ledger rows.
- No change to `codemie-enterprise`.
- No `update_by_query`, no Painless, no read-time ledger join, no second document, no `unmatched` status.
- No targets other than `routing_call_usage` (proxy usage, conversation, workflow and datasource documents are follow-ups).
- No Switchyard classifier spend header; once Switchyard's classifier call carries CodeMie ids, that follow-up must add a Switchyard classifier rule.
- No cache-hit alignment, no `request_count` fix, no new ledger columns, no settled-share UI.
- No change to the retry rule (attempts multiply the counterfactual, design open question 1).
- No full test suite run.

## Decisions

- Dry run is the environment setting `SPEND_BACKFILL_DRY_RUN` only (default `False`), over a dynamic-config key with a runtime switch; turning it off means setting the variable and restarting — user
- Ledger run and request ids are trusted as written, over checking the calling key, because LiteLLM is reachable only by CodeMie, run ids are always minted by CodeMie (`llm_factory.py:224-229`; `_apply_spend_logs_metadata_header`, `proxy_router.py:374`, which also strips any inbound header, `:166-170`), and a collision on a client-chosen request id resolves to `ambiguous` and moves no money; older proxy documents keep matching by request id — user, answers AR-2
- One per-process scan position per target and window, reused across runs and used in write mode as a wrapping sweep, over a cursor on a service rebuilt every run — user, answers AR-1
- The rollout compares ratios from the window log lines, over absolute totals summed across pods — user, answers AR-1
- Ledger groups are classified by one rule per family (LiteLLM: routed by `requested_model`, classifier by `classifier_model`; Switchyard: routed by `routed_model`, no classifier class), over one rule for both families that matches routed rows by `requested_model` or `routed_model` — user, answers AR-3
- The classifier cost comes from the ledger for LiteLLM and stays the document's own for Switchyard, over taking it from the ledger whenever a classifier group exists — user, answers AR-3
- The two write-time rules live once in `core/routing_costs.py` and both write-time call sites use them, over a third copy in the backfill — user, answers AR-7
- One attributes mapping with an explicit field list, money fields included, defined in the repository layer, over per-caller mappings — user, answers AR-8
- The target owns its ledger key, its grain's ledger read and its outcome reasons, over an engine that knows the routing grains — user, answers AR-9
- Selection by missing or lower version, with `*_estimated` written once and read as input, over a `must_not exists` selection that copies this run's values — user, answers AR-4
- Coverage, readiness and emission lag come from `LedgerCoverage`, over re-deriving them from raw settings — user, answers AR-5
- The job gets both `flushed_bound` and `until`, over `flushed_bound` alone — user, answers AR-6
- The design's `ready_bound` is called `flushed_bound`, over keeping the name, so "ready" keeps its `LedgerCoverage` meaning.
- Writes and the mapping call go on a separate repository writer class, over growing `MetricsElasticRepository`.
- `RunSummary.until` is converted with `.replace(tzinfo=UTC)` for the ledger `hi` bound as well as for `flushed_bound`, over passing the naive value, because `start_time` is `timestamptz` (analysis section 6).
- Ids are bound as one `text[]` parameter via `unnest(CAST(:ids AS text[]))`, over the module's `.in_()` convention, as the design mandates; precedent at `repository/spend_projection.py:465`.
- Ledger queries run through the existing `get_ledger_async_session()` (`clients/postgres.py:427`), over opening sessions on `get_ledger_async_engine()` by hand.
- `bulk_update` calls the async client's `bulk` and reads `items[*].update.status`, over `helpers.async_bulk(raise_on_error=False)`, so 409s and other failures are counted per item without parsing an error list.
- The engine catches the repository's existing `ExtendedHTTPException`, which `run_spend_backfill` logs, over adding domain exceptions to `MetricsElasticRepository`.
- The rotation service creates every new index with the attributes mapping regardless of `SPEND_BACKFILL_ENABLED`, over creating it only when enabled, so an index created while the job is off cannot pick the wrong type; the existing rotation tests that assert a body-less `indices.create` are updated.
- The recent window uses `lte flushed_bound` and the history window uses `lt now - MAX_AGE`, over `lte` on both, so the two windows do not overlap.
- The recent window is skipped when `flushed_bound` is earlier than its start, over querying an empty or inverted range.
- When the history scan position reaches the window end, the engine logs one "history pass complete" line and does not re-scan history for the rest of the process, over re-scanning it every run; the design's rollout step 5 relies on that line.
- A request-grain document whose resolved run id then has no groups is pending `no_rows`, over a new reason.
- Line references follow the current code, over the design's drifted ones (analysis "Task Statements Checked Against the Code").
- The `docs` repo entry goes on its own branch from `upstream/main`, over bundling it with the `codemie` change, because `docs` is a separate fork.

## Open risks

- Commits need an `EPMCDME-<id>` key and none exists yet ("no ticket for now"); committing waits for one or for the user's say-so.
- Deployments with a restricted Elasticsearch user need `write` and `manage` on `codemie_metrics_logs*` (design open question 4).
- Classification relies on each family's `model_group` naming (LiteLLM: router alias and classifier model; Switchyard: picked model), checked on local data only; LiteLLM or Switchyard upgrades can break it, and the pending and `ambiguous` counters show it.
- Whether the job's mapping update that conflicts with an existing index's type fails for every index is not verified; a failed call skips the run, so a lasting conflict stops the backfill.
- Dry-run absolute totals are per pod; only the ratios are comparable fleet-wide.
- `SpendUploaderScheduler` has no tests today; the new scheduler tests are its first.
- The installed enterprise package is 2.3.47 while `pyproject.toml` pins 2.3.49; `SpendUploaderService.config` exists in both.
