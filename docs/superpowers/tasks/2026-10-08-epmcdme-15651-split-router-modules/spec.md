# EPMCDME-15651, step 0: router guard tests

## Purpose

EPMCDME-15651 splits five oversized routers (`assistant`, `analytics`, `index`, `projects`, `conversation`) into packages with no API change. No test snapshots the OpenAPI schema or the route registration order, so a split could change either unnoticed. Step 0 adds both guards and changes no production code. Every later step is judged against them.

## Scope

Delivery step 0 only, on the current branch. Steps 1 to 7 are a follow-up, each its own branch and MR. Baseline for both guards: base commit `e55bea3005`.

## Behavior

**OpenAPI snapshot.** A committed JSON file holds `app.openapi()` from `e55bea3005` (sorted keys, stable indentation). The test compares the live schema in full: paths, operationIds, tags (including the extra `Assistant Versioning` tag), request/response schemas, components. Comparison is strictly byte-identical and failures show a readable diff. A failing run never rewrites the snapshot. Regeneration is an explicit, documented manual action.

**Route precedence.** From the live `app.routes`, compute every pair of routes that share an HTTP method and whose paths can match the same URL. The matcher models `{x}` and `{x:path}` converters. Pairs where the generic route already comes first (a conversation folder named `history`) are recorded as they are, not fixed. The ordered pairs, identified by method and path template, are committed as the baseline. The test fails if a baseline pair's relative order changes or a baseline route is missing. New overlapping pairs are reported, not failed.

**Cross-check.** The computed baseline must contain all 16 pairs the technical analysis reproduced by hand (`technical-analysis.md` section 2, "Route-order pairs"). A miss means the matcher or the analysis list is wrong and is resolved before commit. The computed total goes in the MR. The ticket's "40" is not a requirement.

**App construction.** `main.py` decides conditional includes at import: Elasticsearch configured (L1035), LiteLLM enabled plus `LLM_PROXY_BUDGET_CHECK_ENABLED` (L1079), `ENABLE_USER_MANAGEMENT` plus `IDP_PROVIDER == "local"` for local auth (L1092-1097). The guard builds the app with all of these on so every conditional router is covered, and with `WORKFLOW_GENERATION_ENABLED` on if the same mechanism allows. With Elasticsearch configured, `main.py` L1027-1028 runs `StateImportService().import_indexes()` at import. The guard must neutralize it so no real service is contacted. If flags-on is infeasible, fall back to the default env (`tests/.env.test`) and record the uncovered routers in the test docstring and MR. The profile is fixed in code, independent of the developer's environment.

## Acceptance criteria (step 0)

1. The snapshot test passes on the unchanged base tree and fails when an operationId, tag, path or component changes.
2. A failing run never rewrites the snapshot.
3. The precedence test passes on the unchanged base tree and fails when two overlapping routes swap order. Both failure modes are shown once with a local, reverted mutation.
4. The baseline contains all 16 hand-reproduced pairs and the computed total is stated in the MR.
5. Conditional routers (index, budget, user-management, local auth) are in the guarded app, or the gap is recorded.
6. Building the app contacts no network, database or Elasticsearch.
7. Nothing under `src/` changes. Only new tests and committed fixtures are added. New `.py` files carry the Apache header. `make ruff` and the new tests pass.

## Non-goals

- Any change to `src/`, `main.py` registration, endpoint behavior, paths or response shapes.
- Splitting routers, moving models, or moving helpers into services (steps 1 to 7).
- Retargeting `mock.patch` references or editing existing tests.
- Fixing route pairs already shadowed by a generic route.
- Runtime behavior tests, including auth enforcement. Step 0 guards schema and order only.
- Second-priority files (`project_budget_router`, `budget_router`, `workflow_executions`, `skill`, `files`) and the analytics route factory.
- Shortening the 25+ handlers over about 60 lines (separate follow-up).
- Fixing stale `main.py` line references in the guides, and git side effects beyond the flow's own.

## Ticket criteria as resolved (apply to steps 1 to 7)

1. The module-size target (about 500 lines) binds. Handler length (about 60 lines) is soft, for new or touched code only.
2. OpenAPI stays identical, except that step 1 may regenerate the snapshot once. The reviewed diff is limited to the renames of the four `PaginationInfo` components (`budget_router`, `projects`, `cost_centers`, `models/user_management`).
3. The baseline is the computed pair set, `{x:path}` included, not the number 40.
4. "Export only `router`, no re-exports" applies to the five target packages only. `ide.py` and other callers are re-pointed to submodules, and tests import from submodule paths. Two-router modules (`llm_models`, `project_budget_router`) are outside the rule.
5. Step 2 moves `_get_assistant_by_id_or_raise` and `_check_user_can_access_assistant` to `routers/utils.py` as public names. Step 1 removes the service and handler imports of router classes.
6. Covers every test that imports or patches router modules: `tests/codemie/rest_api/routers`, `tests/unit/routers`, `test_xwiki_index_endpoints.py`, `test_main_retrieval_backend_disabled.py`, `tests/codemie/service/budget/*`. Ruff max-complexity stays 16.
7. Unchanged: split MRs are move-only, reviewable with `git diff --color-moved`.
8. `/sanity` is requested in any MR that moves auth, access-check or authorization code (steps 2, 4, 5, 7).

## Follow-up sequence (one branch and MR each, guards pass unchanged)

Step 1 models move (M, 1.5 d); 2 `assistant/` (L, 2.5-3 d); 3 `index/` (L, 2 d); 4 `analytics/` (M, 1-1.5 d); 5 `projects/` plus a fourth shared module (M, 1.5 d); 6 `conversation/` (M, 1-1.5 d); 7 helpers to services, one MR per package (L, about 6 d). Step 0 is S, 1 d. Total is about 17 engineer-days. Shared-module contents per package are in the technical analysis, section 2.

## Open risks

- Matcher fidelity for `{x:path}` is the main correctness risk. The 16-pair cross-check mitigates it but cannot prove completeness.
- Flags-on may need patched import-time behavior. If it cannot be hermetic, the fallback leaves some routers unguarded (recorded, not hidden).
- The full OpenAPI snapshot is large and will churn in step 1. Accepted, because its diff is reviewable.
