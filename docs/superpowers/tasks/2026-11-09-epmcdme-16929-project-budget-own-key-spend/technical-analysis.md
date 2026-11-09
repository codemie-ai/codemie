# Technical Research

**Task**: budget spend project spend-logs litellm
**Generated**: 2026-11-09
**Research path**: codegraph

---

## 1. Original Context

Review https://jiraeu.epam.com/browse/EPMCDME-16929 and implement the fix. Helpful information and initial investigation details are available at docs/superpowers/plans/2026-11-09-epmcdme-16929-project-budget-own-key-spend.md (read it; it holds the problem, root cause and affected surfaces). Summary: a user with a global/personal LiteLLM integration chatting in a project with a budget is billed to their own key (no customer; end_user='N/A'), yet CodeMie counts that spend against the project budget because the ProjectHolder / ProjectMemberHolder / ProjectGroupHolder predicates in src/codemie/repository/spend_logs_repository.py ignore end_user. Fix so project budget surfaces (budget_usage, project list budget summaries, project member spend, enforcement-off project limits) only count rows charged to a project member customer. Usage analytics are out of scope.

---

## 2. Codebase Findings

### Existing Implementations

Purpose A: "Compute how much a project budget (project / project-member / project-group) has consumed from the spend ledger."
- `src/codemie/repository/spend_logs_repository.py:252` `predicate_for_holder` - the single place a holder's rows are defined. Project cases at :263-279:
  - `ProjectHolder`: `_project_scoped_match(project)` AND `attr_category`. No `end_user` term.
  - `ProjectMemberHolder`: the above AND `attr_user_id`. No `end_user` term.
  - `ProjectGroupHolder`: `attr_form='project_scoped'` AND `attr_project IN (...)` AND `attr_category` (written inline, does not use `_project_scoped_match`). No `end_user` term.
  - Docstring (:253-259) describes "two pots" (personal vs project) kept apart by the predicates.
- `spend_logs_repository.py:285` `_project_scoped_match` (`attr_form='project_scoped' AND attr_project=:p`) and `:299` `_project_scoped_match_any`. Shared with non-budget readers (see Consumers).
- `spend_logs_repository.py:330` `_personal_predicate` - the sibling that already tests the charged customer (EPMCDME-15204): explicit half has `~end_user.startswith(PROJECT_PREFIX, autoescape=True)` AND `end_user NOT IN _NO_CUSTOMER`. Docstring :330-350 states a project row charged to the person is "in this figure *and* in the project's attributed one"; this is the note the plan says the change reverses.
- `spend_logs_repository.py:103-106` `_NO_CUSTOMER = ("N/A", "")`: uploader stores a missing customer as literal `'N/A'`.
- `src/codemie/service/spend/attribution.py:52` `PROJECT_PREFIX = "codemie:project:"` (already imported at `spend_logs_repository.py:58`); `parse_end_user` :98 treats `startswith(PROJECT_PREFIX)` as the project-scoped grammar.
- `spend_logs_repository.py:689` `sum_for_holder`, `:716` `sum_for_holders` - evaluate `predicate_for_holder` as `SUM(spend) FILTER (WHERE ...)`; the OR of the chunk's predicates narrows the scan (:772-781). Both default `access=None` (budget answer, no narrowing).
- `src/codemie/repository/spend_logs_model.py:245-257` `PROJECTED_DIMENSIONS` already contains `end_user` (comment :243: added for the personal holder). The hourly projection serves holder reads; `ProjectHolder` is in the `PROJECTABLE_READS` test list (`test_spend_logs_repository_projection.py:115`).
- Ledger port chain: `spend_service.consumed_for_holders` (`src/codemie/service/spend/spend_service.py:120`) -> cutover (`cutover.py:301`) -> `SpendLogsLedger.consumed_for_holders` (`src/codemie/enterprise/litellm/spend_ledger.py:171`) -> repository. `default_ledger.py:422` and `legacy_ledger.py:245` answer category-keyed holders as `no_figure` (no predicate involved). `spend_ledger.py:74` `_PROJECT_KEYED_HOLDERS` only decides the project-dimension label.

Surfaces that read the project holders (all through `spend_service.consumed_for_holders(holders, period)`, no `access`):
- `ProjectMemberHolder`: `src/codemie/service/analytics/handlers/member_spend_service.py:358` (`get_user_project_rows`, profile "Your personal spending" project rows) and `:440` (`get_project_member_spend`, per-member table), both via `_consumed_by_key` (:155-213).
- `ProjectHolder`: `member_spend_service.py:232` `_project_totals` (enforcement-off project limits, applied by `_apply_unenforced_limits`); `src/codemie/service/project/project_spending_service.py:312` `_holder_and_period` / `_budget_spend` :315 (list summary, detail summary, widget - the file docstring says all three derive from these pairs); `src/codemie/rest_api/routers/projects/crud.py:167` (project list budget summaries, own copy of the holder loop).
- `ProjectGroupHolder`: no production code constructs it (grep over `src/`: only the predicate case, the `default_ledger.py:422` match, `spend_ledger.py:74`); tests do.

Purpose B: "Know which rows a person's own key paid for (no customer) versus a project member customer."
- Runtime bypass (sends identity, no customer): `src/codemie/enterprise/litellm/proxy_router.py:938-950` (`is_personal` credentials -> `USER_CREDENTIALS_BYPASS`, sets `SPEND_ATTRIBUTION_CATEGORY`), `src/codemie/enterprise/litellm/llm_factory.py:749-766` (`if creds:` bypass, comments say attribution is deliberately kept). `credentials.py:140-156` returns the user setting for `get_litellm_creds` and skips PROJECT-type settings.
- Member tracking hardcoded on: `llm_factory.py:1105`, `proxy_router.py:1019` (`project_member_tracking_enabled=True`), also `runtime_budget_selection.py:54`.

Other touched code (not named above): none beyond the above; plan says "No other production file changes".

### Decisions Already in the Code
- Holder predicates are centralised in one function; consumers are told not to write their own (`ledger.py:51-54` comment; `sum_for_holders` docstring :726-735 "re-expressing it is exactly what predicate_for_holder exists to prevent").
- Project pot membership is by attribution (`attr_form/attr_project`), personal pot membership by charged customer (`end_user`). Recorded reason for the personal side: EPMCDME-15204 (`_personal_predicate` docstring). For the project side: none found for ignoring `end_user`.
- `_project_scoped_match` carries the form conjunct "so that three holders and the access boundary share one spelling" (:285-296); it is shared with `access_predicate` (:433) and report filters (:495, :553) via `_project_scoped_match_any`/`_project_scoped_match`.
- Budget reads pass `access=None` deliberately (budget answer is a property of the holder, `ledger.py:646-658`).
- Convention for predicate tests: compile to PostgreSQL SQL with literals inlined and assert substrings (`tests/codemie/repository/test_spend_logs_repository.py:68-70`, `PersonalHolder` tests :89-158).
- Data direction: `end_user` is stored verbatim as LiteLLM's customer, with `'N/A'` for none; project customers are `codemie:project:<p>:category:<c>:user:<id>`; the needed test (prefix match) matches how it is stored. `attr_*` columns are parsed/explicit derivations of it.
- Existing `startswith(..., autoescape=True)` renders as `end_user LIKE 'codemie:project:' || '%%' ESCAPE '/'` (asserted for the NOT LIKE form at `test_spend_logs_repository.py:98,110`).

### Task Statements Checked Against the Code
- "ProjectHolder / ProjectMemberHolder / ProjectGroupHolder predicates ignore end_user" - holds (`spend_logs_repository.py:263-279`).
- "PersonalHolder already has the matching customer test" - holds (:351-357).
- "Global integration handled as the user's own key; both paths take the own-credentials bypass" - holds (`proxy_router.py:938`, `llm_factory.py:749`, `credentials.py:140-152`).
- "Bypass still sends identity so the row is `explicit`, `project_scoped`, `end_user='N/A'`" - code comments confirm identity is sent (`llm_factory.py:760-766`, `proxy_router.py:945-949`); the uploader that writes `'N/A'` and the `explicit` row live in the `codemie-enterprise` package, not in this repo: only `_NO_CUSTOMER` (`spend_logs_repository.py:103`) and the repo tests document it. Unverified here (would be settled by the uploader source, plan says 2.3.48 sources).
- "Project-funded traffic always carries a project member customer (member tracking hardcoded)" - holds for the two call sites (`llm_factory.py:1105`, `proxy_router.py:1019`); `runtime_budget_selection.py:47-63` still contains a `project_member_tracking_enabled` parameter/branch.
- "end_user is already in PROJECTED_DIMENSIONS so the projection still serves the read" - holds (`spend_logs_model.py:256`).
- "Keep the condition out of `_project_scoped_match` / `_project_scoped_match_any`" - consistent: both are shared with `access_predicate` (:433, :437) and report filters (:495, :553); keeps those mechanisms unchanged.
- "No ledger reader blocks a request" - holds for the callers found: all `consumed_for_holders` callers are display/analytics surfaces (member_spend_service, budget_usage_service, project_spending_service, crud, budget_handler, spending.py, leaderboard collector, user_management_service, user_handler). Enforcement is not in this repo's ledger path.
- "Stopped/inactive project budget rows currently count in project figure and will not after" - holds for the current side (predicate has no customer term); it reverses the `_personal_predicate` docstring note (:338-341).
- "No other production file changes; fix corrects existing ledger rows without backfill" - read-side only; consistent (predicates are evaluated at query time).
- "AC4/AC5 wording" and the Jira ticket content - unverified (ticket URL is outside the repository and not readable here).

### Consumers of Changed Contracts
Changed contract: what rows `predicate_for_holder` returns for the three project holders (not `_project_scoped_match*`).
- `member_spend_service.py:358` and `:440` (`ProjectMemberHolder`) - profile project rows and per-member table; figure drops own-key/no-customer rows.
- `member_spend_service.py:232` `_project_totals` (`ProjectHolder`) - enforcement-off limits.
- `project_spending_service.py:312,338` and `crud.py:167,175` (`ProjectHolder`) - project list/detail/widget budget summaries and spend percentages.
- `budget_usage_service.py:339-382` - uses only `PersonalHolder` (unchanged); project rows on the Profile table come from `member_spend_service`.
- `spend_logs_repository.py:716-785` `sum_for_holders` - embeds the predicate in `FILTER` and in the OR-narrowing WHERE; the test `test_batched_holder_reads.py:103-111` asserts `sql(predicate_for_holder(holder)) in rendered`, so batch and single stay equal automatically.
- `may_hold_rows` (`spend_logs_repository.py:383`) - only prunes `PersonalHolder`/`PersonTotalHolder`; project holders always priced (unaffected).
- `PersonTotalHolder` (user_handler.py:456, collector.py:1162, `access_predicate`) - uses bare `attr_user_id`; not touched.
- Tests with data (SQLite rows) that evaluate the project holders; rows built without `end_user`:
  - `tests/codemie/repository/test_spend_logs_holder_access_boundary.py:87-104` `_project_row` sets no `end_user` (NULL). `TestAProjectHolderIsNarrowedToTheCallersOwnShareOfTheProject` (:216-228, expects 50.01 / 0.01 / 50.01) and :236 read `PROJECT_A` over these rows, so they would read 0 after a customer test is added to the project predicate.
  - `test_spend_logs_holder_access_boundary.py:418-479` `every_match_path` fixture: the "charged-to-person" row (end_user="bob", project A) is counted by `PROJECT_A` today, the "member" row (project customer) stays; final `sum(...) == 9` count of non-zero figures stays 9 (PROJECT_A still has the member row).
  - Other files that mention project holders and hold project_scoped rows/assertions: `tests/codemie/repository/test_spend_logs_access_boundary.py`, `tests/codemie/service/spend/test_spend_group_aggregation.py`, `tests/codemie/service/spend/test_legacy_ledger.py` (mention `project_scoped`; not checked row-by-row).
- Predicate-shape tests: `test_spend_logs_repository.py:161-184` (`TestTheProjectHolders`, 3 tests assert existing conjuncts only; `test_the_project_holder_does_not_key_on_a_user` asserts `"attr_user_id" not in rendered` - an `end_user` term does not contain that substring), :211-230 (`TestEveryHolderKindHasAPredicate`).

### Architecture and Layers Affected
- Repository layer only for the change: `src/codemie/repository/spend_logs_repository.py` (`predicate_for_holder`).
- Read through the spend-ledger port (service/spend), the enterprise ledger adapter (`enterprise/litellm/spend_ledger.py`), and consumed by analytics handlers, project service and the projects router.

### Integration Points
- LiteLLM spend logs ingested by the `codemie-enterprise` uploader into `litellm_spend_logs` (`LiteLLMSpendLog`, `spend_logs_model.py`), plus the hourly projection (`spend_projection.py`).
- LiteLLM customer id (`end_user`) is the shared key between CodeMie attribution and LiteLLM's own budget counters.

### Patterns and Conventions
- Holder = frozen dataclass in `service/spend/ledger.py`; predicate resolved only in `predicate_for_holder`.
- SQL helpers return `ColumnElement[bool]`; predicates tested by compiled-SQL substring assertions; SQLite-backed fixture tests for figures (`_SyncSessionAsAsync`).
- Code in this repo carries heavy explanatory docstrings; user memory notes say no new explanatory code comments are wanted in changes, and no feature docs under docs/.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/` exists (architecture/repository/testing guides; `testing/testing-patterns.md`, `data/repository-patterns.md`, `architecture/layered-architecture.md`). No guide is specific to the spend ledger / holders. `knowledge-manifest.json` present.
- Plan: `docs/superpowers/plans/2026-11-09-epmcdme-16929-project-budget-own-key-spend.md` (read, see Section 8).

### Architectural Decisions
- EPMCDME-15204 (personal predicate keyed on charged customer) recorded in the `_personal_predicate` docstring and the `PersonalHolder` tests; EPMCDME-11961 / EPMCDME-13264 (bypass behaviour) in `llm_factory.py:755-758`; EPMCDME-15237 AC3 (member and project figures cover the same window) in `member_spend_service.py:172-175`.

### Derived Conventions
- Do not widen `_project_scoped_match*`; add budget-only terms inside `predicate_for_holder`.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/repository/test_spend_logs_repository.py` - predicate shapes (`TestTheProjectHolders` :161, `TestThePersonalHolder...` :89, `TestEveryHolderKindHasAPredicate` :211).
- `tests/codemie/repository/test_spend_logs_repository_projection.py` - projected vs raw statements; `PROJECTABLE_READS` includes "project holders" (:115) and "personal holder" (:131); no member-holder entry.
- `tests/codemie/service/spend/test_batched_holder_reads.py` - batched predicate equals single predicate (:90-111) for Project/ProjectMember holders.
- `tests/codemie/repository/test_spend_logs_holder_access_boundary.py` - SQLite row-level figures for `PROJECT_A`, member holder (see Consumers).
- `tests/codemie/service/analytics/handlers/test_member_spend_service.py` (8 holder refs), `tests/codemie/rest_api/routers/test_projects_router.py` (9), `test_projects_router_auditor.py` (2), `tests/codemie/service/spend/test_default_ledger.py`, `test_legacy_ledger.py`, `test_access_boundary.py`: stub/route holders; the ledger is stubbed, so they do not evaluate the predicate.

### Testing Framework and Patterns
- pytest (+ pytest-asyncio); compiled-SQL string assertions via `sql()` helper; in-memory SQLite `Row.metadata.create_all` fixtures for row-level figures; `_CapturingSession` for statement capture.

### Coverage Gaps
- No existing test asserts an `end_user` term on any project holder, nor a row-level test of a no-customer (`'N/A'`) or NULL-`end_user` row against a project holder.
- No test asserts `_project_scoped_match`/`_project_scoped_match_any` render no `end_user` term.
- No row-level coverage of the member-holder projection read.

---

## 5. Configuration and Environment

### Environment Variables
- `SPEND_LEDGER_PROJECTION_ENABLED` (default True, `configs/config.py:779`) - decides whether reads use the hourly projection; the composition root is `build_spend_logs_ledger` (`spend_ledger.py:869`).
- `SPEND_LEDGER_COVERAGE_FLOOR` (:747, default `2026-11-01T00:00:00Z`), `SPEND_LEDGER_IDENTITY_FLOOR`, `SPEND_LEDGER_READY`, `SPEND_LEDGER_LEGACY_BELOW_FLOOR` (:773, default True) - below the floor, category-keyed holders return no figure from the legacy ledger (no predicate used).

### Configuration Files
- None specific to the predicate.

### Feature Flags and Deployment Concerns
- Projection flag above. No migration surface found for a read-side predicate change (`end_user` already projected: `spend_logs_model.py:256`).

---

## 6. Risk Indicators

- Existing mechanism kept in place by a task statement: none contradicts; the plan keeps `_project_scoped_match*` unchanged, which also serve `access_predicate` and report filters (see Consumers).
- Existing project-holder row-level tests build rows with NULL `end_user` (`test_spend_logs_holder_access_boundary.py:87-104`, fixture :147-175); Speculative: they would need an `end_user` on those rows once the predicate gains a customer term.
- `ProjectGroupHolder` has no production consumer found; only predicate-shape tests cover it.
- `_personal_predicate` docstring (:338-341) states project rows charged to the person are also in the project's figure; the plan's behaviour change reverses that statement (stopped/inactive project budgets).
- Uploader behaviour (`'N/A'`, `explicit` row, `attr_form`) is implemented in `codemie-enterprise`, outside this repo; this repo only documents it in `_NO_CUSTOMER` and tests. Plan says installed 2.3.44 has no uploader; verification needs 2.3.48 sources.
- Jira ticket (AC wording, AC4/AC5) not readable from the repo; unverified.
- Speculative: `crud.py:147-177` duplicates the holder loop that `project_spending_service._budget_spend` also has; both read the same holders, so one predicate change reaches both.
- No ES/legacy path involved: below `SPEND_LEDGER_COVERAGE_FLOOR` project holders have no figure, so the change affects only SQL-ledger periods.

---

## 7. Summary for Complexity Assessment

The change sits in one function of the repository layer: `predicate_for_holder` in `src/codemie/repository/spend_logs_repository.py` (three project-holder cases, :263-279). All project-budget surfaces named in the task (Profile project rows, per-member table, enforcement-off limits, project list/detail/widget summaries) reach the ledger through `spend_service.consumed_for_holders` and evaluate that predicate, so no service or router code reads `end_user` itself. The sibling `_personal_predicate` and the `PROJECT_PREFIX` constant already exist in the same module, and `end_user` is already a projected dimension, so the pattern and the data availability are established.

Production file surface found: one file. Test surface: the compiled-SQL predicate tests in `test_spend_logs_repository.py` (`TestTheProjectHolders`), the projection test list, and the SQLite row-level tests in `test_spend_logs_holder_access_boundary.py` whose project rows carry NULL `end_user` and are evaluated against `PROJECT_A`. Everything else that mentions the holders stubs the ledger.

Risk factors: the behaviour change for stopped/inactive project budgets (rows charged to the person's own customer leave the project figure; reverses a recorded EPMCDME-15204 note), the keep-out of the shared `_project_scoped_match*` helpers (shared with access boundary and report filters), and dependence on an uploader outside this repo for the `'N/A'` value. Nothing novel architecturally; no schema or config surface found.

---

## 8. External References

- `docs/superpowers/plans/2026-11-09-epmcdme-16929-project-budget-own-key-spend.md` - resolved (in repo), read. Facts: root cause = three project holder predicates ignore `end_user`; proposed `_charged_to_project_customer()` = `Row.end_user.startswith(PROJECT_PREFIX, autoescape=True)` added with `and_` to the three `predicate_for_holder` project cases; keep out of `_project_scoped_match` / `_project_scoped_match_any`; update `predicate_for_holder` docstring; tests: `TestTheProjectHolders` renders `end_user LIKE 'codemie:project:' || '%%' ESCAPE '/'` for each holder, existing conjuncts remain, `_project_scoped_match*` render no `end_user`; projection test keeps `ProjectHolder`/`ProjectMemberHolder` reads on the projection; service tests need no change. Row table: project customer counted, own-key `N/A` not counted, NULL not counted, unattributed sentinel excluded by `attr_form`. Accepted behaviour change: stopped/inactive project budgets. Repro fixtures: user `repro16929@codemie.ai`, project `repro16929-proj`; expected `repro16929-proj` row after fix equals LiteLLM's member customer spend. Ticket notes: AC4 already satisfied (enforcement reads LiteLLM counters), AC5 to be reworded (personal keys get the same correction).
- https://jiraeu.epam.com/browse/EPMCDME-16929 - unresolved (external URL; not readable from here).
- `docs/superpowers/tasks/2026-11-09-epmcdme-16929-project-budget-own-key-spend/state.local.json` exists in run_dir (not read).
