# Batched spend-group read Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `read_group_spend` answers all its rows with one SpendLogs statement grouped by attribution columns, with every row's `RenderedSpend` unchanged.

**Architecture:** `ReadShape` gains grouping. A set-wise evaluator in `spend_projection.py` assigns grouped rows to each filter by evaluating that filter's own `report_filter_predicate` tree. A new port method `aggregate_by_spend_group_per_filter` is native in SpendLogs and Cutover, loops in Default, delegates in Legacy, and returns no figure in Noop. The facade asks each ledger once.

**Tech Stack:** Python 3.12, SQLAlchemy Core, pytest (async), SQLite fixtures.

**Spec:** `docs/superpowers/tasks/2026-11-10-epmcdme-17060-batched-group-spend/spec.md`. Read it with this plan. Paths below are relative to `src/codemie/` unless they start with `tests/`.

## Global Constraints

- Commit after each task, following the repository's existing commit convention.
- Reuse what exists; do not write parallel copies. That means `report_filter_predicate`, `_narrowing`, `_spend_group_aggregates`, `_id_free`, `_read`, `_is_value_list`, `_ask`, `_per_key`, `combine_results`, `_report_filter_label`, and the test helpers `_SyncSessionAsAsync`, `_row`, `_personal_row`, `_StubRepository`/`_StubSession` and `_SqlSession`.
- Name new code after its neighbours (see each task's Interfaces block). Follow `.ai-run/guides/standards/code-quality.md` and `testing/testing-patterns.md`.
- No comments by default. Docstrings follow the house "why" style, are brief, and never mention the ticket or the change.
- The read name is the literal `"aggregate_by_spend_group_per_filter"` everywhere: `_ask`, metrics and logs.
- Keyword-only `access`/`common_filter`/`report_filters`. `access` defaults to `None` on the port and adapters, and has no default on `SpendService`.

## Review Focus

- The same identifier on two rows: one filter is read once, and both rows show the same figure (Task 8).
- Rows with a NULL attribution column match no atom, the same as in SQL (Task 2).
- A personal project whose owners are `()`, `None` or several owners matches the rows SQL matches (Task 2, AC-12).
- A window the coverage does not admit gives every filter `no_figure`, and the database is never touched (Task 5).
- A window with no whole hour takes the raw grouped path and still matches per-filter reads (Tasks 1 and 3, on SQLite).

---

### Task 1: Grouping on `ReadShape`

**Files:** Modify `repository/spend_projection.py:136-141` (ReadShape), `:187-194` (`raw_statement`), `:275-281` (`_projected`), `:341-353` (`_grouped`). Test `tests/codemie/repository/test_spend_projection.py`.

**Interfaces:** Produces `ReadShape.group_by: tuple[str, ...] = ()`, where each entry names a `PROJECTED_DIMENSIONS` column. Grouped statements select the key columns first, labelled by their names, then the aggregates.

- [ ] **Test-first: yes** — `test_a_grouped_raw_read_groups_by_the_named_columns` and `test_a_grouped_projected_read_groups_window_rows_and_drops_netted_groups` fail. The raw one expects compiled SQL with `GROUP BY litellm_spend_logs.attr_user_id, …`. The projected one expects `GROUP BY window_rows.attr_user_id …` and `HAVING sum(window_rows.row_count) > 0`. Also add a check that a shape with `group_by=()` compiles exactly as it does today.
- [ ] Implement it. `raw_statement` adds the key columns to the select and the GROUP BY, after the bucket when `width` is given. `_projected` passes `unresolved.group_by` into `_grouped`. `_grouped` groups by the bucket (if any) plus `window.c[name]`, and keeps the existing `HAVING` whenever it groups at all. Order by the bucket only.
- [ ] Run `poetry run pytest tests/codemie/repository/test_spend_projection.py -q`. Expect a pass.

### Task 2: Set-wise predicate evaluator

**Files:** Modify `repository/spend_projection.py` (place the new code beside `_onto`/`_is_value_list`). Tests: create `tests/codemie/repository/test_grouped_rows.py`, and modify `tests/codemie/repository/test_spend_logs_access_boundary.py`.

**Interfaces (Produces):**
```python
class UnmatchablePredicate(ValueError): ...
def grouping_columns(predicates: Iterable[ColumnElement[bool] | None]) -> tuple[str, ...]: ...
class GroupedRows:
    visits: int
    def __init__(self, rows: Sequence[Sequence[object]], key: tuple[str, ...]) -> None: ...
    def matching(self, predicate: ColumnElement[bool] | None) -> frozenset[int]: ...
```
- `grouping_columns` makes one pass over every tree. It returns the ledger columns those trees name, in `PROJECTED_DIMENSIONS` order. It raises `UnmatchablePredicate` for any column outside `PROJECTED_DIMENSIONS`.
- `GroupedRows` indexes each key column (`value -> frozenset[row index]`), with NULL values left out. Supported nodes: `==` against a bind; `IN` as `_is_value_list` recognises it (but not `NOT IN`); AND/OR `BooleanClauseList`; `Grouping` as a pass-through; `True_` and `False_`. `None` means every row. Any other node, or a column outside `key`, raises `UnmatchablePredicate`.
- Results are memoised per instance on a structural key, for example `("in", column, frozenset(values))`, so a term shared by many filters is computed once. An AND intersects its children's sets smallest-first. An OR takes their union.
- `visits` counts the row indexes touched: the size of each index lookup, the size of the smaller operand of each intersection, and the operand sizes of each union. A memo hit adds 0.

- [ ] **Test-first: yes** — the following fail:
  - In `test_grouped_rows.py`:
    - AC-4: `NOT`/`not_in`, `IS NULL` and a column outside `key` each raise `UnmatchablePredicate`, and so does a column outside `PROJECTED_DIMENSIONS` in `grouping_columns`.
    - A NULL key value matches no `==`/`IN`.
    - AC-14: with 2,000 groups and 2,000 single-user filters that share one projects term, `visits <= 3 * sum(len(m) for m in matches) + 4 * G`.
  - In `test_spend_logs_access_boundary.py`, add `TestTheEvaluatorSelectsWhatTheFilterSelects` (AC-12). Each ledger row is its own group, keyed by `request_id` plus the five attribution columns. Assert that `GroupedRows.matching(report_filter_predicate(f))` gives the same request ids as `select(Row.request_id).where(report_filter_predicate(f))`. Cover users only, projects only, both, `aliases=None`, and owners `None`, `()` and several. Use `_row`/`_personal_row`, plus `parsed`/`principal` rows built from `_row`.
- [ ] Implement it.
- [ ] Run `poetry run pytest tests/codemie/repository/test_grouped_rows.py tests/codemie/repository/test_spend_logs_access_boundary.py -q`. Expect a pass.

### Task 3: Repository grouped spend-group read

**Files:** Modify `repository/spend_logs_repository.py`, near `sum_for_spend_groups` (`:881-911`). Tests: `tests/codemie/repository/test_spend_logs_access_boundary.py` and `tests/codemie/service/spend/test_spend_group_aggregation.py`.

**Interfaces (Produces):**
```python
@dataclass(frozen=True)
class SpendGroupBatch:
    report_filters: tuple[ReportFilter, ...]
    predicates: tuple[ColumnElement[bool] | None, ...]
    group_key: tuple[str, ...]
    @classmethod
    def of(cls, report_filters: Collection[ReportFilter]) -> SpendGroupBatch: ...
    def figures(self, rows: Sequence[Sequence[object]]) -> dict[ReportFilter, dict[SpendGroup, Decimal]]: ...

async def sum_for_spend_groups_per_attribution(
    self, session, period, group_key: tuple[str, ...], access=None, common_filter=None
) -> list[Sequence[object]]: ...
```
- `of` builds `report_filter_predicate(f)` for each filter and sets `group_key = grouping_columns(predicates)`. It is pure, so it can run on a worker thread.
- `figures` builds a `GroupedRows(rows, group_key)`. Each filter's figure for a group is the `Decimal` sum of that group's column over the matched rows, and a filter that matches nothing gets `Decimal("0")`.
- The method reads `ReadShape(_narrowing(access, common_filter), _spend_group_aggregates(), group_by=group_key)` through `self._read(session, period, _id_free(shape))` and returns `.all()`. Nothing from the rows enters the WHERE. Its docstring, and the docstring of `sum_for_spend_groups`, name `TestTheBatchedSpendGroupReadEqualsTheSingleRead` as the test that keeps the two equal.

- [ ] **Test-first: yes** — the following fail:
  - AC-1 money, `TestTheBatchedSpendGroupReadEqualsTheSingleRead` in `test_spend_logs_access_boundary.py`. For each filter, `batch.figures(rows)[f]` equals `sum_for_spend_groups(session, PERIOD, access, f)`. Cover every AC-1 match path: explicit id, alias fallback, `aliases=None`, an alias shared by two users, a shared project, a personal pot, owners `None`, user rows sharing a projects term, and project rows sharing a users term. Run each with an empty and a non-empty common filter, under `ALICE_SCOPE` and under `AccessScope.everything()`.
  - AC-2 and AC-13 in `test_spend_group_aggregation.py`, extending `_SqlSession` with `.all()`:
    - One `execute` is made for N filters.
    - With `projection=True` and a whole-hour period, the SQL reads `window_rows`, has the group key and the `group_N` FILTER aggregates, and contains the access and common-filter conjuncts.
    - The literal-bound SQL for 1 filter equals the SQL for 10,000 filters of the same shape.
- [ ] Implement it.
- [ ] Run `poetry run pytest tests/codemie/repository/test_spend_logs_access_boundary.py tests/codemie/service/spend/test_spend_group_aggregation.py -q`. Expect a pass.

### Task 4: Port method; shared failure recording; Default, Legacy and Noop

**Files:**
- Modify `service/spend/ledger.py` (add the method after `:798-821`).
- Create `service/spend/read_failures.py`.
- Modify `service/spend/spend_service.py:590-601` (`_ask` calls the new function).
- Modify `default_ledger.py` (after `:613`), `legacy_ledger.py` (after `:389`), and `ledger_registry.py` (after `:222`).
- Tests: `test_default_ledger.py`, `test_legacy_ledger.py` (including the fake at `:870`) and `test_ledger_registry.py`, all under `tests/codemie/service/spend/`.

**Interfaces (Produces):**
```python
async def aggregate_by_spend_group_per_filter(
    self, period: TimeRange, *, access: AccessScope | None = None,
    common_filter: ReportFilter | None, report_filters: Collection[ReportFilter],
) -> Mapping[ReportFilter, Mapping[SpendGroup, SpendResult]]: ...
def record_read_failure(ledger_name: str, read: str, message: str, error: BaseException, count: int = 1) -> None: ...
```
- `record_read_failure` takes over `_ask`'s ERROR log (with `exc_info=error`) and its `SPEND_LEDGER_READ_FAILURES_METRIC` count (`send_count_metric(..., count=count)`). `_ask`'s observable behaviour does not change.
- The port docstring states that the method answers every requested filter, that each answer equals `aggregate_by_spend_group` for that filter, and that an absent key is a defect.
- Default:
  - `READ_CONCURRENCY = 8` moves here, as a semaphore over its own `aggregate_by_spend_group` per filter.
  - A filter that raises gets `no_figure` for every group.
  - If one or more filters fail, it calls `record_read_failure` once, with the failed count and the first error.
  - If every filter fails, it re-raises the first error.
- Legacy delegates to its inner Default. Noop returns `no_figure` for every filter and every group.

- [ ] **Test-first: yes** — AC-9 fails:
  - Default answers each filter exactly as its single read does.
  - If one of three filters raises, only that filter gets `no_figure`, the failure metric is sent once with `count=1`, and one ERROR line is logged.
  - If every filter raises, the call raises.
  - Legacy passes its arguments through to Default unchanged.
  - Noop answers `no_figure` everywhere.
  - The existing `_ask` failure tests still pass.
- [ ] Implement it.
- [ ] Run `poetry run pytest tests/codemie/service/spend/test_default_ledger.py tests/codemie/service/spend/test_legacy_ledger.py tests/codemie/service/spend/test_ledger_registry.py tests/codemie/service/spend/test_spend_service.py -q`. Expect a pass.

### Task 5: SpendLogsLedger native batch

**Files:** Modify `enterprise/litellm/spend_ledger.py` (after `:380-420`). Test `tests/codemie/service/spend/test_spend_group_aggregation.py`: give `_StubRepository` the grouped method.

**Interfaces:** Consumes `SpendGroupBatch` and `sum_for_spend_groups_per_attribution` from Task 3.

The method works in this order:
1. If `coverage.admitted(period) is None`, return `no_figure` for every filter.
2. If there are no filters, return `{}`.
3. Build `batch = await asyncio.to_thread(SpendGroupBatch.of, filters)`.
4. Run the grouped read inside `async with self._session_factory()`.
5. After the session block, run `await asyncio.to_thread(batch.figures, rows)`.
6. For each filter, build `SpendResult(figure, unavailable=coverage.unavailable_flag(period), partial=_report_filter_label(f))`.

The docstring names the same agreement test as Task 3.

- [ ] **Test-first: yes** — the following fail:
  - AC-1 flags: for filters with `aliases=None`, owners `None` and fully resolved terms, each batched result equals `aggregate_by_spend_group(period, access=..., report_filter=f)` on the same stub totals, including under a crossing (`unavailable`) coverage.
  - With the coverage unready, every filter gets `no_figure` and the repository makes zero reads.
  - N filters make one repository read.
- [ ] Implement it.
- [ ] Run `poetry run pytest tests/codemie/service/spend/test_spend_group_aggregation.py -q`. Expect a pass.

### Task 6: CutoverSpendLedger and the six-way signature

**Files:**
- Modify `service/spend/cutover.py` (after `:392-409`).
- Tests: `tests/codemie/service/spend/test_cutover.py` (the fake at `:168`) and `tests/codemie/service/spend/test_access_boundary.py` (beside `:638-646`).

Behaviour:
- With one leg (`len(self._legs(period)) == 1`), return that ledger's answer as it is.
- When the window crosses, use `_per_key` unchanged, with an async `ask` that flattens each half to `(filter, group)` keys. Then re-nest the result per filter. No second merge helper.

- [ ] **Test-first: yes** — the following fail:
  - AC-8: for each filter, a crossing batch equals the crossing `aggregate_by_spend_group` for that filter, including a group the legacy half omitted.
  - A single leg returns the inner answer object unchanged (`is`).
  - AC-10: `test_the_batched_spend_group_read_takes_its_arguments_by_keyword_only` passes over `_IMPLEMENTATIONS` for `access`, `common_filter` and `report_filters`.
- [ ] Implement it.
- [ ] Run `poetry run pytest tests/codemie/service/spend/test_cutover.py tests/codemie/service/spend/test_access_boundary.py -q`. Expect a pass.

### Task 7: SpendService facade

**Files:** Modify `service/spend/spend_service.py:309-340`. Test `tests/codemie/service/spend/test_spend_service.py` (fakes at `:104` and `:678`, and the parametrize list at `:398` and `:405-411`).

**Interfaces:** Produces `SpendService.aggregate_by_spend_group_per_filter(period, *, access: AccessScope | None, common_filter: ReportFilter | None, report_filters: Collection[ReportFilter]) -> dict[ReportFilter, dict[SpendGroup, SpendResult]]`.

- Deduplicate with `dict.fromkeys`. If nothing remains, return `{}` and ask no ledger.
- Ask each of `_distinct_ledgers()` once through `_ask(..., "aggregate_by_spend_group_per_filter", ...)`, with a lazy fallback of `no_figure` for every filter and group.
- Extract `_combined_groups(per_ledger, period) -> dict[SpendGroup, SpendResult]` from `aggregate_by_spend_group`'s return expression, and use it there unchanged and here per filter.

- [ ] **Test-first: yes** — the following fail:
  - AC-7: each ledger is asked once. Results are combined per filter and group, including a group omitted by one ledger and a ledger that answered nothing for a filter.
  - AC-6: a ledger that raises gives every filter `no_figure`, and the call returns.
  - AC-10: the method is added to the no-default-boundary parametrize list.
- [ ] Implement it.
- [ ] Run `poetry run pytest tests/codemie/service/spend/test_spend_service.py -q`. Expect a pass.

### Task 8: `read_group_spend` and its call sites

**Files:**
- Modify `service/analytics/handlers/ledger_group_spend.py` (whole module: docstring `:15-33`, `:51-53`, `:71-112`, `:140-160`), `project_handler.py:353-356`, `user_handler.py:408-414` and `cli/insights_handler.py:166-177`.
- Modify `configs/config.py:790-791` (comment only).
- Tests: `test_ledger_group_spend.py`, `test_project_handler.py`, `test_user_handler.py` and `test_cli_handler.py`, under `tests/codemie/service/analytics/handlers/`.

**Interfaces (Produces):**
```python
class TableFilters(NamedTuple):
    common: ReportFilter
    rows: list[ReportFilter | None]
async def read_group_spend(period, access, common_filter: ReportFilter, report_filters: Sequence[ReportFilter | None], group) -> list[RenderedSpend]: ...
```
- `user_report_filters` returns `common=replace(base, users=())`.
- `project_report_filters` and `project_filters_within` return `common=replace(base, projects=(), personal_project_owners=())`.
- When no row is named, return `TableFilters(ReportFilter(), [None, ...])`.
- `read_group_spend`:
  - If `period is None`, or after removing `None` filters, no filter remains, it renders `render_spend(None)` and makes no call.
  - Otherwise it checks every filter with `_carries_common_term(report_filter, common_filter) -> bool`, meaning each non-empty term is equal (users; projects together with owners). A filter that fails the check raises `ValueError` before any read.
  - It then makes one facade call with the deduplicated filters and maps the results back in row order. The semaphore goes.
- Docstrings say that a failed batch costs every row its figure, and that a `SPEND_LEDGER_STATEMENT_TIMEOUT_MS` timeout on the dedicated pool is one such cause. The config comment says the timeout is sized for one statement per table.

- [ ] **Test-first: yes** — the following fail:
  - AC-5: one facade call is made with the common filter and the distinct filters, and row order is kept. Repeated filters give equal figures. `None` rows and a `None` period make no call. A filter whose projects term differs from the common one raises.
  - AC-11: the handler tests stub `aggregate_by_spend_group_per_filter`, or the new `read_group_spend` signature in `test_cli_handler.py`, and assert that the four call sites pass the common filter. The `_scope_ledger_total` footer still goes through `aggregate_by_spend_group`, and the existing figure assertions are unchanged.
- [ ] Implement it. The four handler method signatures stay the same.
- [ ] Run `poetry run pytest tests/codemie/service/analytics/handlers/test_ledger_group_spend.py tests/codemie/service/analytics/handlers/test_project_handler.py tests/codemie/service/analytics/handlers/test_user_handler.py tests/codemie/service/analytics/handlers/test_cli_handler.py -q`. Expect a pass.

---

## Negative constraints

- The statement carries no row keys, aliases, projects or owners: Task 3 (AC-13).
- No chunking and no FILTER per row; there is one statement: Task 3.
- No Python restatement of the person rule; the builders' own trees are evaluated: Task 2.
- The group key does not include `codemie_origin`, `attr_category` or `call_type`; the split stays in `_spend_group_aggregates`: Task 3.
- NOT is unsupported: Task 2.
- Assignment does not run on the event loop: Task 5.
- A failed batch does not fall back to per-row reads: Tasks 5 and 7.
- Cutover gets no second merge helper: Task 6.
- No optional method and no facade fallback; every adapter implements the method: Tasks 4–6.
- Default gets no native ES batch, and the pre-floor ES half is not batched: Task 4.
- The facade `access` has no default: Task 7.
- No semaphore stays in `read_group_spend`: Task 8.
- Behaviour does not change for `aggregate_by_spend_group`, `dashboard_group_spend`, the summary cards, the user footer, figures, flags, endpoints or shapes. Task 7 only extracts an identical combine helper; Task 8 keeps the footer as it is.
