# Technical Research

**Task**: analytics spending formatting service
**Generated**: 2026-10-10
**Research path**: codegraph

---

## 1. Original Context

Implement delivery step 7, package `analytics` (run 7b), of Jira story EPMCDME-15651 ("Split oversized REST API router modules into smaller per-concern modules"). Steps 0-6 and 7a (assistant helpers -> service/assistant/) are done on branch EPMCDME-15651_split-router-modules: the five big router modules are now packages. Ticket step 7: "Move domain helpers into services, one merge request per package." For `analytics` the ticket says: "Move the spending formatting helpers into `service/analytics/`." They live in the package src/codemie/rest_api/routers/analytics/ (notably `_spending_support.py`, 324 lines, and possibly helpers in `_common.py`, `spending.py` or other route modules; find the exact set of non-HTTP domain/formatting helpers used for spending). HANDOFF notes dead code kept verbatim in step 4 (`_get_user_all_projects`, `_build_key_spending_tabular_data` and its two callees, `PROJECTS_FILTER_ADMIN_DESC`, `ERROR_MSG_PROJECT_EMPTY`) and that helpers were "not moved to service/analytics/ (step 7)": decide with evidence what moves, what stays, and what dead code is out of scope.
Unlike steps 1-6 this is NOT a pure file move: handlers call the new service and test patch targets move. The pattern just used in run 7a (read docs/superpowers/tasks/2026-10-10-epmcdme-15651-step7-assistant-service/{spec.md,plan.md,technical-analysis.md} and the "Step 7: assistant helpers to service/assistant" section at the end of local/router-split/HANDOFF.md) is the precedent: module functions (not classes), bodies unchanged, function-local imports kept, characterization tests first (green at BASE), body-AST check under a rename map, retargeted patch literals plus flip check, guards byte-identical. Constraints: no API behaviour change (OpenAPI snapshot and route-precedence baselines in tests/codemie/rest_api/router_guards/ stay byte-identical, nothing regenerated); modules under service/ and rest_api/handlers/ must NOT import from rest_api/routers/ (except routers/utils.py); no src module imports an underscore name from a module that defines a `router`; ruff max-complexity 16; pure relocation (no redesign, no fixing of pre-existing quirks: list them as non-goals). Spending/budget data are security-adjacent (user and project scoping, admin vs user views): note any access-control or visibility logic among the candidate helpers; if such logic is present the eventual MR needs `/sanity`. Follow .ai-run/guides (architecture/layered-architecture.md, service-layer-patterns.md, project-structure.md, security-patterns.md). Ticket body and criteria: docs/superpowers/tasks/2026-10-08-epmcdme-15651-split-router-modules/{ticket,spec}.md; step 4 (analytics package) records: docs/superpowers/tasks/2026-10-09-epmcdme-15651-step4-analytics-package/.
Research must report, with path:line evidence: (1) the exact helper set to move and every caller (routers, services, handlers, tests, sibling repos /home/taras_spashchenko/EPAM/cm/codemie-enterprise etc.); (2) existing service/analytics/ contents and naming conventions (esp. response_formatter.py, totals_calculator.py: is there a natural home, or would a new module be right); (3) every test file and mock.patch literal that targets the helpers' current module paths, and which would go dark (silently pass) if mis-aimed; (4) whether any helper depends on router-only things (HTTPException, Request, Depends, router constants); (5) circular-import risks; (6) behaviour that must be preserved byte-for-byte and its existing test coverage; (7) whether acceptance criterion 5 (no service/ or handlers/ import of routers/ except routers/utils.py) holds today.

---

## 2. Codebase Findings

Tools: Read, Glob, codegraph. No grep or shell; nothing was run. Counts come from reading; tree-wide absence is unproven unless a source is named. Paths are under `src/codemie/` unless prefixed `tests/`, `docs/` or a repo name. `R` = `rest_api/routers/analytics`.

### Existing Implementations

Candidates, all in `R/_spending_support.py` (324 lines) except the last row. Only `R/spending.py` imports from it (`spending.py:40-45`); the other 8 route modules import only from `_common` (import blocks read).

| Helper (`_spending_support.py`) | Status | Callers | Router-only deps | Direct tests |
|---|---|---|---|---|
| `_SPENDING_METRIC_DEFINITIONS` :35-52 | live | `_build_spending_metric` :81 | none | none |
| `_build_spending_metric` :67-93 | live | `spending.py:117,129`; `_build_spending_metrics` :108-138 | none | none |
| `_build_spending_metrics` :96-139 | live | `spending.py:110` | `logger` | handler only |
| `_calculate_time_until_reset` :142-175 | live | `_build_spending_metrics` :121; dead `_build_spending_row` :235 | `logger`; function-local `timezone` import :155 | none |
| `_paginate_tabular` :300-324 | live | `spending.py:303,340` | `ResponseFormatter` (service module) | handler only |
| `_authorize_admin_budget_view` :283-297 | live, ACCESS CONTROL | `spending.py:177,298,335` | `ExtendedHTTPException`, `fastapi.status`, `ERROR_MSG_ACCESS_DENIED`/`ERROR_MSG_ADMIN_HELP` (`_common.py:43-44`, imported :24-27) | 9 refs (section 4) |
| `_get_user_all_projects` :55-64 | dead | none found | `User` annotation | none |
| `_get_key_spending_columns` :178-223 | dead | `_build_key_spending_tabular_data` :280 | none | none |
| `_build_spending_row` :226-238 | dead | `_build_key_spending_tabular_data` :274,278 | none | none |
| `_build_key_spending_tabular_data` :241-280 | dead | none found | none | none |
| `spending.py::_resolve_admin_budget_subject` :152-186 | live | `spending.py:227` | `config.ENABLE_USER_MANAGEMENT`, DB lookup, 400/404/422 HTTP errors; calls the gate :177 | handler only |

- "Dead" rests on: no import in the 9 route modules; the step-4 patch map (`local/router-split/step4/patch-map.txt`, every BASE test reference to `routers.analytics`) whose 8 `_spending_support` rows are all `_authorize_admin_budget_view`; step-4 `spec.md:30` (no other importer in `src/`, `tests/`, `.ai-run/`, sibling repos). Same-named hits in tests belong to the service twins below.
- Service-side twins in `service/analytics/handlers/budget_usage_service.py`: `_get_key_spending_columns` :77-122 (same six columns), `_build_spending_row` :148-160 (same body), `_calculate_time_until_reset` :125-145 (DIVERGES: naive timestamp taken as UTC :132-133, `<= 0` gives "Expired" :135-136; the router one gives "0 days 0 hours 0 mins" for `< 0` :162-163 and raises TypeError on a naive timestamp, as only `(ValueError, AttributeError)` is caught :173). `/budget_usage` uses the twins (`spending.py:241`), hence tests import `_get_key_spending_columns` from `budget_usage_service` (`test_analytics.py:1355`, `test_analytics_budget_usage_labels.py:26,74`). A third, different twin: `routers/projects/_spending_support.py:73-87` `_format_time_until_reset` (7c).
- `_common.py` has no spending formatter: `handle_analytics_errors` :47-94, `_create_response` :97-107, param classes :110-132, constants :36-44 (`PROJECTS_FILTER_ADMIN_DESC` :36, `ERROR_MSG_PROJECT_EMPTY` :42 unreferenced).
- Stays in the handler: `get_user_spending` `spending.py:67-149` (inline proxy/premium appends :112-137, metadata dict :139-147). Metric ids `cli_current_spending`/`premium_current_spending` are absent from `_SPENDING_METRIC_DEFINITIONS` (:35-52), so label falls back to the id and description to "" (:81-87).
- Input shape: `codemie_enterprise/litellm/service.py:1251-1284` `get_customer_spending` returns `customer_id`, `total_spend`, `max_budget`, `budget_duration`, `budget_reset_at`. A codegraph query in codemie-enterprise for the helper names found no consumer.

### Architecture and Layers Affected

Router layer (`R/spending.py`, `R/_spending_support.py`, `R/_common.py`); service layer (`service/analytics/`); tests (`tests/codemie/rest_api/routers/`, `tests/enterprise/litellm/`, `tests/codemie/service/analytics/`); `rest_api/security/user.py` (`resolve_is_admin` :59-77: ENV=local forces admin, `is_maintainer` implies admin).

### Integration Points

`spending.py` already imports `AnalyticsService` (:50) and `member_spend_service` (:51), so `service/analytics/__init__.py:23` is on the router's import path. `ResponseFormatter` (`service/analytics/response_formatter.py:66`) is used only by `_paginate_tabular` (`_spending_support.py:29,315`).

### Patterns and Conventions

`service/analytics/` top level: `analytics_service.py` (facade), `response_formatter.py` (`ResponseFormatter` statics + TypedDicts `Metric` :45-53, `Column` :56-63 matching the helpers' output shapes), `totals_calculator.py` (`TotalsCalculator` statics), `time_parser.py`, `access_filter.py`, `query_builder.py`, `query_pipeline.py`, `aggregation_builder.py`, `metric_names.py`, `delivery_framework.py` (module functions, one public + private helpers), `cli_analytics_jobs.py`; `handlers/` has `*_handler.py`, `budget_usage_service.py`, `member_spend_service.py` (module functions + class + singleton :351). Naming `<noun>_<role>.py`; tests flat at `tests/codemie/service/analytics/test_<module>.py`. 7a precedent: module functions, underscore dropped, `from __future__ import annotations`, annotation-only imports under `TYPE_CHECKING`, 13-line Apache header, function-local imports kept.

---

## 3. Documentation Findings

### Guides and Architecture Docs

Read: `architecture/{layered-architecture,service-layer-patterns,project-structure}.md`, `development/{security-patterns,error-handling}.md` (all short). `service-layer-patterns.md:5`: "Keep request parsing and response formatting outside service methods" (in tension with the ticket wording; `response_formatter.py` already sits in `service/analytics/`). `security-patterns.md:5-12`: central role helpers over inline checks. `error-handling.md:5-12`: `ExtendedHTTPException` is a shared typed exception.

### Architectural Decisions

Ticket `ticket.md:34,39,67,70`; step-4 `spec.md:28` (underscore names only from private modules), `:59` (step-7 move and dead-code deletion are non-goals); `HANDOFF.md:142`.

### Derived Conventions

7a `spec.md:9-16,26-35`: rename map, public names, no re-export, flip check lists inert patches, guards byte-identical.

---

## 4. Testing Landscape

### Existing Coverage

- Gate `_authorize_admin_budget_view`: `tests/codemie/rest_api/routers/test_analytics.py:1646-1705` (6 tests; imports from `_spending_support` :1650,1657,1664,1672,1684,1696); `test_analytics_auditor.py:38-62` (imports :40,:49); `test_analytics_capability_guards.py:285` (`patch("codemie.rest_api.routers.analytics.spending._authorize_admin_budget_view")`); handler paths `test_analytics_member_spending.py:188-214,288-306,350-407`.
- Metric helpers only through `get_user_spending`: `test_analytics.py:1176-1337` (past reset date "2026-03-01T00:00:00Z" :1190 hits the expired branch but asserts type only :1228-1229; no-data N/A :1262-1273; +10 days asserts substrings :1304-1309; null reset :1335-1337); `tests/enterprise/litellm/test_premium_models_budget.py:495-612` (metric ids only).
- `_paginate_tabular` through `test_analytics_member_spending.py:140-155,236-269` (total_count, has_more, slicing).
- Service twins: `tests/codemie/service/analytics/handlers/test_budget_usage_service.py:30-115`.

### Testing Framework and Patterns

pytest, `pytest.mark.asyncio`/`anyio`, `unittest.mock`; handlers called directly with `user=`; function-local imports of the code under test. Run: `poetry run pytest <path> -n 0 --show-capture=no -q` (HANDOFF `:18`); guards in `tests/codemie/rest_api/router_guards/`.

### Coverage Gaps

Nothing pins: metric label/description text, `format` key rule, the id-as-label fallback, empty-dict-as-unavailable (`:105`), `budget_limit=None` with data, the "X days Y hours Z mins" arithmetic, the expired string, invalid-timestamp `None`, naive-timestamp TypeError, `logger.info` lines, `_paginate_tabular` pass-through (`totals=None`, `filters_applied`). Patch literals that pass when mis-aimed: `capability_guards.py:285` (already PASS in the step-4 flip, HANDOFF `:166`). Loud when mis-aimed: the 8 gate imports (ImportError); the 3 `spending.get_async_session` literals (`capability_guards.py:271,291`, `member_spending.py:337`, NO-SIBLING). Stable while `spending.py` keeps `import asyncio`: the 9 `spending.asyncio.to_thread` literals (`test_analytics.py:1552,1576,1600,1631`, `member_spending.py:77`, `test_premium_models_budget.py:513,547,574,602`), which patch the shared `asyncio` module.

---

## 5. Configuration and Environment

### Environment Variables

The helpers read none. Adjacent: `config.ENABLE_USER_MANAGEMENT` (`spending.py:154`), `config.ANALYTICS_DEFAULT_PAGE_SIZE` (`spending.py:280,330`, `_common.py:132`), `config.is_local` (`_common.py:102`), `ENV`/`ADMIN_USER_ID`/`ADMIN_ROLE_NAME` (`user.py:67-72`).

### Configuration Files

Ruff max-complexity 16 and the license-header check run in `make ruff` (ticket AC 6; pyproject not read).

### Feature Flags and Deployment Concerns

`is_premium_models_enabled()` (function-local import `spending.py:89-94`, used :122) gates the premium metric in the handler. No manifest or secret involved.

---

## 6. Risk Indicators

- Dead-chain coupling (fact): dead `_build_spending_row` :235 calls live `_calculate_time_until_reset`. Speculative: moving the live function while the dead chain stays leaves an undefined name (ruff F821); the spec must choose keep-dead-importing-from-service, move-dead-with-it, or delete-dead (contradicts "dead code out of scope").
- Access control (fact): `_authorize_admin_budget_view` is the only access logic among the candidates (admin or auditor pass; else intersection of `admin_project_names` with target projects; else 403; maintainers are admins via `user.py:74-75`). It is not formatting. Its two constants are shared router-package strings (also `spending.py:292-294`, `leaderboard.py:28-29`). Speculative: a service copy cannot import them from `routers.analytics._common` (package `__init__` :25-35 imports `spending`, which would import the service module: a cycle and an AC 5 violation); options are leaving the gate in the router or duplicating two strings. If it moves, `/sanity` is needed for this reason; the single eventual MR already needs `/sanity` and `## Test harness` (HANDOFF `:402`). The other candidates do no scoping (scoping is `user.username` in the handler, `spending.py:101,110`).
- Twin trap (fact): three divergent time-until-reset functions (router :142, `budget_usage_service.py:125`, `projects/_spending_support.py:73`). Speculative: merging them changes `/spending` text for expired or naive timestamps.
- Name collisions (fact): `_calculate_time_until_reset`, `_get_key_spending_columns`, `_build_spending_row` also exist in `budget_usage_service`; a whole-word "old name gone" grep must be path-qualified.
- Quirks to list as non-goals (by inspection, not run): naive timestamp -> TypeError -> 500 through `handle_analytics_errors`; expired text differs from the twin; id-as-label fallback; empty dict treated as unavailable; redundant function-local `timezone` import :155 beside module-level :18; unknown target user gets 404 before authorization (`spending.py:171-176`); per-user `/spending` is `Cache-Control: public` (`_common.py:105`); `_build_spending_metrics` docstring omits `user_id`.
- Verification tooling (fact): `local/router-split/step7/bodycheck.py` compares functions only; `_SPENDING_METRIC_DEFINITIONS` is an assignment.
- Placement (fact): `test_response_formatter.py:56` patches `response_formatter.datetime`. Speculative: a new sibling module (for example `spending_formatter.py`) avoids editing a stable module; `delivery_framework.py` shows module functions at this level.
- Import weight (fact): any `codemie.service.analytics.<x>` import runs `__init__.py:23` (loads `AnalyticsService` and handlers); check with `python -I`.
- AC 5 today (partly unverified): HANDOFF `:408` (post-7a) records `routers` imports in `service/` = 3 and `rest_api/handlers/` = 1, all `routers.utils`; 7a `technical-analysis.md:54` shows the ticket's three offenders gone; I read the heads of `service/analytics/{response_formatter,totals_calculator,time_parser,access_filter,delivery_framework,metric_names}.py`, `handlers/{budget_usage_service,member_spend_service}.py`, both `__init__` files: no `routers` import. No fresh repo-wide scan was possible; re-run it.
- Siblings (unverified): no hit in codemie-enterprise (semantic query only); `codemie-mcp-connect-service` not indexed; step-4 `spec.md:30` says none. No `caplog` search; no test patches `_spending_support.logger` (patch map).

---

## 7. Summary for Complexity Assessment

The live surface is small and local: one constant and four pure functions (`_build_spending_metric`, `_build_spending_metrics`, `_calculate_time_until_reset`, `_paginate_tabular`) plus one access-control gate, in a 324-line file whose only importer is `R/spending.py` (4 imported names, 8 call sites). Layers: router (imports), service (`service/analytics/`, where module-function style already exists), tests (8 import lines in 2 files and 1 patch literal for the gate). No route, signature, OpenAPI or precedence input is involved. The formatting helpers need no router-only object; only the gate pulls two router constants and HTTP-flavoured exceptions.

Novelty is low (7a is the template, scratch tooling exists in `local/router-split/step7/`), but the decisions are not trivial: dead code that calls a live helper, three divergent time functions plus same-named service twins, and whether the gate counts as "formatting". Today the formatting helpers are covered only indirectly (handler level, substring asserts), so nothing existing would catch a change in label text or arithmetic. The gate itself has 9 references, one of them an already-inert patch.

Key risks: scope drift into de-duplication, the dead-chain undefined name, verification greps hitting the twins, the inert patch, unverified tree-wide AC 5 and sibling absence (no grep tool), and the `/sanity` obligation if the gate moves.

---

## 8. External References

All resolved and read.
- 7a `spec.md` (all), `plan.md` (1-80), `technical-analysis.md` (1-60): module functions; bodies unchanged; underscore-to-public rename map; characterization tests green at BASE first; `ast.dump` body check with `--negative`; retargeted literals plus flip check; guards byte-identical, `regenerate_baselines` never; `python -I` import per module; AT = `pytest -n 0 --show-capture=no -q`.
- `local/router-split/HANDOFF.md` 1-243 and 340-412 (steps 5-6 detail not read): `:142` dead code and "not moved" note; `:164-171` step-4 flip (12 silent patches incl. `capability_guards.py:285`); `:402-405` `/sanity` + `## Test harness`; scratch tools at `local/router-split/step7/{extract,bodycheck,flip,step7_common}.py`.
- `ticket.md` and umbrella `spec.md` (all): `ticket.md:39` the analytics sentence, `:67` AC 5, `:70` `/sanity`; `spec.md:42-51` resolved criteria.
- Step-4 `spec.md` (1-69): `_spending_support` scope :20, naming rule :28, no-importer :30, patch rules :31-35, non-goals :56-61; `step4/patch-map.txt` (all 141 rows).
