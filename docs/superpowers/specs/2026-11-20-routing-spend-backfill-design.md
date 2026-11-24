# Routing cost: backfill `routing_call_usage` spend from the spend ledger

Status: ready for implementation (v3) | Date: 2026-11-20 | Replaces: draft v1 of 2026-11-19, "settle `routing_call_usage` documents from the spend ledger"

Repos: `codemie` only. File paths are relative to `codemie/src/codemie/` unless they name another repo. The enterprise package (`codemie-enterprise`) needs no change.

## Read this first

You are implementing a scheduled job, the **spend backfill job**. It overwrites the money on routing usage documents in Elasticsearch with figures from the spend ledger in Postgres. The routing widgets then show real spend instead of CodeMie's estimate. The read side (handlers, UI) does not change.

Before writing code:

1. Read `codemie/AGENTS.md` and the guides it points to:
   - `.ai-run/guides/quality-gates.md`
   - `.ai-run/guides/testing/testing-patterns.md`
   - `.ai-run/guides/data/elasticsearch-integration.md`
   - `.ai-run/guides/development/configuration-patterns.md`
   - `.ai-run/guides/standards/git-workflow.md`
2. Commits need a Jira key (`EPMCDME-<id>: <summary>`). If none was given, ask for it before committing.
3. Everything under [Decisions already made](#decisions-already-made) was settled after tracing the code and testing on the local stack. Don't reopen it. If the code contradicts something in this document, trust the code, note it in the merge request, and say what you did instead.

## Background

### The widgets

| Widget | Where | Money it shows |
|---|---|---|
| Routing Summary | Analytics → Routing tab | Total original cost, classifier cost, estimated max cost, potential savings |
| Routing Paths | Analytics → Routing tab | Actual cost, estimated max cost, savings per router → model → tier |
| Routing impact | Project overview card | Potential savings |

All of them sum fields on Elasticsearch documents with `metric_name = routing_call_usage` (`service/analytics/handlers/routing_handler.py:336`, `:378`). They read `config.ELASTIC_METRICS_INDEX` (`codemie_metrics_logs*`) and recompute nothing.

### The routing document

There is one document per LLM call that went through a router. It is written once, when the call ends:

| Path | Writer | When |
|---|---|---|
| Chat and agents | `ConversationService._emit_routing_metrics` (`service/conversation_service.py:406`), one per LLM run | At the end of the turn |
| Proxy (CLI traffic) | `_queue_routing_metric` (`enterprise/litellm/proxy_router.py:1652`) | Right after the proxied call |

Both go through `RoutingMonitoringService.send_routing_metric` (`service/monitoring/routing_monitoring_service.py:42`), which writes a JSON log line. Fluent Bit ships the line to `codemie_metrics_logs*`. Fields end up under `attributes`. The indices are plain indices with dynamic mapping, no template and no ILM by default. Strings get a `.keyword` subfield, and money fields are mapped as `float`.

The fields this job reads:

| Field | Meaning |
|---|---|
| `@timestamp` | When the document was written (emission time) |
| `attributes.llm_run_id` | Id of the one LLM call. Set on chat documents; proxy documents lack it until task 1 below. |
| `attributes.request_id` | The chat turn, or the single proxied HTTP call |
| `attributes.endpoint` | Set only by the proxy |
| `attributes.requested_model` | The router's name, for example `gpt-auto-llm` |
| `attributes.routed_model` | The model that answered |
| `attributes.classifier_model` | The model that classified the request, if any |
| `attributes.routing_family` | `litellm` (LiteLLM auto-router) or `switchyard` (CodeMie-side router) |
| `attributes.tier` | `simple`, `medium`, `reasoning`, `complex` |
| `attributes.routed_cache_hit` | The answer came from LiteLLM's cache |
| `attributes.routed_input_tokens`, `attributes.routed_output_tokens` | Tokens of the routed call |
| `attributes.original_cost_usd` | Actual cost: routed call plus classifier |
| `attributes.estimated_max_cost_usd` | The same tokens priced at the counterfactual model, from CodeMie's table |
| `attributes.potential_savings_usd` | Estimated max minus actual |
| `attributes.classifier_cost_usd` | The classifier call's cost |
| `attributes.project` | Project name |

Why the money is often wrong:
- **Missing cost.** When LiteLLM returned no cost with the response, `original_cost_usd` is CodeMie's own estimate (`agents/callbacks/tokens_callback.py:283`, `proxy_router.py:1598`). Streamed Anthropic responses on the proxy never carry a cost (`codemie_enterprise/litellm/proxy_utils.py:297`).
- **Charges the document misses.** Retries are charged once per attempt, and LiteLLM can add discounts, margins or tool cost. None of that is on the document.
- **Switchyard classifier.** Switchyard's classifier cost is always CodeMie's estimate (`enterprise/switchyard/llm_clients.py:173`).

### The ledger

`codemie.litellm_spend_logs` (`repository/spend_logs_model.py`) holds one row per LLM call attempt that LiteLLM billed. It is range-partitioned monthly on `start_time`. The existing spend uploader job copies it from LiteLLM's own table every 2 minutes (`service/spend_uploader/scheduler.py`; the job logic is in `codemie_enterprise/spend_uploader/`). Columns used:

| Column | Meaning |
|---|---|
| `codemie_run_id` | Same value as the document's `llm_run_id`. Partial index `ix_spend_logs_run_id`. |
| `codemie_request_id` | Same value as the document's `request_id`. Partial index `ix_spend_logs_request_id`. |
| `model_group` | For the LiteLLM auto-router: the router name on the routed row, the classifier model on the classifier row. For direct calls (Switchyard): the model name. |
| `status` | `success` or `failure`. Failure rows usually have `spend = 0`. |
| `spend` | What LiteLLM charged (`numeric`) |
| `prompt_tokens`, `completion_tokens` | Tokens of the attempt |
| `start_time` | Partition key |

How the ids match up:
- **Chat path.** CodeMie sends both ids to LiteLLM on every call in the `x-litellm-spend-logs-metadata` header (`enterprise/litellm/llm_factory.py:224`). The run id is LangChain's id for the call (`tokens_callback.py:556`).
- **Proxy path.** The proxy mints one run id per request (`proxy_router.py:416`).
- **LiteLLM's classifier call.** The LiteLLM auto-router makes its classifier call with the same header, so one routed call normally has two rows under one run id.

Local example, run id `01a1f455…`:

| `model_group` | `spend` | Row |
|---|---|---|
| `gpt-auto-llm` | 0.03651125 | Routed call |
| `gpt-4.1-mini` | 0.0002 | Classifier |

Together they equal the document's `original_cost_usd`, 0.03671125.

### The spend uploader's guarantee

`run_feed()` returns a `RunSummary` (`codemie_enterprise/spend_uploader/models.py`) after reading the whole window `[since, until)`. `until` is the LiteLLM database clock pinned at tick start, as naive UTC. Every source row that ended at least `end_margin` (default 180 s) before `until` is in the ledger once `run_feed()` returns. `run_feed()` returns `None` when another pod holds the uploader's advisory lock, or when both tables are empty.

## Decisions already made

1. **Correct the documents in place.** Rejected alternatives:
   - a read-time join to the ledger: too heavy for the per-call counterfactual
   - LiteLLM's own `autorouter_savings`: its definition changes between LiteLLM versions, and it isn't in the ledger
   - emitting a second document
2. **A separate job, triggered after each successful uploader run.** It is never run inside the uploader job, so it can't delay the feed.
3. **Readiness comes from `RunSummary.until`, not `max_start_time`.** `max_start_time` stops moving when traffic stops.
4. **Settle only when the call's own successful ledger row is present.** For the LiteLLM family with a reported classifier cost, the classifier row must be present too.
5. **Actual cost comes from the ledger.** The counterfactual stays CodeMie's figure, multiplied only by the number of billed attempts. There is no price ratio, because `original_cost_usd` is LiteLLM's figure on some calls and CodeMie's on others.
6. **Classifier cost:** for the LiteLLM family it comes from the ledger; for Switchyard it is left as is.
7. **Writes are bulk partial updates** by `_index`/`_id` with `if_seq_no`/`if_primary_term`. No `update_by_query`, no Painless.
8. **Every money value is written as a Python `float`, counts as `int`.** Explicit mappings are added as a safeguard.
9. **Settings use the generic `SPEND_BACKFILL_` prefix**, and the code is split into an engine and targets. Later targets (proxy usage, conversation and workflow documents) plug into the same engine.
10. **Proxy documents get their run id** (task 1). Until then, and for history, proxy documents are matched by request id.
11. **There is no `unmatched` status.** A document with no status older than the max age simply wasn't matched.

## Behaviour

### Trigger and run

1. `SpendUploaderScheduler._run_spend_uploader` (`service/spend_uploader/scheduler.py:100`) calls `_build_and_run_spend_uploader()` in a thread, as it does today.
2. Change `_build_and_run_spend_uploader()` to return `(summary, end_margin)`, where `end_margin` comes from the built service's config (`SpendUploaderService` exposes its `SpendUploaderConfig` as a property at `codemie_enterprise/spend_uploader/service.py:27`; read its `end_margin`). If `None` comes back, there's nothing to do.
3. When `config.SPEND_BACKFILL_ENABLED` is true and a summary came back, compute:
   ```
   ready_bound = summary.until.replace(tzinfo=UTC) - end_margin - CLOCK_SKEW
   ```
4. Schedule a one-shot job on the same scheduler, without awaiting it:
   ```python
   self.scheduler.add_job(
       run_spend_backfill, trigger="date", args=[ready_bound],
       id="spend_backfill", name="Spend backfill", replace_existing=True,
       max_instances=1, coalesce=True, misfire_grace_time=60,
   )
   ```
   If a run is still going, APScheduler drops the new one, which is correct. The feed job returns at once.
5. `run_spend_backfill` builds the service and calls `run(ready_bound)`. It catches and logs every exception and never re-raises.

### One run

1. **Budget.** The deadline is `monotonic() + SPEND_BACKFILL_TIMEOUT_SECONDS`.
2. **Dry run.** Read once per run: `DynamicConfigService.get_bool_value_safe(SPEND_BACKFILL_DRY_RUN_KEY, default=config.SPEND_BACKFILL_DRY_RUN)`. Copy the pattern of `agents/assistant_agent.py:160`: a module-level key constant plus the static default.
3. **Targets.** For each target in the `TARGETS` constant, only `routing_call_usage` today:
   - **Recent window:** `[now - SPEND_BACKFILL_MAX_AGE_HOURS, ready_bound]`.
   - **History pass,** only if `SPEND_BACKFILL_HISTORY_START` is set and budget is left: `[max(HISTORY_START, SPEND_LEDGER_COVERAGE_FLOOR), now - MAX_AGE)`.
4. **For each window,** loop pages until the window is exhausted or the deadline passes:
   1. **Select** a page of ready documents ([Selection](#selection)).
   2. **Resolve each document's run id.** Run grain uses `llm_run_id`. Request grain resolves the run id from the ledger ([Matching](#matching)).
   3. **Fetch the ledger groups** for the page's run ids with one query.
   4. **Compute an update per document** with the target's formula ([Formula](#formula)).
   5. **Write.** Unless dry run, send the updates in one bulk request ([Writing](#writing)).
   6. **Count** everything for the log line.
5. **Log** one line per window ([Logging](#logging)).

### Selection

Use a point-in-time on `config.ELASTIC_METRICS_INDEX` with `keep_alive="2m"`, and close it in `finally`. Page with `search_after`, sorted `[{"@timestamp": "asc"}, {"_shard_doc": "asc"}]`, `size = SPEND_BACKFILL_BATCH` (default 1000), and `seq_no_primary_term: true`.

```json
{
  "bool": {
    "filter": [
      {"term": {"metric_name.keyword": "routing_call_usage"}},
      {"range": {"@timestamp": {"gte": "<window start, ISO UTC>", "lte": "<window end, ISO UTC>"}}}
    ],
    "must_not": [{"exists": {"field": "attributes.ledger_settlement"}}]
  }
}
```

- **Project allow-list.** When `SPEND_BACKFILL_PROJECTS` is non-empty, add `{"terms": {"attributes.project.keyword": [...]}}`.
- **`_source` fields.** Use the fields in the table under [The routing document](#the-routing-document). Each target declares its own list.
- **Cursors.** Dry run and the history pass keep their `search_after` cursor in memory on the service instance, which lives as long as the process. Without a cursor, dry run would re-read the same documents every run, because nothing drops out of the selection.
- **Continuous mode.** With writes enabled, the recent window starts from the beginning every run; settled documents drop out by themselves. Documents that stay pending are re-read until they age out.

### Matching

**Run grain:** documents with `llm_run_id`.

**Request grain:** documents without `llm_run_id` that have `endpoint` set (proxy):
- Resolve their run id from the ledger:
  ```sql
  SELECT l.codemie_request_id AS request_id,
         count(DISTINCT l.codemie_run_id) AS run_ids,
         min(l.codemie_run_id) AS run_id
  FROM unnest(CAST(:request_ids AS text[])) AS r(id)
  JOIN litellm_spend_logs l ON l.codemie_request_id = r.id
  WHERE l.start_time >= :lo AND l.start_time < :hi
  GROUP BY l.codemie_request_id
  ```
- **Exactly one run id:** use it, and continue as run grain.
- **More than one:** outcome `ambiguous`.
- **No rows yet:** pending, reason `no_rows`.
- **Rows but no run id on any of them:** pending, reason `no_run_id_on_rows`.

**Neither:** documents without `llm_run_id` and without `endpoint` (chat documents written without per-call data, `conversation_service.py:439`) get `no_basis`.

**Ledger groups:** one query per page, with all the page's run ids as one array parameter. This is the shape the spike ran:

```sql
SELECT l.codemie_run_id AS run_id, l.model_group, l.status,
       sum(l.spend) AS spend, count(*) AS rows,
       sum(l.prompt_tokens + l.completion_tokens) AS tokens
FROM unnest(CAST(:run_ids AS text[])) AS r(id)
JOIN litellm_spend_logs l ON l.codemie_run_id = r.id
WHERE l.start_time >= :lo AND l.start_time < :hi
GROUP BY l.codemie_run_id, l.model_group, l.status
```

- **Bounds.** `lo` is the page's oldest `@timestamp` minus `SPEND_LEDGER_EMISSION_LAG_HOURS` (default 24). `hi` is the window end, or `summary.until` for the recent window. These are the existing setting and convention for "a call starts before its document is emitted".
- **Engine and conventions.** Put both queries in `repository/spend_logs_repository.py`. Run them on `PostgresClient.get_ledger_async_engine()` (`clients/postgres.py:219`), and follow the repository's async session pattern. Bind the array as one parameter, never with `.in_()`. The table lives in schema `config.DEFAULT_DB_SCHEMA`; use the model's table object or whatever the repository already does.
- **Tests.** Compile both statements against the PostgreSQL dialect, the same way `tests/codemie/repository/test_spend_logs_repository.py` does.

**Classifying a run's groups**, in this order:

| Class | Rule |
|---|---|
| ignored | `spend == 0` and `status != 'success'`. Also any group with `spend == 0` and an empty `model_group`. Counted in `ledger_rows`, ignored otherwise. |
| routed | `model_group` equals the document's `requested_model` or `routed_model` |
| classifier | `routing_family == "litellm"` and `model_group` equals the document's `classifier_model` |
| foreign | Anything else |

**Outcome rules:** the first match wins.

| Condition | Outcome |
|---|---|
| No groups | pending (`no_rows`) |
| No routed group with `status == 'success'` | pending (`no_routed_success`) |
| `routing_family == "litellm"`, `classifier_cost_usd > 0`, no classifier group | pending (`no_classifier_row`) |
| Any foreign group | `ambiguous` |
| `original_cost_usd` or `estimated_max_cost_usd` missing | `no_basis` |
| `routed_cache_hit` is true | `no_basis` |
| `routed_input_tokens + routed_output_tokens <= 0` | `no_basis` |
| Otherwise | `settled`, with the formula below |

Pending documents are not written. They come back on the next run, until they fall out of the recent window.

### Formula

This is a pure function in `core/routing_costs.py`, next to `with_counterfactual_costs`, with no I/O. Inputs:

- **From the document:**
  - `O` = `original_cost_usd`
  - `M` = `estimated_max_cost_usd`
  - `C` = `classifier_cost_usd`, or `0.0` when missing
  - `T` = `routed_input_tokens + routed_output_tokens`
  - `tier`, `routing_family`
- **From the run's groups, ignored groups left out:**
  - `routed`: spend of the routed groups
  - `paid`: number of routed rows with spend > 0, taken from `rows` in the routed groups whose `spend > 0`
  - `routed_tokens`: tokens of the routed groups with spend > 0
  - `classifier`: spend of the classifier groups
  - `total`: spend of the routed and classifier groups
- **Convert first.** Make every `Decimal` a `float`.

```
attempts   = 1.0 if paid <= 1 else routed_tokens / T
actual     = total + (0.0 if routing_family == "litellm" else C)
est_max    = routed if tier == "complex" else M * attempts
clf        = classifier if routing_family == "litellm" else C
savings    = est_max - actual
```

Meaning:

- **Actual.** It is what LiteLLM charged for the call and, for LiteLLM's router, its classifier.
  - Switchyard's classifier call isn't under this run id (it carries no CodeMie ids, `enterprise/switchyard/llm_clients.py:145`), so its cost on the document is added.
  - That cost is CodeMie's own calculation (`llm_clients.py:173`).
- **Estimated max.** It stays CodeMie's price for the baseline model and is multiplied only by billed attempts.
  - **One attempt, the common case:** it is unchanged.
  - **Pricing extras:** LiteLLM's discounts, margins, tool cost and tier pricing move `actual` but not `est_max`. That's intended, and it's how LiteLLM's own savings figure treats them.
  - **Complex tier:** it keeps today's rule, est_max = routed spend.
- **Classifier.** For LiteLLM's router it is the ledger sum. A retried request is classified again and charged again.

Worked example: one SDK retry of a 1,000-token call, both attempts charged and both classified by LiteLLM.

| | Before | After |
|---|---|---|
| Routed | 0.0100 | 0.0200 (2 paid rows, 2,000 tokens, `T` = 1,000, `attempts` = 2) |
| Estimated max (`M`) | 0.0500 | 0.1000 |
| Classifier | 0.0002 | 0.0004 |
| Actual | 0.0102 | 0.0204 |
| Savings | 0.0398 | 0.0796 |

Local check: both local documents settle to their current values, with `attempts` = 1 and the ledger equal to the estimate.

### Writing

Use `elasticsearch.helpers.async_bulk` (elasticsearch-py 8.19.3) or the client's `bulk`, with `raise_on_error=False` and `refresh=False`. One action per document:

```python
{
    "_op_type": "update",
    "_index": hit["_index"],
    "_id": hit["_id"],
    "if_seq_no": hit["_seq_no"],
    "if_primary_term": hit["_primary_term"],
    "doc": {"attributes": {...fields below...}},
}
```

- **Partial merge.** A partial `doc` merges into the existing `attributes` object, so only the listed fields change.
- **`409`.** A `409` means the document changed since it was read, for example another pod got there first. Count it and don't retry.
- **Other item errors.** Count them by status and log the first few reasons.
- **Address by the hit.** Always write to the hit's own `_index`, never to the index pattern or the write alias.

**Fields written** (all under `attributes`):

| Field | Type | On |
|---|---|---|
| `ledger_settlement` | string: `settled`, `ambiguous`, `no_basis` | every decided document |
| `ledger_settlement_version` | int, `1` | every decided document |
| `ledger_settled_at` | ISO-8601 UTC string | every decided document |
| `ledger_spend_usd` | float: `total` (routed and classifier groups) | every decided document that had ledger rows |
| `ledger_rows` | int: sum of `rows`, including ignored groups | every decided document that had ledger rows |
| `ledger_attempts` | float | `settled` |
| `original_cost_usd` ← `actual` | float | `settled` |
| `estimated_max_cost_usd` ← `est_max` | float | `settled` |
| `potential_savings_usd` ← `savings` | float | `settled` |
| `original_cost_usd_estimated`, `estimated_max_cost_usd_estimated`, `potential_savings_usd_estimated` | float: the values read in this run | `settled` |
| `classifier_cost_usd` ← `clf`, and `classifier_cost_usd_estimated` ← the old value | float | `settled`, LiteLLM family only |

**Type rule.** Pass every money value through `float()` and every count through `int()`, including zeros. A new quarterly index maps a field from the first value it sees. Verified locally: a field whose first value was `0` became `long`, and `0 + 0.0365` then summed to `0`. A unit test must serialize the update with the client's JSON serializer and check the types.

**Explicit mappings**, as a safeguard against the type hazard above:
- **At startup.** When `SPEND_BACKFILL_ENABLED` is true, `rest_api/main.py` calls a repository method that runs `PUT codemie_metrics_logs*/_mapping`. It maps:
  - `attributes.ledger_settlement` as `text` with a `keyword` subfield
  - the floats as `float`
  - the counts and version as `long`
  - `ledger_settled_at` as `date`

  On failure it logs a warning and startup continues.
- **On index creation.** `MetricsIndexRotationService.rotate()` (`service/metrics_rotation/es_index_rotation_service.py`) creates new quarterly indices with the same mapping.

**Dry run:** compute everything and write nothing.

### Logging

There is one `logger.info` line per window, in the repo's `key=value` style:

```
spend_backfill target=routing_call_usage window=recent dry_run=true
  selected=… settled=… ambiguous=… no_basis=… pending=… pending_no_rows=… pending_no_routed_success=…
  pending_no_classifier_row=… pending_no_run_id_on_rows=… conflicts=… item_errors=…
  old_actual=… new_actual=… old_savings=… new_savings=… max_rel_change=…
  elapsed_s=… timed_out=…
```

The `old_*`/`new_*` sums are over settled documents only, in dry run and in write mode alike. They are the rollout's comparison data. Log no user or project names.

### Configuration

Add these to `configs/config.py`, next to the `LITELLM_SPEND_UPLOADER_*` block:

| Setting | Type | Default | Purpose |
|---|---|---|---|
| `SPEND_BACKFILL_ENABLED` | bool | `False` | Master switch, read when the job is triggered |
| `SPEND_BACKFILL_DRY_RUN` | bool | `False` | Compute and log only, write nothing |
| `SPEND_BACKFILL_MAX_AGE_HOURS` | int | `48` | Start of the recent window |
| `SPEND_BACKFILL_BATCH` | int | `1000` | Documents per page |
| `SPEND_BACKFILL_TIMEOUT_SECONDS` | int | `60` | Wall time per run |
| `SPEND_BACKFILL_HISTORY_START` | str | `""` | ISO-8601 UTC. Empty turns the history pass off. |
| `SPEND_BACKFILL_PROJECTS` | str | `""` | Comma-separated project names. Empty means all. |

Reused settings: `SPEND_LEDGER_EMISSION_LAG_HOURS` (`config.py:837`) and `SPEND_LEDGER_COVERAGE_FLOOR` (`config.py:825`). Document the new settings wherever the repo documents backend environment variables. Check the API configuration reference in the `docs` repo (`docs/docs/admin/configuration/codemie/api-configuration.md`) and add them there.

## Implementation tasks

Do them in this order. Each is a separate, reviewable commit.

1. **Proxy run id.** In `_queue_routing_metric` (`enterprise/litellm/proxy_router.py:1652`), pass `llm_run_id=request_info.get(MetricsAttributes.RUN_ID)` to `send_routing_metric`.
   - The value is set in `_prepare_proxy_headers` (`:1339` → `:416`) on the same `request_info` before the downstream call. `llm_proxy_monitoring_service.py:502` already reads it the same way.
   - Add a test asserting the routing metric gets the run id that was sent in the spend header.
2. **Formula.** Implement `backfill_routing_costs` in `core/routing_costs.py`, with the group classification and the outcome rules. Use small dataclasses for its input and output (for example `LedgerGroup`, `RoutingBackfill`). Pure unit tests cover every row of the outcome table, every formula branch, the worked example, and type conversion.
3. **Ledger queries.** Add the two queries to `repository/spend_logs_repository.py`, with SQL-compile tests.
4. **Elasticsearch methods.** In `repository/metrics_elastic_repository.py`, add these methods, with mocked-client tests:
   - `open_pit`, `close_pit`
   - `search_page(query, source, size, search_after, pit_id)`, returning the hits with `_index`, `_id`, `_seq_no`, `_primary_term`
   - `bulk_update(actions)`, returning per-item statuses
   - `put_attributes_mapping(properties)`

   Raise on an unavailable Elasticsearch: this is not the fail-open analytics path.
5. **Engine and target.** Add `service/spend_backfill/engine.py` with `SpendBackfillService`, and `service/spend_backfill/targets.py` with the target protocol and `RoutingCallUsageTarget`. The engine owns the windows, paging, cursors, budget, dry run, allow-list, matching flow, writing, counting and logging. The target owns:
   - the metric name and `_source` fields
   - its grain rules
   - group classification and the formula call
   - the update document
6. **Scheduling.** Make the `scheduler.py` change and add `run_spend_backfill`, with tests:
   - a successful feed schedules the job with the right bound
   - `None` schedules nothing
   - the feed job doesn't wait
7. **Settings, mapping, docs.** Add the settings, the startup mapping call in `rest_api/main.py`, the rotation-service mapping, and the docs entry. Replace "Elasticsearch has no later pass" in the docstrings at `tokens_callback.py:297` and `proxy_router.py:1603` with a pointer to the backfill job.

Run the quality gates from `.ai-run/guides/quality-gates.md` and the tests for the touched areas. A full suite run isn't needed.

## Acceptance criteria

- **Proxy run id.** With the job disabled, nothing changes anywhere except that new proxy routing documents carry `llm_run_id`.
- **Settled values.** With the job enabled and dry run off, every routing document that is ready and has its ledger rows gets `ledger_settlement`. Settled documents carry the formula's values and the `*_estimated` copies.
- **Repeat runs.** A second run over the same window writes nothing.
- **Dry run.** Writes nothing, and logs each document's figures once per process.
- **Feed.** Its timing and behaviour don't change.
- **Types.** Every money field written is a JSON float.
- **Local stack.** The two existing local routing documents settle to their current values (see [Local verification](#local-verification)).

## Local verification

**Test data.** `.tmp/routing-spend-backfill-fixtures/` in the workspace holds a snapshot of 24 local routing documents with their ledger rows and the expected result for each (see its `README.md`):
- all four local routers: `gpt-auto-heuristic`, `gpt-auto-llm`, `sy-signal-gpt41`, `sy-classifier-gpt5`
- chat and proxy calls, streamed and not
- run-id and request-id matching

After the job has run with writes on (and `SPEND_BACKFILL_HISTORY_START=2026-11-19T00:00:00Z`, since the documents are older than 48 hours), `verify.py` there must print `24/24 documents match`. On this data every document settles to its current values, so it proves matching and no regression. Cases where the ledger differs from the estimate need the unit tests.

What runs where locally:
- Backend: native on `http://localhost:8080`, with `LLM_PROXY_ENABLED=true` and `LITELLM_SPEND_UPLOADER_ENABLED=true` in `codemie/.env`.
- Elasticsearch: `http://localhost:9200`, index `codemie_metrics_logs`.
- Postgres: container `postgres`, schema `codemie`.
- LiteLLM: container `codemie-litellm-1`.
- Fluent Bit (`local-stack/fluent-bit/conf`): tails the backend log and stamps `@timestamp` from the log line's `time`.

Steps:

1. **Test data.** Chat through CodeMie with an assistant that uses a router model (`gpt-auto-llm` or `gpt-auto-heuristic`). Calls sent to LiteLLM directly have no CodeMie ids and produce no routing document.
2. **Expected local documents.** There are 2 routing documents (run ids `01a1f454…` and `01a1f455…`). Their expected settled values equal their current ones:
   - `0.0002216 / 0.0002 / -0.0000216`
   - `0.03671125 / 0.03651125 / -0.0002`
3. **Spike reference.** The throwaway script `.tmp/routing-settler-spike/spike.py` in the workspace runs selection, matching, formula and bulk writes on a scratch copy of the documents, then deletes the scratch indices. Use it as a reference, or test against a scratch index the same way, before running against the real index.
4. **Widgets still show $0.00 savings locally after the job runs. That's correct.** The two calls really saved nothing:
   - The reasoning-tier call was routed to the baseline model itself, so its savings are minus the classifier cost.
   - The simple-tier call saved 0.00016 on the model but paid 0.00018 for the classifier.
   - The sum is -$0.0002, which rounds to $0.00.

   To see non-zero savings, send longer prompts that route to a cheaper tier, or use `gpt-auto-heuristic`, which has no classifier cost.

## Failure modes

| Case | Result |
|---|---|
| Job error, Elasticsearch or ledger down | Logged; the feed is unaffected; the next run retries |
| Uploader outage | No runs; it catches up afterwards within `MAX_AGE`, and beyond that via the history pass |
| Run partly flushed | Pending until the routed success row (and the classifier row, if expected) exists |
| Two runs overlap, for example on two pods | The later write gets `409`, and nothing changes twice |
| Duplicate document from the shipper | Both get the same values, as today |
| Read-only or closed old index | Per-document errors, counted; the run continues |
| Deployment without LiteLLM | The uploader job isn't registered, so this job never runs |

## Read-path effects

- **Settled figures replace estimates.** The widgets' queries don't change; they return settled figures once documents are settled.
- **Mixed windows.** A window's newest few minutes mix estimated and settled documents.
- **Totals shift after the fact.** A period's total can change later. Kibana dashboards on these fields see the same change.
- **Older documents stay estimated.** Documents older than `MAX_AGE` at rollout keep their estimates unless the history pass covers them. Documents from before release 2.65.0 can't be matched at all.

## Rollout

1. Ship with `SPEND_BACKFILL_ENABLED=true`, dry run on. Check `EXPLAIN (ANALYZE, BUFFERS)` of the ledger query on a staging-sized ledger: `ix_spend_logs_run_id` should be used. If not, narrow `lo`/`hi` to the page's own time range, or lower the page size.
2. Collect a week of dry-run lines and compare the ratios from the window lines.
3. Set `SPEND_BACKFILL_PROJECTS` to one busy project and turn dry run off at runtime. Watch the counters and Elasticsearch indexing load.
4. Clear `SPEND_BACKFILL_PROJECTS`.
5. Optionally set `SPEND_BACKFILL_HISTORY_START` to the environment's 2.65.0 deploy date, and clear it after the "history pass complete" log line.
6. After two weeks, review the shares of pending, `ambiguous` and `no_basis`.

## Open questions

None of these block the implementation.

1. **Retry rule (product).** Retries raise both actual cost and the counterfactual. Is that acceptable, or should only the final attempt count? Implement the attempts rule as written; the formula version field allows a later change.
2. **Production query plan.** Checked during rollout step 1.
3. **Production shipper's `@timestamp`.** A wrong value only delays settling, because of the row-presence rule.
4. **Restricted Elasticsearch users.** The backend uses one Elasticsearch user for knowledge-base indexing, metrics reads and index rotation, so writing to `codemie_metrics_logs*` very likely works. Deployments with a restricted user need `write` and `manage` on that pattern.
5. **LiteLLM upgrades.** After each upgrade, confirm the auto-router still writes the classifier as its own row with `model_group` = the classifier model. The pending counters show it if not.

## Follow-ups (not in this change)

- **Switchyard classifier.** Send the spend-logs header on its call (`enterprise/switchyard/llm_clients.py`, `_call_litellm`) with the request id and run id, so its ledger row can replace CodeMie's estimate.
- **Cache hits.** Align cache-hit handling:
  - The proxy sets the counterfactual to 0 on a cache hit (`proxy_router.py:1624`).
  - The chat path prices it (`tokens_callback.py:323`, `:355`).
- **`request_count`.** On the routing summary it should count distinct requests.
- **More ledger columns.** Upload LiteLLM's `internal_call_origin` into the ledger, to identify classifier rows without relying on `model_group`.
- **Settled share.** Show the settled share on the routing cards.

### All LLM spend, through the same engine

The longer-term direction: the ledger stays the source of truth, this job copies it onto every usage document, and the widgets that read the ledger today are rewritten to read Elasticsearch. Each document type becomes a new target in `service/spend_backfill/targets.py`.

| Document | Ids on it | Status |
|---|---|---|
| `codemie_litellm_proxy_usage` | `run_id`, `request_id` (`llm_proxy_monitoring_service.py:501`) | Ready: the same grain as routing |
| `conversation_assistant_usage` | `request_id` (the turn) | Decide what the turn total includes. The ledger sum by request id also includes tool LLM calls and classifier rows. |
| `workflow_execution_total`, one per LLM run | `request_id` (the execution) | Add `llm_run.run_id` to the emission first (`workflow_monitoring_service.py:147`) |
| `datasource_tokens_usage` | none | Add an id, or it stays unmatched |
| CLI usage documents (CLI Insights) | to be checked | |

Gaps that direction must close before a ledger-backed widget moves to Elasticsearch:

1. **Ledger spend no document claims.** Direct LiteLLM calls (`attr_form = unattributed`: 10 of 16 local rows), the Switchyard classifier, and turns that failed before emission. The job has to create documents for those rows, or the moved totals drop.
2. **Grouping fields.** The budget category and the charged project or customer (`attr_category`, `attr_project`, `attr_principal`) are only in the ledger (`service/spend/default_ledger.py:32`). They have to be copied onto the documents.
3. **Period edges.** The ledger buckets calls by `start_time`, Elasticsearch by emission `@timestamp`. Copy `start_time` onto the documents and bucket on it.
4. **Freshness.** Elasticsearch trails the ledger by a few minutes.
5. **Platform rules.** AD-29 and CL-48 in the `service/spend` docstrings forbid summing Elasticsearch money. They have to be replaced in writing with "sum only settled Elasticsearch money".
6. **History.** It can't be matched before 2.65.0.

Each widget moves only after a dry-run comparison shows its Elasticsearch total matches today's ledger figure.
