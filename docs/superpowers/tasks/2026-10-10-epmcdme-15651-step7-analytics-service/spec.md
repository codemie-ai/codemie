# EPMCDME-15651, step 7 (analytics): spending formatters into service/analytics

## Purpose

Move the live spending formatters from `routers/analytics/_spending_support.py` into new `service/analytics/spending_formatter.py`, no API change. Not move-only (the handler now imports the service), so criteria 2-4 replace the AST proof of steps 2-6. Same branch; MR only after the whole ticket (see 10).

## Scope

Bodies unchanged: `_SPENDING_METRIC_DEFINITIONS` (stays private) and four functions that drop the underscore: `build_spending_metric`, `build_spending_metrics`, `calculate_time_until_reset`, `paginate_tabular`. Module functions, Apache header, `from __future__ import annotations`, own `logger` (name changes with the module); imports only stdlib and sibling `response_formatter`. No re-export from `__init__.py`. Only importer: `spending.py:40-45`. No test names a moved helper.

## Decisions

- **The gate stays in the router.** `_authorize_admin_budget_view` is access control, not formatting. It raises `ExtendedHTTPException` and reads `ERROR_MSG_ACCESS_DENIED`/`ERROR_MSG_ADMIN_HELP` (`_common.py:43-44`). `service/analytics/` imports neither that exception nor `fastapi.status`, and a copy cannot import `_common` (the package `__init__` imports `spending`: a cycle, and `service/` must not import `routers/`). Duplicating or moving the strings is a redesign.
- **The dead cluster stays** in `_spending_support.py`, nothing deleted. `_build_spending_row` (`:235`) calls the moved function, so the file imports `calculate_time_until_reset` and that one call identifier is renamed (no alias). Moving the cluster would add a third copy of functions whose live twins sit in `handlers/budget_usage_service.py`. Imports left unused go; so do `logger` and `import logging`: only the moved functions use `logger` (`:106,:123,:174`), the gate and dead cluster never do, and ruff would not flag the orphan.
- **One module**, named like `response_formatter.py`; `paginate_tabular` rides along (only spending endpoints use it).
- **`test_analytics_capability_guards.py:285`** (patch of `spending._authorize_admin_budget_view`) stays unchanged, correctly aimed, inert since step 4; not fixed.

## Behavior that must not change

- Labels and descriptions of the four ids; an unknown id (`cli_current_spending`, `premium_current_spending`) is its own label, empty description; `format` only when truthy.
- Empty or `None` data is unavailable: current 0.0, the other three `"N/A"`. `budget_limit=None` with data stays a number metric holding `None`. A reset date gives `date`/`timestamp`, none gives `string`, no format.
- Time until reset: `"{d} days {h} hours {m} mins"`; past gives `"0 days 0 hours 0 mins"`; `None`, empty or invalid ISO gives `None` plus a warning; `Z` reads as `+00:00`.
- `paginate_tabular` slices `rows[page*per_page:(page+1)*per_page]`, passes `totals=None`, unsliced `total_count` and `filters_applied` to `ResponseFormatter.format_tabular_response`. Both `logger.info` texts stay.

## Acceptance criteria

1. The new module has the full header (line 13 is `# limitations under the License.`). `rg -w` over `src/` and `tests/`: `_build_spending_metric`, `_build_spending_metrics`, `_paginate_tabular` 0 hits; `_SPENDING_METRIC_DEFINITIONS` only in the new module; `_get_user_all_projects`, `_build_key_spending_tabular_data` only in `_spending_support.py`. Twin names legitimately stay: `_calculate_time_until_reset` only in `budget_usage_service.py` and `test_budget_usage_service.py`; `_get_key_spending_columns`, `_build_spending_row` also in `_spending_support.py`, the first also in `test_analytics.py` and `test_analytics_budget_usage_labels.py`.
2. Body check: a throwaway script in gitignored `local/router-split/step7/` compares `ast.dump` (docstring included) of each function and of the `_SPENDING_METRIC_DEFINITIONS` assignment at BASE and now, under a declared rename map (four names, plus the call identifier in `_build_spending_row`). The gate and other dead functions match under identity. A one-character mutation must fail it.
3. Characterization tests come first, green at BASE against the unmoved private names, in new `tests/codemie/service/analytics/test_spending_formatter.py`. They pin every bullet above: labels, descriptions, day, hour and minute terms (reset = now + 3 days 16 hours 30 minutes 30 seconds, expected `3 days 16 hours 30 mins`), expired string, log records via `caplog`, `patch.object` on `ResponseFormatter`. Quirks are named `*_pinned_quirk` (naive timestamp raises `TypeError`). A scripted mutation set at BASE with at least one mutation per Behavior bullet must fail a new test per mutation: each arithmetic term (days, hours, minutes, floor division), labels, descriptions, expired string, format rule, `Z` handling, `None` or invalid input, `date` vs `string`, `budget_limit=None`, empty dict, fallback, slice bounds, `columns`/`page`/`per_page`, `total_count`, `totals`, `execution_time_ms` unit.
4. After the move that file changes only in import lines and renamed identifiers. New `tests/codemie/rest_api/routers/test_analytics_spending_wiring.py` is handler-level, imports no moved name, stays byte-identical, and pins the full `/spending` metrics list (three reset cases, cli and premium extras) and the pagination of `user-project-spending` and `project-member-spending`. No existing test file is edited.
5. Grep shows no patch literal names a moved helper, so none is retargeted (re-run at the end). Flip control: point `:285` once at `_spending_support._authorize_admin_budget_view`; it still passes (inert); revert the edit, `git diff -- tests/` shows no change to existing files. The `spending.get_async_session` (3) and `spending.asyncio.to_thread` (9) literals are unchanged.
6. No `src/` module imports an underscore name from a module that defines a `router` (`_spending_support.py` defines none, so the gate import stays). `service/` and `rest_api/handlers/` import from `routers/` only `routers.utils`; the new module imports none. Re-run the scan.
7. `poetry run python -I -c "import codemie.service.analytics.spending_formatter"` exits 0 although `service/analytics/__init__.py` loads `AnalyticsService`; the guarded app builds hermetically.
8. `router_guards` pass; `openapi.json` and `route_precedence.json` unchanged (`git diff` empty); `main.py` unchanged; `regenerate_baselines` never run. On a failure: stop, report, ask.
9. `make ruff` passes (no F401 or F821, complexity 16, license). Tests pass in `tests/codemie/rest_api/routers`, `tests/unit/routers`, `tests/codemie/service/analytics`, `tests/enterprise/litellm/test_premium_models_budget.py`, `router_guards`.
10. The handoff records the name map, decisions, inert list, logger-name change, the Non-goals quirk list (two touch authorization and caching), and that no security code moved (gate byte-identical). The single MR for steps 0-7 must still ask a reviewer to post `/sanity` and carry a `## Test harness` block, because of 7a; moving the gate later attaches the same duty to that move.

## Non-goals

- Fixing quirks, carried as MR notes: naive-timestamp `TypeError`; expired text differs from the twin; id-as-label fallback; empty dict as unavailable; redundant local `timezone` import; unknown target user gets 404 before authorization (`spending.py:171-176`); `Cache-Control: public` on per-user `/spending` (`_common.py:105`).
- Merging the three time-until-reset functions (router, `budget_usage_service.py:125`, `projects/_spending_support.py:73`, the last is step 7c) or any other twin.
- Moving the gate, `_resolve_admin_budget_subject`, the handler's proxy and premium appends or `_common.py`; deleting dead code, including unreferenced `PROJECTS_FILTER_ADMIN_DESC` and `ERROR_MSG_PROJECT_EMPTY`.
- Classes, typed exceptions, injection, new logging; any path, tag, operationId, schema or route-order change; `main.py`, guard code, other repos and packages.

## Open risks

- New tests pin what the code does, not what it should do; BASE-green plus the mutation set is the only check. The body check cannot see a wrong call site (wiring tests, greps).
- `calculate_time_until_reset` resembles the differently behaving private twin in `budget_usage_service.py`.
- Sibling repos were checked by semantic query only; grep them at the end.
