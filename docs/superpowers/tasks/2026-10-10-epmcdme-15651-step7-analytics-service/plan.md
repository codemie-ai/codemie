# EPMCDME-15651 Step 7 (analytics): Spending Formatters to service/analytics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `_SPENDING_METRIC_DEFINITIONS` and four live functions from `routers/analytics/_spending_support.py` into new `service/analytics/spending_formatter.py`; `spending.py` calls the public names. No API change.

**Architecture:** Not move-only (the handler now imports the service), so spec AC 2-4 replace the AST proof of steps 2-6: characterization and wiring tests first (green at BASE), one extraction commit with the rewire, proof last. Commit per task using the repository's existing convention (`EPMCDME-15651: Capital description`, e.g. "Add spending formatter characterization tests", "Add analytics spending wiring tests", "Extract spending formatters to service layer"); a task's tests and code share its commit.

**Tech Stack:** Python, FastAPI handlers called directly, pytest with `unittest.mock`, ruff.

**Spec:** `docs/superpowers/tasks/2026-10-10-epmcdme-15651-step7-analytics-service/spec.md` (research: `technical-analysis.md`; precedent: `../2026-10-10-epmcdme-15651-step7-assistant-service/plan.md`). `BASE` = HEAD before Task 1 (`e840b7f4e8` or a later planning-only commit). Tasks 1-2 touch tests only.

## Global Constraints

- Rename map (underscore dropped): `_build_spending_metric`, `_build_spending_metrics`, `_calculate_time_until_reset`, `_paginate_tabular`. `_SPENDING_METRIC_DEFINITIONS` moves and stays private.
- Bodies (docstrings, comments, the function-local `from datetime import timezone`) are copied by `extract.py` from BASE, never retyped; parameters unchanged. New module: verbatim 13-line Apache header of `_spending_support.py` (line 13 `# limitations under the License.`), `from __future__ import annotations`, imports only stdlib and `ResponseFormatter` from `codemie.service.analytics.response_formatter`, own `logger = logging.getLogger(__name__)`, module functions, no class, no re-export from `service/analytics/__init__.py`.
- `_spending_support.py` keeps byte-identical: `_authorize_admin_budget_view` (access gate, stays in the router), `_get_user_all_projects`, `_get_key_spending_columns`, `_build_spending_row`, `_build_key_spending_tabular_data`. Only edits: import `calculate_time_until_reset` for the one call in `_build_spending_row` (renamed, no alias); delete the moved code and the imports it orphans, including `import logging` and `logger`.
- Untouched: `_common.py`, `main.py`, `tests/codemie/rest_api/router_guards`, every existing test file (incl. `test_analytics_capability_guards.py:285`); `regenerate_baselines` never. Nothing under `service/` imports `routers/`.
- Stage only the task's files by path (never `git add -A`, `opencode.json.lock`, `skills-lock.json`, the task dir, anything under `local/`); no push, no MR. Stage 7 owns `make ruff` (never run it: it rewrites files), the whole test run, `router_guards` and the stale-importer sweep (spec AC 9); the cold `python -I` import of the new module (spec AC 7) runs in Task 3. Per task run `poetry run ruff check` and `poetry run ruff format --check` on the changed files. A failure the plan does not describe: stop, report, ask.

## Tools and checks

`WORK=local/router-split/step7/analytics` (subfolder of 7a's gitignored dir, `git check-ignore $WORK`; 7a's scripts are not touched; nothing here is staged). Python as `poetry run python`; each tool is built in the first task that needs it.

- `base_spending_support.py` = `git show BASE:src/codemie/rest_api/routers/analytics/_spending_support.py`, re-verified against git on every run.
- `mutate.py [--moved]`: applies one mutation at a time (table in Task 1: id, occurrence from the top of the file, old, new) to `_spending_support.py` (`--moved`: to `spending_formatter.py`, old/new passed through the rename map), refuses a mutation whose `old` is absent at that occurrence, runs the characterization file, requires non-zero, restores with `git checkout --`, and finishes with `git diff --exit-code -- src`.
- `extract.py <name>...` slices the named BASE items by `ast` line range (the constant with its comment line), applies the rename map on word boundaries, creates the destination with header, imports and `logger`, and refuses output that differs from BASE under the map.
- `bodycheck.py [--expect N] [--negative]` compares `ast.dump` (docstring included, annotations kept) of BASE against now under the rename map: the constant assignment and the 4 moved functions in the new module (5), the gate, `_get_user_all_projects`, `_get_key_spending_columns`, `_build_spending_row`, `_build_key_spending_tabular_data` in `_spending_support.py` (5); `--negative` changes one character of a string constant in a scratch copy of each module and must exit non-zero.
- `AT` = `poetry run pytest -n 0 --show-capture=no -q` on `tests/codemie/service/analytics/test_spending_formatter.py`, `tests/codemie/rest_api/routers/{test_analytics_spending_wiring,test_analytics,test_analytics_member_spending,test_analytics_auditor,test_analytics_capability_guards}.py`, `tests/enterprise/litellm/test_premium_models_budget.py` (those that exist so far).
- `DIFFCHK` = `git diff -U0 $(cat $WORK/T<n>) -- <file>` shows only import lines and renamed identifiers.

## Review Focus

- Dead `_build_spending_row` calls the moved `calculate_time_until_reset`: a missing import is F821 and caught by `LINT` and `bodycheck` (Task 3).
- `calculate_time_until_reset` resembles the differently behaving twin `budget_usage_service._calculate_time_until_reset` (naive timestamp, expired text): Task 1 pins the router behaviour; Task 4 greps are path-qualified.
- Time-dependent tests: reset = now + 3 days 16 hours 30 minutes 30 seconds (30 s of slack, no clock patch; 16 hours so the `% 43200` mutation is not equivalent); wiring future date is 2999 and matched by pattern.
- The `codemie` logger has `propagate: False` (`configs/logger.py:79`), so root-level `caplog` can miss records; the logger name changes with the module.
- `test_analytics_capability_guards.py:285` is inert when mis-aimed: Task 4 flip.

---

### Task 1: Characterization tests, green at BASE

**Files:** Create `tests/codemie/service/analytics/test_spending_formatter.py`. Setup, uncommitted: `$WORK`, `BASE` file (`git rev-parse HEAD`), `base_spending_support.py`, `mutate.py`.

Test-first: yes — a pure relocation cannot go RED, so the first check is every new test green against the unmoved private names. Then the scripted mutation set: each mutation must turn at least one test red (for example `test_time_until_reset_formats_days_hours_and_minutes` fails when `total_seconds // 86400` becomes `// 43200`).

- [ ] Import the module as an object, `from codemie.rest_api.routers.analytics import _spending_support as spending_formatter` (Task 3 swaps this one line), and call `spending_formatter._build_spending_metric(...)` etc. Log fixture: `spending_formatter.logger.addHandler(caplog.handler)` plus `caplog.set_level(logging.DEBUG, logger=spending_formatter.logger.name)`, removed on teardown; assert on `(levelname, getMessage())` of that logger's records.
  - Metric builder: exact label and description of the four ids (`_spending_support.py:35-52`, parametrized); `cli_current_spending` and `premium_current_spending` use the id as label, empty description (`..._falls_back_to_the_id_as_label_pinned_quirk`); `format` key present only when truthy (absent for `None` and `""`); default `type` is `"string"`.
  - Metrics unavailable: `None` and `{}` (`..._empty_dict_is_unavailable_pinned_quirk`) give current 0.0 `number`/`currency` and `"N/A"` `string` for the other three, plus INFO `Budget tracking not available for user u1`.
  - Metrics with data (reset date in the future): full 4-item list; `budget_limit=None` stays a `number` metric holding `None`; missing `total_spend` gives 0.0; reset date gives `date`/`timestamp`; `None` or `""` reset gives `"N/A"` `string` with no `format`, and `time_until_reset` `"N/A"`; INFO `Spending analytics retrieved for user u1: spend=$15.50, budget=100.0, time_until_reset=...`.
  - Time until reset: now + 3 days 16 hours 30 minutes 30 seconds gives `"3 days 16 hours 30 mins"` (16 hours: with a 4-hour fixture `% 86400` to `% 43200` would be an equivalent mutation); `Z` and `+00:00` spellings equal; one hour past and 400 days past give `"0 days 0 hours 0 mins"` (`..._expired_text_pinned_quirk`); `None` and `""` give `None` with no WARNING; `"not-a-date"` and `12345` give `None` plus WARNING starting `Failed to parse budget_reset_at: <value>, error: `; naive timestamp raises `TypeError` (`..._naive_timestamp_raises_pinned_quirk`).
  - `paginate_tabular`: with `patch.object(ResponseFormatter, "format_tabular_response")`, 5 rows: slices for pages 0, 1, last partial, beyond end; kwargs `columns`, `filters_applied` (same object), `page`, `per_page`, `total_count == 5` (unsliced), `totals=None`; returns the formatter's result; `start_time` two seconds ago gives `2000 <= execution_time_ms < 4000`.
- [ ] `AT` green, `LINT` on the file (this is the "green at BASE" check; do it before any mutation).
- [ ] Build `mutate.py` with one row per item, each applied exactly once, labels and descriptions at occurrence 1 (dead `_get_key_spending_columns` repeats those eight strings): arithmetic (`total_seconds // 86400` to `// 43200`, `total_seconds % 86400` to `% 43200`, `remaining_seconds // 3600` to `// 1800`, `% 3600) // 60` to `// 30`, `) // 60` to `) / 60`, `{minutes} mins` to `{minutes} min`); expired string to `"Expired"`, `total_seconds() < 0` to `< -86400`; `.replace('Z', '+00:00')` to `.replace('Z', '')`; the `if not budget_reset_at:` return to `""`, `except (ValueError, AttributeError)` to `AttributeError` only and to `ValueError` only, the except-branch `return None` to `"N/A"`, warning text; each of 4 labels and 4 descriptions plus `X`; the two `.get(..., metric_id)` / `.get("description", "")` fallbacks; `if format_type:` to `if True:` and `if not format_type:`; `"type": value_type` to `"string"`; `if not spending_data:` to `if spending_data is None:`; unavailable current `0.0` to `1.0` and each of the three `"N/A"` to `"-"`; `get("max_budget")` to `... or "N/A"`; `get("total_spend", 0.0)` default to `1.0`; `date`/`string` swap; `"timestamp"` unconditional; the two `else "N/A"` to `else None`; three log texts; slice end `+ 1` and start `+ 1`; `* 1000` to `* 100`; `totals=None` to `{}`; `columns=[]`; `page=page + 1`; `per_page=per_page + 1`; `total_count=len(page_rows)`; `filters_applied={}`; `rows=rows`. Run it: every row red, tree clean after.
- [ ] Commit (tests only); `git rev-parse HEAD > $WORK/T1`.

### Task 2: Handler wiring tests, green at BASE

**Files:** Create `tests/codemie/rest_api/routers/test_analytics_spending_wiring.py`.

Test-first: yes — green at BASE (the wiring exists), imports no moved name, stays byte-identical across the move. Spot-check in the working tree: `page=0` instead of `page=page` in the `_paginate_tabular` call at `spending.py:303-310` turns the pagination test red; `None` instead of `spending_data` at `spending.py:110` turns the `/spending` tests red; `git checkout --` the file after each.

- [ ] Handlers imported function-locally from `codemie.rest_api.routers.analytics.spending` (`get_user_spending`, `get_user_project_spending`, `get_project_member_spending`); nothing else from that module.
- [ ] `/spending`: patch set and user as in `test_analytics.py:1193-1198` (`codemie.enterprise.litellm.dependencies.*`), `MagicMock(id="u-1", username="testuser")`; compare the full `metrics` list parsed from `response.body`. `_create_response` dumps the `Metric` model (`_common.py:97-107`, `models/analytics.py:50-60`), so every metric also carries `format` (null when unset), `fixed_timeframe: None`, `secondary_metrics: None`: build them with a local `_metric(...)` helper. Cases with spend 15.5 and limit 100.0: reset `2999-01-01T00:00:00Z` (`time_until_reset` value matched by `^\d+ days \d+ hours \d+ mins$`, everything else exact), reset `2020-01-01T00:00:00Z` (`"0 days 0 hours 0 mins"`), reset `None` (`"N/A"` twice); extras: proxy `{"total_spend": 1.5}` and premium enabled returning `{"total_spend": 2.5}` append `cli_current_spending` then `premium_current_spending` (label = id, description `""`, `number`, `currency`) after the four.
- [ ] `user-project-spending` and `project-member-spending`: handlers called directly with `user=MagicMock(is_admin=True)` and explicit `page`/`per_page`; `get_async_session` patched at `codemie.rest_api.routers.analytics.spending.get_async_session` with an async-context mock; service calls patched as in `test_analytics_member_spending.py:119-250` (`resolve_spend_subject` returning `(target, {"atlas-core"})`, `get_user_project_spend` / `get_project_member_spend` as `AsyncMock` returning `(columns, rows)`). Each: 5 rows, page 1 and `per_page` 2 returns rows 2 and 3, `pagination` `total_count == 5` and `has_more` as observed at BASE, `filters_applied` is `{"users": email}` / `{"projects": name}`, `columns` passed through; a page past the end returns `[]`.
- [ ] `AT` green, `LINT`; spot-checks; commit; `git rev-parse HEAD > $WORK/T2`.

### Task 3: `spending_formatter` module and rewire

**Files:** Create `src/codemie/service/analytics/spending_formatter.py`; modify `routers/analytics/_spending_support.py` (delete `:17-18`, `:29`, `:31`, `:34-52`, `:67-175`, `:300-324`; add the one import and rename the call at `:235`); modify `routers/analytics/spending.py` (import block `:40-45` keeps `_authorize_admin_budget_view` from `_spending_support` and takes the three public names from `codemie.service.analytics.spending_formatter`; calls `:110`, `:117`, `:129`, `:303`, `:340`); modify `test_spending_formatter.py` (the one import line and the renamed identifiers).

**Interfaces:** Produces, signatures unchanged from BASE: `build_spending_metric(metric_id: str, value: Any, value_type: str = "string", format_type: str | None = None) -> dict`, `build_spending_metrics(spending_data: dict | None, user_id: str) -> list[dict]`, `calculate_time_until_reset(budget_reset_at: str | None) -> str | None`, `paginate_tabular(*, columns: list[dict], rows: list[dict], filters_applied: dict, start_time: datetime, page: int, per_page: int) -> dict`.

Test-first: yes — retarget the test import first (`from codemie.service.analytics import spending_formatter`, public names): `ImportError` for the missing module; after the move the Task 1 and Task 2 tests are green.

- [ ] Retarget the test, run it red; build `extract.py`, create the module (`_SPENDING_METRIC_DEFINITIONS`, then the four functions in BASE order); edit `_spending_support.py` and `spending.py`.
- [ ] `bodycheck.py --expect 10` OK; `poetry run python -I -c "import codemie.service.analytics.spending_formatter"` exits 0 (a cycle: stop, ask); `AT` green; `LINT` on the four changed files (no F401 or F821 in `_spending_support.py`); `DIFFCHK` against `T1` for the test file; commit; `git rev-parse HEAD > $WORK/T3`.

### Task 4: Proof and handoff

**Files:** none; nothing to commit (a working-tree diff is a bug).

Test-first: no — proof only, no code change.

- [ ] AC 1: `sed -n 13p` of the new module; `rg -nw` over `src tests`: `_build_spending_metric`, `_build_spending_metrics`, `_paginate_tabular` 0 hits; `_SPENDING_METRIC_DEFINITIONS` only in the new module; `_get_user_all_projects` and `_build_key_spending_tabular_data` only in `_spending_support.py`; `_calculate_time_until_reset` only in `budget_usage_service.py` and `test_budget_usage_service.py`; `_get_key_spending_columns` and `_build_spending_row` also in `_spending_support.py` (the first also in `test_analytics.py`, `test_analytics_budget_usage_labels.py`).
- [ ] AC 2: `bodycheck.py --expect 10`, `--negative` exits non-zero. AC 3 again: `mutate.py --moved` every row red against the new module, tree clean after.
- [ ] AC 4: `git diff --exit-code $(cat $WORK/T2) -- tests/codemie/rest_api/routers/test_analytics_spending_wiring.py` empty; `rg -nw` of the five moved names in that file 0 hits; `DIFFCHK` against `T1` for `test_spending_formatter.py`; `git diff BASE -- src/codemie/rest_api/routers/analytics/_spending_support.py` shows only import lines, the deleted moved code and the renamed call; `git diff --stat BASE -- tests` lists only the two new files.
- [ ] AC 5 flip: with a clean tree, point `test_analytics_capability_guards.py:285` at `codemie.rest_api.routers.analytics._spending_support._authorize_admin_budget_view`, run its test (`-k test_project_member_spending_unaffected_by_enterprise_guard`): `PASS` expected (inert, handoff Step 4); `git checkout --` the file, `git diff --exit-code -- tests` empty. `rg -n "patch.*(build_spending_metrics?|calculate_time_until_reset|paginate_tabular)" tests src` 0 hits; the `spending.get_async_session` and `spending.asyncio.to_thread` literals sit in untouched files.
- [ ] AC 6, 8: `rg -nUP "routers\.[\w.]+ import (?:[^\n(]*|\([^)]*)\b_[a-z]\w*" src` hits only modules that define no `router` (`_spending_support.py` is one); `rg -n "routers\." src/codemie/service src/codemie/rest_api/handlers` hits only `routers.utils` (3 and 1), none in the new module; `git diff --exit-code BASE -- src/codemie/rest_api/main.py tests/codemie/rest_api/router_guards`; `rg -nw "_build_spending_metrics?|_paginate_tabular|_spending_support"` over the four sibling repos 0 hits.
- [ ] Handoff: append `## Step 7b: analytics spending formatters to service/analytics` to `local/router-split/HANDOFF.md` (never staged): status, `T1`..`T3` shas, name map, decisions (gate stays, dead cluster stays with one renamed call, one module, `:285` unchanged), the flip result (`:285` inert), logger name change (`codemie.rest_api.routers.analytics._spending_support` to `codemie.service.analytics.spending_formatter`), pinned quirks (`*_pinned_quirk` tests), spec non-goal MR notes (naive-timestamp `TypeError`, expired text differs from the twin, id-as-label fallback, empty dict as unavailable, redundant `timezone` import, 404 before authorization at `spending.py:171-176`, `Cache-Control: public` on per-user `/spending`), no security code moved (gate byte-identical), the single MR must still ask a reviewer to post `/sanity` and carry a `## Test harness` block (because of 7a), Stage 7 owns `make ruff`, the full run and guards. Delete `$WORK/base_*.py` and `__pycache__`; `git status --porcelain` lists only the planning docs if uncommitted.

## Negative-constraint pass

negative-constraints: stated (spec Behavior, Non-goals, AC 2-8).

- No API, route, tag, operationId or OpenAPI change; `main.py` and guards untouched: `bodycheck.py`, Task 4 `git diff --exit-code BASE`.
- Gate not moved, no `ExtendedHTTPException`/`fastapi.status`/`_common` import in `service/`: Constraints, Task 3 module imports, Task 4 AC 6 scan.
- Dead cluster, `_resolve_admin_budget_subject`, handler proxy and premium appends, `_common.py` constants not deleted or moved: Task 3 file list, `bodycheck.py` identity.
- No quirk fixed, no twin merged (`budget_usage_service.py:125`, `projects/_spending_support.py:73`): Task 1 pins, Task 4 path-qualified greps.
- No class, typed exception, injection, new logging, re-export: Constraints, Task 3.
- No existing test file edited, no `regenerate_baselines`, no sibling-repo edit, no whole-suite, ruff-wide or guard task, browser check, push, MR or staged lock/scratch file: Constraints, Task 4 `git diff --stat BASE -- tests`; none planned.
