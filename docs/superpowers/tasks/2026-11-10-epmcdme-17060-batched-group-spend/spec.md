# EPMCDME-17060: batched spend-group read for per-row spend tables

## Problem

`read_group_spend` (`service/analytics/handlers/ledger_group_spend.py:140-160`) calls `spend_service.aggregate_by_spend_group` once per table row, which means one spend-ledger statement per row, up to 10,000 rows. This makes projects-spending, spending/by-users/platform and /cli, and the CLI Insights per-user and per-project widgets slow for unfiltered admin requests.

## Goal

Each `read_group_spend` call answers all its rows with **one** SpendLogs statement. Every row's `RenderedSpend` stays identical to what today's per-row read gives: money, `unavailable`, `partial` / `identity_bridge` portions and omitted groups. `read_group_spend` gains one input, the dashboard filter common to all its rows. The four call sites (`project_handler._ledger_money`, `user_handler._enrich_group_spend`, `cli/insights_handler._cli_spend_by_user` / `_cli_spend_by_project`) pass it and keep their own signatures.

Known fact (user): lab routes every call class through LiteLLM (`LLM_PROXY_EMBEDDINGS_DISABLED="false"`; lab traces show per-row SpendLogs ledger queries and no per-row Elasticsearch reads for windows above the floor). In that configuration the tables make one SpendLogs statement per read. The pre-floor part of a floor-crossing window is answered by the Legacy ledger's inner Default with one Elasticsearch search per page of rows.

## Design

**New port method** on `SpendLedger`, on `SpendService`, and on all six adapters:

```python
async def aggregate_by_spend_group_per_filter(
    self,
    period: TimeRange,
    *,
    access: AccessScope | None = None,
    common_filter: ReportFilter | None,
    report_filters: Collection[ReportFilter],
) -> Mapping[ReportFilter, Mapping[SpendGroup, SpendResult]]:
```

The method answers every requested filter, and an absent key is a defect (the convention at `ledger.py:685-687`). For each filter, the answer equals `aggregate_by_spend_group(period, access=access, report_filter=f)`. `common_filter` is the dashboard term every filter in `report_filters` carries unchanged. On `SpendService` only, `access` is keyword-only with **no default**, as on `aggregate_by_spend_group` (`spend_service.py:313-321`), so a forgotten boundary is a `TypeError`. The port and adapters keep `= None`.

**`read_group_spend`** becomes `(period, access, common_filter, report_filters, group)`. User tables pass the dashboard's projects term with its owners (the `base` of `user_report_filters` without users, `ledger_group_spend.py:80-84`). Project tables pass its users term (the `base` of `project_filters_within` without projects or owners, :99-112). It keeps rows with a `None` filter, or a `None` period, as `render_spend(None)` with no read, as today. Every remaining filter must carry each non-empty term of `common_filter` unchanged. A filter that does not is a caller defect and raises before any read, because its figure would be short. It deduplicates the remaining filters (they are hashable) and calls the facade once. It maps the answers back to rows in order. When no filter remains, it makes no call. It drops its `READ_CONCURRENCY` semaphore.

**Facade.** Each distinct ledger is asked once through `_ask` (`spend_service.py:543`). The lazy fallback is `no_figure` for every filter and group. The facade then combines each filter and group with `combine_results`, exactly as `aggregate_by_spend_group` does for one filter.

**SpendLogsLedger / repository.** Coverage gating stays as in `spend_ledger.py:380`. One grouped statement runs over the real read path, the hourly projection plus raw edge hours, through `_read`. Grouping lives on `ReadShape` and is rendered by both the raw and the projected renderer.
- Group key: the columns the batch's filter trees name, collected in one pass. Today these are `attr_user_id, attr_source, attr_form, attr_principal, attr_project` (`spend_logs_repository.py:231-249, 310-338, 375-387`). A column outside `PROJECTED_DIMENSIONS` raises.
- Aggregates: the existing `_spend_group_aggregates` FILTER columns (:622), so `spend_group_predicate` remains the only definition of the group split.
- WHERE: period, `access_predicate(access)` and `report_filter_predicate(common_filter)`, and nothing from the rows. No row's keys, aliases, projects or owners enter the statement, so its size does not grow with the row count. Each row's own term is applied only in Python.
- Source: the grouped projected statement reads the same `window_rows` (`spend_projection.py:303-320`) as the per-filter projected statement, hourly rows plus raw edge hours. Like the per-filter read, it carries no correlation ids, so neither has resolved or cancelled images (:284-291). A group whose rows net to zero contributes zero.

**Assignment in Python.** Each filter's own `report_filter_predicate` tree is evaluated against the grouped rows, and the filter's figure per spend group is the sum of the matching groups' columns. Spend is `Numeric` (`spend_logs_model.py:94`), so the sums are exact. A group matching several filters counts in each.
- The evaluator handles exactly the nodes the builders emit: `==`, `IN`, AND, OR, the grouping wrapper around an OR inside an AND (a pass-through), `true()` and `false()`. Any other node raises, and so does a column outside the group key. Either one fails the read rather than guessing.
- It works set-wise over per-column indexes of the grouped rows. An AND evaluates its most selective `==`/`IN` child first, as an index lookup, and tests the rest only on those candidate groups. Atom and shared-subtree results, including the common term every filter carries, are memoised per batch. Its cost is the sum of each filter's matched groups plus a per-batch constant, not rows × groups.
- Predicate building and assignment run in `asyncio.to_thread` after the session is released, as `_read` does for statement building (`spend_logs_repository.py:1346-1372`).
- A NULL column matches no comparison, the same as in SQL. NOT is unsupported, so two-valued evaluation agrees with SQL.

**Flags per filter.** `unavailable = coverage.unavailable_flag(period)` and `partial = _report_filter_label(filter)` are computed for each filter, not once per batch.

**CutoverSpendLedger.** With one leg, the call is delegated untouched. When the window crosses the floor, both halves are read through the batched method. Each half is flattened to `(filter, group)` keys, combined with `_per_key` unchanged, and re-nested. There is no second merge helper. Each filter's result equals today's crossing `aggregate_by_spend_group` for that filter.

**Default / Legacy / Noop.**
- Default answers the batch with one Elasticsearch search per page of rows. The outer query is `_query(period, common filter clauses, scoped_by=access)`. A `filters` aggregation holds one bucket per row carrying exactly that row's `_report_filters(row)` clauses, and inside it the same spend-group buckets and `_money_aggs` as `aggregate_by_spend_group`. Each row's figures go through `_money_figure` / `_money_result`, and a group Default cannot carry is omitted or answered `no_figure` exactly as `aggregate_by_spend_group` does. The spend-group selectors are built by one helper shared with `aggregate_by_spend_group`. Pages are bounded by a fixed row count sized to Elasticsearch's default bucket limit. The per-filter loop and `READ_CONCURRENCY` are removed. A failed page gives its rows `no_figure`.
- Default records its failures through `_ask`'s failure recording (`spend_service.py:543-600`), which moves into one function that both call. Once per batch, it records the failed row count on the failure metric under the batch's read name and writes one ERROR log with the count and the first error. When every page fails, Default raises, so `_ask` counts the outage as today.
- Legacy delegates to its inner Default.
- Noop returns `no_figure` for every filter.

**Failure and metrics.** If the batched statement fails, every row on that ledger leg gets the `no_figure` a single failed row gets today. The table still renders. The `read_group_spend` docstring and the module docstring (:17-32) are updated to say so. They also name a `SPEND_LEDGER_STATEMENT_TIMEOUT_MS` timeout (dedicated pool only, `SPEND_LEDGER_POOL_SIZE>0`) as one such cause, and that setting's comment says it is sized for one statement per table. `_ask` records one duration sample and at most one slow-read warning per ledger per batch, under the read name `aggregate_by_spend_group_per_filter`. The docstrings of `sum_for_spend_groups` and the batched read name AC-1 as the test that keeps the two equal.

## Decisions

- One SQL statement for all rows of a read over chunks of 100 or one FILTER column per row — user
- GROUP BY the attribution columns over the real read path with one shared WHERE, over per-row predicates in SQL — user
- Python assigns grouped rows by each row's own predicate, with a group counting in every row it matches — user
- "Which rows belong to whom" stays defined once and is proved by batch == per-row tests, over the "no GROUP BY" preference — user
- Native batch in SpendLogsLedger, its repository, CutoverSpendLedger and DefaultSpendLedger; Legacy delegates and Noop answers no figure — user
- The four call sites keep their signatures — user
- Shared WHERE is the period, access and the common dashboard filter passed into `read_group_spend`, over the union of the rows' keys and aliases — user, answers AR-1
- Default batches with a `filters` aggregation carrying each row's own filter clauses, over a `terms` or `composite` grouping on the user or project field — user
- A row filter that does not carry the common term raises, over trusting the caller
- Spend-group split kept as the existing FILTER aggregates, over adding `codemie_origin`/`attr_category`/`call_type` to the group key
- Group key collected from the batch's filter trees, over a hand-listed column set
- Assignment evaluates the row's own `report_filter_predicate` tree, over a Python restatement like `may_hold_rows`
- Set-wise evaluation over per-column indexes, over checking every group against every row
- Selective-first AND with per-batch memoisation, over evaluating each tree whole per row
- Assignment on a worker thread after the session is released, over the event loop
- Batch failure blanks every row on that leg, over falling back to per-row reads
- Result keyed by `ReportFilter` with dedup in `read_group_spend`, over a positional sequence
- Facade `access` keyword-only with no default, over the adapters' `= None`
- Cutover merges by flattening to `(filter, group)` keys through `_per_key`, over a nested merge helper
- Every adapter implements the method (Default batches, Legacy delegates, Noop answers no figure), over an optional method with a facade fallback
- A failed Default page costs only its own rows, over failing the whole batch
- Default records failures through `_ask`'s shared recording and raises when every page fails, over a silent catch
- Default pages at a fixed row count sized to the default bucket limit, over one unbounded search
- `partial` label computed per filter, over once per batch as `consumed_for_holders` does
- One metric sample per ledger per batch under a new read name, over per-row samples

## Acceptance criteria

- **AC-1** For every match path, the batched read gives each filter the same money and flags as `aggregate_by_spend_group` for that filter. This is shown on real rows (SQLite fixtures, `test_spend_logs_access_boundary.py` style). Match paths: explicit id, alias fallback, `aliases=None`, a shared alias across two users, a shared project, a personal-project pot, `personal_project_owners=None`, user rows sharing a projects term, project rows sharing a users term, an empty and a non-empty common filter, restricted access, and unrestricted access. It also covers a period the coverage does not admit (every filter gets `no_figure`) and a period where `unavailable_flag` is true, each matching per-filter `aggregate_by_spend_group`.
- **AC-2** SpendLogsLedger issues exactly one statement for N filters (capturing session). The projected statement compiles with the group key, the FILTER aggregates, the access conjunct and the common filter. It reads the same `window_rows` source as the per-filter projected statement: hourly rows, raw edge hours, and the same resolved-id / cancelled-pair handling, which this id-free read does not use. A group whose rows net to zero contributes zero.
- **AC-4** The evaluator raises on a predicate node outside the supported set and on a column outside the group key.
- **AC-5** `read_group_spend` makes one facade call with the common filter and the distinct non-`None` filters, and keeps row order. Repeated filters get equal figures. `None` rows and a `None` period read nothing. A filter that does not carry the common filter's term unchanged raises before any read.
- **AC-6** A failing batch gives every row `no_figure`, and the table is returned.
- **AC-7** The facade asks each distinct ledger once and combines per filter and group, including an omitted group and a ledger that did not answer.
- **AC-8** Cutover crossing gives each filter the same result as the per-filter crossing read. A single leg delegates untouched.
- **AC-9** Default answers N filters with one Elasticsearch search per page of rows. Each row bucket carries exactly that row's own clauses, and the outer query equals the per-filter query's other clauses. Figures and flags equal per-filter `aggregate_by_spend_group` for every filter, including groups Default cannot carry. A failed page costs only its rows, recorded once per batch as the failed count and one ERROR log line; a batch where every page fails raises. Legacy delegates, and Noop gives `no_figure`.
- **AC-10** The six-way signature tests (`test_access_boundary.py`) cover the new keyword-only method. The facade method is added to the no-default-boundary test (`test_spend_service.py:389-403`). The protocol fakes are updated.
- **AC-11** Handler tests (`test_ledger_group_spend.py`, `test_project_handler.py`, `test_user_handler.py`, `test_cli_handler.py`) stub the new facade method or the new `read_group_spend` signature. They show that the four call sites pass the common filter. The `_scope_ledger_total` footer stays on `aggregate_by_spend_group`, and existing figures are unchanged.
- **AC-12** For every shape `report_filter_predicate` emits (users only, projects only, both, `aliases=None`, owners `None` / empty / several), the evaluator selects the same ledger rows as the same filter's SQL on SQLite.
- **AC-13** The statement compiled for 1 filter and for 10,000 filters is the same. It contains no row's keys, aliases, projects or owners.
- **AC-14** A counting test of group visits for N filters over G groups stays within the sum of each filter's matched groups plus a per-batch constant.

## Non-goals

- top-agents-usage / assistants-chats raw-ledger query after the ES|QL lookup
- Widgets that duplicate the same per-user read
- The ES candidate aggregation (including projects-spending's pre-floor money)
- Changing `aggregate_by_spend_group`, `dashboard_group_spend`, the summary cards or the user footer
- Any change to figures, flags, endpoints or response shapes

## Risks

- No in-repo test runs a projected (array-bound) statement on a real engine. Projected grouping is covered only by compiled-SQL tests, as today.
- The per-row `aggregate_by_spend_group` duration samples for these tables disappear. Any dashboard counting them will see one sample per batch instead.
- With no dashboard filter, the statement returns every attribution group in the window. The group count on lab is not known, and assignment cost grows with it.
