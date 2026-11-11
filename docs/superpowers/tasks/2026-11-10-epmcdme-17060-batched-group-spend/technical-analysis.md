# Technical Research

**Task**: analytics spend ledger batching
**Generated**: 2026-11-10
**Research path**: filesystem

---

## 1. Original Context

(Verbatim, except DECISIONS item 8 and one remark in item 2, which the user had removed.)

EPMCDME-17060 (ticket created for this issue): slow Analytics "Insights" and "CLI Insights" endpoints for admin-level users with no project filter (lab: 30-600 s per request).

Cause: `read_group_spend` in `src/codemie/service/analytics/handlers/ledger_group_spend.py` loops over the table's candidate rows (every project/user with activity in the window, up to 10,000) and calls `spend_service.aggregate_by_spend_group(period, access=..., report_filter=...)` once per row (asyncio.gather, READ_CONCURRENCY=8). One SQL statement per row against the LiteLLM spend ledger (hourly projection UNION raw edge hours). Lab: ~5k-10k statements per request; widgets compete for the shared pool (PG_POOL_SIZE=10). Concurrency is not the lever (measured: 8, 16 and 32 give the same time).

Call sites, all through read_group_spend, signatures unchanged:
- project_handler.py `_ledger_money` -> GET /v1/analytics/projects-spending (SpendGroup.TOTAL). Pre-floor money comes from its own ES candidate aggregation; only [floor, end) goes to the ledger.
- user_handler.py `_enrich_group_spend` -> GET /v1/analytics/spending/by-users/platform and /cli.
- cli/insights_handler.py `_cli_spend_by_user` (cli-insights-top-spenders, -users, -top-users-by-cost, -user-classification, -by-enriched-user-*) and `_cli_spend_by_project` (cli-insights-top-projects-by-cost, -project-classification), SpendGroup.CLI.

DECISIONS (agreed with the user, fixed):
1. ONE SQL statement for all rows of a read. Not chunks of 100, not one SUM ... FILTER column per row (that cannot be one statement anyway: Postgres caps a SELECT list at 1,664 columns and the driver at 32,767 parameters).
2. Shape: the statement GROUPs BY the attribution columns the per-row predicates key on (e.g. attr_user_id, attr_principal, attr_form, attr_source, attr_project, plus whatever the group split TOTAL/PLATFORM/CLI/EMBEDDING needs), with one shared WHERE: period, access scope, the dashboard filter part common to all rows, and the union of all rows' keys/aliases. It keeps the real read path's hourly-projection + raw-edge-hours shape.
3. Python then assigns the small grouped result to table rows by each row's own predicate. A group matching several rows counts in each of them, exactly as independent per-row reads do today. So figures are identical to today's per-row reads, including flags (unavailable / partial / identity_bridge portions / omitted groups).
4. Already resolved, do not re-raise: (a) one user cannot have two personal projects (business logic), so multi-owner personal projects are not a concern; (b) old principal-only rows recorded under username/email are handled by passing the users' aliases into the query, as today's per-row predicate does; (c) "GROUP BY puts each ledger row in one group" is not a limitation because rows are assigned to groups in Python; (d) a call matching two users through a shared alias counts for both, via (c).
5. The codebase's "no GROUP BY, re-use the per-row predicate" preference (`sum_for_holders` docstring, spend_logs_repository.py ~726) is a maintenance preference, not a correctness issue. Analyse how to keep "which ledger rows belong to whom" defined once, e.g. the Python-side matching mirrors or reuses the existing predicate builders (`report_filter_predicate`, `_person_total_predicate`, `_personal_pot`, `_project_scoped_match_any`, `_fallback_principal_match`), and how to prove batch == per-row in tests.
6. Port scope: a new batched method on the spend ledger port, implemented natively in SpendLogsLedger (src/codemie/enterprise/litellm/spend_ledger.py + repository/spend_logs_repository.py) and CutoverSpendLedger (service/spend/cutover.py); SpendService facade gets the batched method. Adapters without a native batch (LegacySpendLedger, DefaultSpendLedger, _NoopSpendLedger) keep today's per-row behaviour; analyse the cleanest way given the Protocol and the six-way conformance tests (e.g. implement it in each by looping, or a facade fallback). Floor-crossing windows on the user/CLI tables may keep per-row ES reads for the pre-floor half: accepted for this ticket.
7. Out of scope: cause #2 (top-agents-usage, assistants-chats raw-ledger query), widgets duplicating the same per-user read, the ES candidate aggregation.

Open points for you to analyse and report (not decided): failure granularity (today one failed read blanks only its row; one statement fails all rows), how per-row flags/labels (`_report_filter_label`, partial/identity_bridge) are computed per row from a batched result, how restricted (non-admin) access scope and the users/projects dashboard filters fold into the shared WHERE, what in the handler tests stubs the per-row facade method, and per-row timing/slow-read logging that changes.

---

## 2. Codebase Findings

Paths are relative to `src/codemie/` unless prefixed `tests/`. Branch `EPMCDME-17060_batched-group-spend` exists locally.

### Existing Implementations

**Purpose A: one spend-group figure per table row, under the caller's boundary and the dashboard filter.**
- `service/analytics/handlers/ledger_group_spend.py:140` `read_group_spend(period, access, report_filters, group)`. It takes **one `access` for every row**, `Semaphore(READ_CONCURRENCY=8)` (:53), and gives `None` period/filter -> `render_spend(None)` without a read (:154). Each row is `render_spend(groups[group])`, in order. Docstring :146-149: "one bad read costs its row's figure and never the table". Module docstring :17-32 records why no batched port read exists.
- Row filters are built once per table, by `replace(base, ...)`:
  - `user_report_filters` :71. Each row is `replace(base, users=(term,))`, so rows differ **only** in their one `UserTerm` and share `base.projects` and `base.personal_project_owners` (from `report_filter_for(named, projects)`, `handlers/report_filter.py:33`). An empty identifier gives `None`.
  - `project_filters_within` :99. Each row is `replace(base, projects=(name,), personal_project_owners=owners[name])`, so rows differ in their project and owners and share `base.users`. Owners come from `_personal_owners_by_name` :115: `()` for a shared project, a tuple for a personal one, `None` when the lookup failed or no owner is recorded.
- Callers:
  - `project_handler.py:356` (`_ledger_money`, TOTAL, only the `[F,end)` half from `split_at_floor` :310-318; `UNATTRIBUTED_PROJECT_KEY` -> `""` -> `None` filter).
  - `user_handler.py:414` (`_enrich_group_spend`, PLATFORM/CLI).
  - `cli/insights_handler.py:167` (`_cli_spend_by_user`, CLI) and `:176` (`_cli_spend_by_project`, CLI).

**Purpose B: the per-filter group read the loop calls.**
- Facade `service/spend/spend_service.py:309` `aggregate_by_spend_group`. It asks each of `_distinct_ledgers()` (:67) once via `_ask` (:543); the fallback is `no_figure` for every group. It then combines per group with `combine_results` over the ledgers that answered: an omitted group comes from the others, and no answer at all means unavailable.
- `SpendLogsLedger.aggregate_by_spend_group` `enterprise/litellm/spend_ledger.py:380`:
  - `coverage.admitted(period) is None` -> `no_figure` for all groups.
  - Otherwise it opens one session and calls `repository.sum_for_spend_groups`.
  - `unavailable = coverage.unavailable_flag(period)` is a property of the period.
  - `partial = _report_filter_label(report_filter)` (:81) is a pure function of the filter. It returns an identity-bridge portion when any `UserTerm.aliases is None` or `personal_project_owners is None`.
  - Every group is answered and none is omitted.
- `repository/spend_logs_repository.py:881` `sum_for_spend_groups`: `ReadShape(_narrowing(access, rf), _spend_group_aggregates())` gives one result row with 4 columns. `_spend_group_aggregates` :622 is a bare TOTAL plus `spend_group_predicate` FILTERs (`service/spend/spend_group.py`). `_narrowing` :642 is `narrowed_by_access(narrowed_by_filter(true(), rf), access)`.
- Predicate builders, all in `spend_logs_repository.py`:
  - `report_filter_predicate` :466: users terms ORed through `_person_total_predicate` (:375: `attr_user_id = id` OR `_fallback_principal_match(aliases)`, which is :231 `attr_source='parsed' AND attr_form='principal' AND attr_principal IN aliases`; aliases `None` or empty means explicit half only). The projects term is `_project_scoped_match_any` (:310: `attr_form='project_scoped' AND attr_project IN`, empty -> `false()`) OR `_personal_pot(owner)` (:326: `attr_user_id=owner AND attr_form='principal'`). The users and projects terms are ANDed.
  - `access_predicate` :408: `None` when unrestricted. A scope with no projects gives `false()`. Otherwise it is own `attr_user_id` AND (plain projects OR principal form), OR admin projects.
- `CutoverSpendLedger.aggregate_by_spend_group` `service/spend/cutover.py:392` -> `_per_key` (:265). With one leg it delegates untouched. When crossing, it reads both halves and combines per key over the union of keys.
- The other three adapters:
  - `LegacySpendLedger` `legacy_ledger.py:389` delegates to an inner `DefaultSpendLedger`.
  - `DefaultSpendLedger` `default_ledger.py:613` runs one ES `_sum_money_per_bucket` per call and omits groups its call classes cannot carry.
  - `_NoopSpendLedger` `ledger_registry.py:222` returns `no_figure`.

**Purpose C: an existing batch read over many keys (sibling).**
- `consumed_for_holders`: port `service/spend/ledger.py:662`, facade `spend_service.py:120` (each ledger asked once for the set, combined per key), `SpendLogsLedger` `spend_ledger.py:171` (computes `_report_filter_label` once for the batch), cutover `cutover.py:301` via `_per_key`. All six adapters implement it.
- Repository `sum_for_holders` :727 uses one `FILTER` per holder in chunks of `HOLDER_BATCH_SIZE=100` (`ledger.py:139`). Its WHERE is the OR of the chunk's predicates, narrowed once by filter and access.
- Before pricing, it prunes past one chunk with `may_hold_rows` (:394). That is a Python restatement of which columns the person predicates key on, fed by `identities_statement` (`spend_projection.py:235`, distinct `(attr_user_id, attr_principal)` over projection + edges).

**Other touched code**:
- `repository/spend_projection.py`:
  - `ReadShape(where, aggregates)` has no grouping field.
  - `raw_statement` :187 and `_grouped` (~:356) group only by a time bucket (`width`).
  - `_window` builds the `window_rows` UNION ALL of hourly rows, raw edge hours and the resolved-id images. It carries every column in `PROJECTED_DIMENSIONS` (`spend_logs_model.py:245`): `attr_source, attr_form, attr_user_id, attr_principal, attr_project, attr_category, codemie_origin, call_type, codemie_workflow_id, model_group, end_user`.
  - The time-bucket grouping drops buckets left only by cancelled pairs with `HAVING sum(row_count) > 0`.
  - Value lists become `= ANY(:array)` on the projected path, so the 32,767-parameter cap does not bind there.
- `service/spend/rendering.py` `render_spend`.

### Decisions Already in the Code
- **Batched reads use FILTER-per-key, not GROUP BY.** The `sum_for_holders` docstring (`spend_logs_repository.py:737-746`) gives the reason that GROUP BY re-expresses predicates, and cites `_personal_predicate`'s `end_user` exclusion. The task treats this as a maintenance preference (item 5). The predicates this task uses (`_person_total_predicate`, `_personal_pot`, `_project_scoped_match_any`, `spend_group_predicate`) do not reference `end_user`.
- **A Python restatement of predicate keys already exists, held by an agreement test.** `may_hold_rows` :394 has the docstring "must follow them if either ever keys on another; the batch-versus-single agreement test over every match path is what catches a drift". The test is `tests/codemie/service/spend/test_batched_holder_reads.py:222`.
- **Predicate trees are already walked programmatically.** `spend_projection._onto` / `_Translation` uses `replacement_traverse` to re-target the same predicates onto the projection, and fails closed (`_Untranslatable`) on anything it cannot map.
- **The boundary and the filter are conjuncts of the one WHERE, never per FILTER** (:751-755).
- **Every adapter implements every port method.** There is no `hasattr`/optional-method precedent in `service/spend/`. `SpendLedger` is `@runtime_checkable` (`ledger.py:621`) but no `isinstance` check uses it.
- **Keyword-only `access`/`report_filter` on spend-group reads**, enforced for all six by `tests/codemie/service/spend/test_access_boundary.py:94-101,642-650`.
- **Mapping reads answer every requested key; an absent key is a defect** (`ledger.py:685-687`; facade keys on requested values, `spend_service.py:390-396`).
- `ReportFilter` is a frozen dataclass and hashable; existing tests use it as a dict key (`test_ledger_group_spend.py:181`).
- **Projected statement building is CPU-heavy and runs in `asyncio.to_thread`** (`_read` :1346-1370).

### Task Statements Checked Against the Code
- "loops ... once per row, READ_CONCURRENCY=8" - holds (`ledger_group_spend.py:53,151-160`).
- "up to 10,000 rows" - holds (`analytics/query_pipeline.py:63` `BUCKET_SELECTOR_FETCH_SIZE`).
- "one statement per row, hourly projection UNION raw edge hours" - holds for the SpendLogs leg (`_read`, `SPEND_LEDGER_PROJECTION_ENABLED`). Each per-row facade call also asks every other distinct ledger (one ES query per row for `DefaultSpendLedger` where native classes exist).
- "GROUP BY the columns the per-row predicates key on" - holds as feasible on both sources:
  - Row-specific terms key on `attr_user_id, attr_source, attr_form, attr_principal` (user rows) and `attr_form, attr_project, attr_user_id` (project rows).
  - The group split keys on `codemie_origin, attr_category, call_type` (`traffic_class.py:213-276`).
  - All of these are in `PROJECTED_DIMENSIONS` and in `window_rows`.
  - Neither `ReadShape` nor `_grouped`/`raw_statement` supports grouping by dimension columns today.
- "access scope in the shared WHERE" - holds: one `access` per `read_group_spend` call (:143); today it is already a WHERE conjunct (`_narrowing`).
- "dashboard filter part common to all rows" - holds structurally (`replace(base, ...)`, :83 and :110). `read_group_spend` receives only the expanded list, not `base`.
- "flags identical": `unavailable` is per period; `partial` is `_report_filter_label(row_filter)`, pure; omitted groups are combined per group in the facade. All of this is computable per row without a query.
- "five/six implementations and conformance tests" - holds (`test_access_boundary.py:94-101`).
- "facade fallback for adapters without native batch" - no mechanism exists. It keeps `aggregate_by_spend_group` per-row in place for Default/Legacy/Noop.
- Item 5's builders - all hold at the cited lines, plus `access_predicate` :408.

### Consumers of Changed Contracts
Searched for: `read_group_spend`, `aggregate_by_spend_group`, `SpendLedger`, `_IMPLEMENTATIONS`, `sum_for_spend_groups`.
- `read_group_spend`:
  - The 4 call sites above.
  - Tests that patch the per-row facade method: `tests/.../handlers/test_ledger_group_spend.py:181-254` (`ledger_group_spend.spend_service.aggregate_by_spend_group`, keyed per filter), `test_project_handler.py:494,534-550` (same patch, per project name, filter asserted whole), `test_user_handler.py:997-1040,1456-1485` (`user_handler.spend_service.aggregate_by_spend_group`, keyed on `report_filter.users[0]`; the same singleton also serves the footer).
  - `test_cli_handler.py:2384-2414` patches `read_group_spend` itself.
- `aggregate_by_spend_group` callers left per-call: `summary_handler.py:105`, `user_handler.py:430` (`_scope_ledger_total` footer, shares the stub with the per-row reads in `test_user_handler.py`), `ledger_group_spend.py:67` (`dashboard_group_spend`).
- A `SpendLedger` Protocol addition reaches:
  - all six adapters;
  - the protocol fakes `tests/codemie/service/spend/test_spend_service.py:104,678`, `test_cutover.py:168` and `test_legacy_ledger.py:870`;
  - the signature tests `test_access_boundary.py`.
- `sum_for_spend_groups` tests: `test_spend_logs_access_boundary.py` (SQLite figures), `test_spend_logs_repository_projection.py` (compiled SQL), `test_spend_group_aggregation.py`.

### Architecture and Layers Affected
- Analytics handler helper: `ledger_group_spend.py`. Its 3 caller modules keep their signatures.
- Spend facade: `SpendService`.
- Port: `SpendLedger` (`service/spend/ledger.py`).
- Adapters: `SpendLogsLedger` and `CutoverSpendLedger` (native), plus `DefaultSpendLedger`, `LegacySpendLedger` and `_NoopSpendLedger`.
- Repository: `SpendLogsRepository` and the `spend_projection` statement builder.

### Integration Points
- Postgres `codemie.litellm_spend_logs` / `litellm_spend_hourly`, through the ledger's `session_factory`.
- ES metrics index, through the Default and Legacy adapters.
- `application_repository.aget_personal_project_owners_by_name` and the user lookup in `report_filter_for`: one each per table, already batched.

### Patterns and Conventions
- Batch contract wording, "every figure equals the single read" (`ledger.py:679-687`).
- Cutover `_per_key` union-merge for mapping reads.
- Facade `_ask` with a lazy full-key `no_figure` fallback.
- Long "why" docstrings with a bold lead on every port and repository method.
- The repository never decides floors.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/` exists, but no guide covers the spend ledger. `development/performance-patterns.md` and `data/database-optimization.md` only say "batch per-item calls" in general terms.

### Architectural Decisions
Recorded in docstrings, not ADR files:
- batch equals single (`ledger.py:679-687`);
- FILTER over GROUP BY (`spend_logs_repository.py:737`);
- boundary vs filter (`ledger.py:261-286`, `report_filter_predicate` docstring);
- projection exactness (`spend_projection.py:15-60`: "pinned by the deterministic database oracle", which is not in this repo);
- the cutover two-clock design (`cutover.py:44-83`).

### Derived Conventions
- Port methods are implemented by all adapters, and test fakes are updated alongside.
- Per-key mapping results.
- Keyword-only `access`/`report_filter`.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/analytics/handlers/test_ledger_group_spend.py`: filter builders; `read_group_spend` order, `None` filter/period, missing group.
- `tests/codemie/service/spend/test_spend_group_aggregation.py`: `SpendLogsLedger.aggregate_by_spend_group`, compiled `report_filter_predicate`/`spend_group_predicate` SQL.
- `tests/codemie/service/spend/test_batched_holder_reads.py`: a batch-vs-single agreement template (`_CapturingSession`) and the `may_hold_rows` agreement test (:222).
- `tests/codemie/repository/test_spend_logs_access_boundary.py`: real statements over real rows on SQLite (`create_engine("sqlite://")`, fixtures :114, :212, :340, :367). It asserts money for boundary and filter cases, including a personal project as its owner's pot (:265). Raw statements only; projected statements use arrays, which SQLite lacks.
- `tests/codemie/repository/test_spend_logs_repository_projection.py`, `test_spend_projection.py`: projected vs raw routing, as compiled SQL.
- Per adapter: `test_spend_service.py`, `test_cutover.py`, `test_legacy_ledger.py`, `test_default_ledger.py:1117-1230`, `test_ledger_registry.py:205-213`, `test_access_boundary.py`.

### Testing Framework and Patterns
- pytest 8.4.2, async tests, `patch`/`AsyncMock`.
- Hand-written protocol fakes.
- Compiled-SQL assertions (`postgresql.dialect()`, `literal_binds`).
- SQLite sync session wrapped as the repository's `AsyncSession` surface.

### Coverage Gaps
- No batch-vs-per-row test for spend-group figures, since no such batch exists.
- No test of one failing row among many in `read_group_spend`.
- No in-repo test runs a projected (array-binding) statement on a real engine.

---

## 5. Configuration and Environment

### Environment Variables
All in `configs/config.py`:
- `PG_POOL_SIZE` (10, :94) and `SPEND_LEDGER_POOL_SIZE` (0, :787: shares the main pool).
- `SPEND_LEDGER_STATEMENT_TIMEOUT_MS` and `SPEND_LEDGER_POOL_TIMEOUT_SECONDS`, which apply to the dedicated pool only.
- `SPEND_LEDGER_PROJECTION_ENABLED` (True, :779).
- `SPEND_LEDGER_SLOW_READ_SECONDS` (5.0, :794).
- `SPEND_LEDGER_COVERAGE_FLOOR` (:747) and `SPEND_LEDGER_LEGACY_BELOW_FLOOR` (:773).

### Configuration Files
`src/codemie/configs/config.py` only.

### Feature Flags and Deployment Concerns
- `SPEND_LEDGER_LEGACY_BELOW_FLOOR` decides whether cutover wrappers are registered (`composition.py:201-302`).
- `routing.uses_litellm` decides whether a `DefaultSpendLedger` is registered for native classes (`composition.py:120-134`), which sets how many distinct ledgers each read fans out to.

---

## 6. Risk Indicators

- **Failure granularity.** Today `_ask` (`spend_service.py:543`) catches per facade call, so one failed row read gives that row `no_figure`, matching the `read_group_spend` docstring (:146-149). The batched statement goes through one `_ask`, whose fallback covers every row the batch answers. Speculative: the docstring promise and a per-row-failure test change with it.
- **Flags per row** need no query: `unavailable_flag(period)` is per period, and `_report_filter_label(row_filter)` is pure and per filter. Speculative: the native batch computes the label per row's filter, not once per batch as `consumed_for_holders` (`spend_ledger.py:212`) does for its single shared filter.
- **Common filter part is not passed to `read_group_spend`.** User rows share `base.projects`/`personal_project_owners`; project rows share `base.users`. Speculative: the shared WHERE needs `base` passed alongside the rows, or derived from them.
- **No grouping by dimension columns** in `ReadShape`, `raw_statement` or `_grouped`. Grouping over `window_rows` must keep the cancelled-pair handling: today `HAVING sum(row_count) > 0` for buckets, and sums that net to zero for resolved images. Speculative: the group split can stay as `_spend_group_aggregates` FILTER columns per group key, which keeps `spend_group_predicate` the single definition. Otherwise `codemie_origin, attr_category, call_type` become keys too.
- **"Defined once" for the Python matching:**
  - Row-specific terms use only `==`, `IN` (`= ANY` when projected), `AND`, `OR` and `false()` over non-negated atoms. Access and the common filter part stay in SQL.
  - The precedents are `may_hold_rows` (restatement plus agreement test) and `_onto`/`_Translation` (traversal of the same predicate objects).
  - A NULL key fails these positive atoms in SQL (unknown) and in Python (`None` not in set) alike.
- **Port addition reaches six adapters**, three protocol fakes and `test_access_boundary.py`, and there is no optional-method precedent. Default, Legacy and Noop keep one call per row; in deployments with native classes that is still one ES query per row (Default).
- **Cutover crossing**: `_per_key` merges `Mapping[key, SpendResult]`. A batch result per row per group needs a key per row (`ReportFilter` is hashable; `user_report_filters` can return the same filter twice for a repeated identifier, and `None` entries).
- **Handler test stubs** patch `spend_service.aggregate_by_spend_group` per filter (Consumers section) and stop intercepting a different facade method. `test_user_handler.py`'s stub also serves the `_scope_ledger_total` footer, which stays per-call.
- **Timing and logs**:
  - `_ask` records `SPEND_LEDGER_READ_DURATION_METRIC` (read=`aggregate_by_spend_group`) and the slow-read WARNING (≥5 s) once per row per ledger. A batch emits one sample under a new read name.
  - `logger.info` row counts at `project_handler.py:355` and `user_handler.py:415` are unaffected.
- **Statement size**: a 10,000-row union of user ids plus aliases, or projects plus owners, goes into WHERE. Projected statements bind arrays; raw statements (projection off, or a window with no whole hour) use expanding `IN`, one parameter per value.

---

## 7. Summary for Complexity Assessment

The change runs through the spend port stack: the handler helper `read_group_spend` (one module; four callers keep their signatures), the `SpendService` facade, the `SpendLedger` Protocol, `SpendLogsLedger` and `SpendLogsRepository`, the `spend_projection` statement builder, and `CutoverSpendLedger`. Default, Legacy and Noop keep their per-row behaviour, but the Protocol touches all six, plus three protocol fakes and the six-way conformance tests. A close sibling exists: `consumed_for_holders`/`sum_for_holders`, a batch with facade per-key combining, cutover `_per_key` and batch-equals-single tests. It uses FILTER per key, though, and the repository has no GROUP BY over attribution columns today. Every column needed is in `PROJECTED_DIMENSIONS`, so it can be expressed on both the raw and the projected source.

The new part is a grouped `ReadShape` in both renderers, including the projected window's cancelled-image handling, and Python-side row matching that must agree with `report_filter_predicate`'s row terms. Precedents for keeping that single-sourced are `may_hold_rows` with its agreement test and `_onto`'s predicate traversal. Flags carry over without queries: `unavailable` is per period, and `_report_filter_label` is a pure per-filter function.

Test coverage is broad per adapter, with compiled-SQL tests, an agreement-test template and a SQLite real-rows figure suite. There is no batch-vs-per-row spend-group test, no failing-row test, and no in-repo real-engine run of projected statements. Key risks are batch-wide failure replacing per-row failure, the shared filter part being absent from `read_group_spend`'s inputs, handler stubs on the per-row facade method, and per-row metrics changing to one batched sample.

---

## 8. External References

- `C:\Users\KostiantynPshenychny\Documents\epm-cdme\.tmp\perf-investigation\investigation.md` (resolved) records:
  - Lab per-request statement counts (7 days): projects-spending 8,388 (333 shared + 8,055 personal), by-users/platform 5,244, by-users/cli 4,623, CLI per-user widgets ~4,886 each, per-project ~4,955.
  - "Personal activity (no project) is grouped under the user's email, so most rows are personal projects."
  - "only 1-3 run at once on average (shared pool `PG_POOL_SIZE=10`, `SPEND_LEDGER_POOL_SIZE=0`; statement building runs in `asyncio.to_thread`)".
  - History: MR !4701 (`9c288b290`) used batched holders with wrong figures; MRs !4742/!4744 (`e0ce2543c`, `773af9407`) chose per-row reads, giving the reason "A batched port read would remove this and touches every ledger adapter".
  - Attribution: explicit metadata gives `attr_form` `project_scoped` or `principal`, and `end_user` grammar is parsed otherwise. "A call is booked to a shared project only if that project has a budget for the category, else it falls back to personal."
  - Decided fix: as in Section 1.

## Facts Added by Review

Researched for the names the spec relies on, after the analysis above was written. Read like the facts layer; where a line here contradicts one above, this one was read later.

- Defined at `spend/rendering.py:96`. It is a frozen, slotted dataclass with these fields: `figure: float | None`, `unavailable: bool`, `partial: bool`, `missing_amount: float | None`, and `missing_portions: tuple[MissingPortion, ...] = ()`. `MissingPortion` is `tuple[SpendDimension, Portion]` (`:86`). It also has `is_exact` (a figure exists, and it is neither unavailable nor partial) and `with_figure()`.
- On this path every instance comes from `render_spend(result)` (`rendering.py:~124`), called inside `read_group_spend`'s inner `read()` (`analytics/handlers/ledger_group_spend.py:153-158`):
- `read_group_spend` returns `list[RenderedSpend]` in input order (`ledger_group_spend.py:140-160`).
- `ordered_spend_rows` (`analytics/query_pipeline.py:862`) and `enriched_spend_rows` (`query_pipeline.py:887`) are the finishers. `enriched_spend_rows` drops only an *exact* zero.
- Wider consumers: 21 `src` files import it, including `conversation_service`, `workflow_service`, `budget/cost_center_budget_summary`, `leaderboard/collector`, `platform_tool`, and most analytics handlers. Tests build it directly in `tests/unit/service/analytics/handlers/test_cli_handler_enriched_user.py:60-67`.
- Defined at `analytics/handlers/project_handler.py:327`. What it does:
- Calls `project_filters_within(report_filter or ReportFilter(), names)` (`ledger_group_spend.py:99`). For each named project this keeps the dashboard's users term, replaces the projects term with `(name,)`, and adds `personal_project_owners`. The owners come from one `application_repository.aget_personal_project_owners_by_name` lookup. If that lookup fails, every project gets `None` owners, which the ledger labels.
- Defined at `analytics/handlers/user_handler.py:383`. What it does:
- `user_report_filters([row[identifier_key] ...], projects)` (`ledger_group_spend.py:71`): one person per filter, keeping the dashboard's projects term. An empty identifier gets `None`.
- `spend_service.consumed_for_holders` (`spend/spend_service.py:120`) is a batched holder read. `user_handler._enrich_users_spending` (`~:365-381`) uses it with `PersonTotalHolder`. According to the `ledger_group_spend.py:20-22` docstring, `PersonTotalHolder` "has no category split".
- `spend_service.aggregate_by_dimension` (`spend_service.py:342`) is a grouped read whose residue is shared across values (rendering docstring). The project tests forbid calling it on the projects table (see Consumers).
- `spend_service.aggregate_by_spend_group` (`spend_service.py:309`) has the signature `(period, *, access, report_filter=None) -> dict[SpendGroup, SpendResult]` and asks each distinct ledger once. It is implemented in `spend/ledger.py:798`, `default_ledger.py:613`, `legacy_ledger.py:389`, `cutover.py:392`, `ledger_registry.py:222` and `enterprise/litellm/spend_ledger.py:380`.
- One port call per filter, recorded at `ledger_group_spend.py:17-24`. The recorded reason is that the port has no batched per-person or per-project group read, and the alternatives (`PersonalHolder`, `PersonTotalHolder`, composed reads) each give the wrong figure or have no access boundary.
- `READ_CONCURRENCY = 8` (`ledger_group_spend.py:53`) is a semaphore, so each scan is on its own session.
- A row with no identity gets no figure and never an unfiltered read (`ledger_group_spend.py:31-32`, `:154`).
- The footer check uses one report filter for both the rows and the total (`user_handler.py:100-103`).
- "`read_group_spend` runs one spend-ledger statement per table row": **holds**. There is one `aggregate_by_spend_group` call per non-`None` filter (`ledger_group_spend.py:157`). Each call asks every distinct ledger once (`spend_service.py:322-323`).
- "projects-spending, by-users/platform and /cli, CLI Insights per-user and per-project": **holds**. Its 4 call sites are `project_handler.py:356`, `user_handler.py:414` and `insights_handler.py:167` and `:176`. `_cli_spend_by_user` is used by `get_cli_insights_user_classification` (`:290`), `get_cli_insights_top_users_by_cost` (`:354`), `_get_cli_insights_user_rows` (`:899`) and `_aggregate_user_rows_by_enrichment_field` (`:1957`). `_cli_spend_by_project` is used by `_get_cli_insights_project_rows` (`:969`).
- `tests/codemie/service/analytics/handlers/test_project_handler.py:478-500` patches `ledger_group_spend.spend_service.aggregate_by_spend_group` and makes `aggregate_by_dimension` raise. `_answers_over` (`:539+`) asserts the period is the above-floor leg, the access is the expected scope, and the filter as a whole for each call.
- `tests/codemie/service/analytics/handlers/test_user_handler.py:1458-1490` uses a stub that answers by `report_filter.users[0].user_id` when the filter names one person, and the dashboard otherwise. It patches `user_handler.spend_service.aggregate_by_spend_group`.
- `tests/codemie/service/analytics/handlers/test_ledger_group_spend.py:186-219` calls `read_group_spend` directly (order, no-figure, missing group).
- `tests/codemie/service/analytics/handlers/test_cli_handler.py:2380-2414` patches `insights_handler.read_group_spend` itself.
