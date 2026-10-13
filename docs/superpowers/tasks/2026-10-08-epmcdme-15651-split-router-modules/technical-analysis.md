# Technical Research

**Task**: rest-api routers fastapi refactor
**Generated**: 2026-10-08
**Research path**: codegraph

---

## 1. Original Context

Implement Jira ticket EPMCDME-15651, "Split oversized REST API router modules into smaller per-concern modules". The verbatim ticket body (context, approach, target split per package, 8-step delivery plan, risks, acceptance criteria, out of scope) is in /tmp/claude-1000/-home-taras-spashchenko-EPAM-cm-codemie/ba411522-5ecf-42ee-a650-c0156020a145/scratchpad/jira_story.md — read it in full first; it is the requirements. Clarifications agreed when the ticket was created: (1) no epic; (2) the numbered delivery plan wins: request/response classes move to rest_api/models/ BEFORE the router splits (step 1), domain helpers move into services AFTER them (step 7); (3) the services' imports from budget_router.py / project_budget_router.py are only Pydantic request/result classes (BudgetCreateRequest, BudgetUpdateRequest, BudgetSyncResult, BudgetAssignmentBackfillResult; ProjectBudgetCreateRequest, ProjectBudgetUpdateRequest, ProjectBudgetGroupCreateRequest, ProjectBudgetGroupUpdateRequest), so step 1 removes those imports without splitting the budget routers.
Prior analysis (static, not verified by running anything): src/codemie/rest_api/routers has 62 modules / 26,061 lines; assistant.py 3214, analytics.py 3113, index.py 2884, projects.py 1483, conversation.py 1187. 40 candidate route pairs depend on registration order. mock.patch references into the current modules: assistant 135 (11 test files), index 168 (5), projects 265 (2), workflow_executions 132 (6), conversation 79 (5), analytics 55 (8). Please verify or correct these numbers rather than trusting them.
feature_area: rest-api routers fastapi refactor
run_dir: /home/taras_spashchenko/EPAM/cm/codemie/docs/superpowers/tasks/2026-10-08-epmcdme-15651-split-router-modules
Write technical-analysis.md to run_dir with its Codebase Findings and Risk Indicators sections, and return your digest. Make Section 7 (and Section 6) say plainly whether the whole story fits one branch or should be delivered as its delivery-plan steps, with a per-step size estimate.

Follow-up from the coordinator (the scratchpad file was deleted; the ticket is now `ticket.md` in run_dir):
1. Target split per package (assistant, analytics, index, projects, conversation) and the second-priority files: does each proposed module boundary work against the real call graph, shared helpers and module-level singletons? Name any helper that would need to be shared across the new modules.
2. Delivery steps 0 to 8 each get a size estimate, and say plainly whether step 0 (guard tests) and step 1 (models move) can stand alone as one branch.
3. Acceptance criteria 1 to 8: say which are already true, which are measurable now, and which conflict with what you found (for example the 'export only router' rule against tests that import private helpers, the two-routers-per-module cases, and the duplicate PaginationInfo component names).
4. Re-check the '40 candidate pairs' and the patch counts if a cheap method exists; otherwise say they stay unverified.
Rewrite Section 7 and Section 6 to match, with a recommendation on which single delivery step this run should implement.

---

## 2. Codebase Findings

Tool limits: Read, Glob, codegraph only (no grep, no shell, nothing executed). Line counts come from Read's end-of-file report (can be +1 versus `wc -l`).

### Existing Implementations

`src/codemie/rest_api/routers/`: 62 modules plus a 1-line `__init__.py`, 26,372 lines measured (ticket 26,061). The tree is ahead of the ticket's numbers: `projects.py` has 1 route and 2 classes more (consistent with the allowed-models route and its two classes), `files.py` is 437 lines (ticket 580), `workflow.py` 946 (847), `workflow_executions.py` 951 (921).

| Module | Lines (ticket) | Routes | Local request/response classes | Notes |
|---|---|---|---|---|
| `assistant.py` | 3,297 (3,214) | 36, matches | 7 | about 1.7k lines are private helpers |
| `analytics.py` | 3,114 (3,113) | 85, matches | 4 param classes | 57 routes by L2130, 28 after |
| `index.py` | 2,885 (2,884) | 44 (45) | 2 (about 30 more already in `models/index.py`) | 17 helpers, 13 message constants |
| `projects.py` | 1,539 (1,483) | 14 (13) | 31 (29) | about 27 spending/budget helpers |
| `conversation.py` | 1,244 (1,187) | 29, matches | 0 | |
| `project_budget_router.py` | 787 | many | 8 before helpers (L60-146) plus group classes | `router` and `group_router` |
| `budget_router.py` | 249 | 7 | 7 | |

`rest_api/models/` has 55 flat files; none for project, budget or cost-center. `PaginationInfo` is defined four times: `budget_router.py` L114, `projects.py` L180, `cost_centers.py` L40, `models/user_management.py` L376.

### Target split against the real call graph

All five target files were read in full (analytics L1-3114 included).

| Package | Helpers/singletons needing sharing across new modules | Routes with no named home / size flags |
|---|---|---|
| assistant | `_get_assistant_by_id_or_raise`, `_check_user_can_access_assistant`, `_validate_remote_entities_and_raise`, `_track_assistant_management_metric`, `_raise_assistant_operation_error`, `_get_assistant_by_slug_or_raise` (catalog + execution), `_index_marketplace_assistant` (management update + marketplace publish), `_get_masked_prompt_variables` and `_mask_sensitive_prompt_variables` (catalog + marketplace). The ticket's `_shared` omits the last four. Error builders (`_create_assistant_error`, `_save_error`) are execution-only. No module-level singletons. | reactions (2), `health`, `context`, `mcp/test`, `system-prompt/validate`. marketplace about 0.9k lines, execution about 0.8k until step 7. |
| index | `_index_unique_check`, `_kb_demo_user_check`, `_validate_project_change`, `_update_datasource_scheduler`, `_validate_git_credentials` (code_repositories + kb_git_faq), 13 message constants. Single-owner: `_get_index_by_id_or_raise`, `_get_elasticsearch_stats` (queries), `_parse_jwt_exp` (sharepoint). No singletons. | `/index/health`, `/index/jira/fields`, `DELETE /index/{id}`, `GET /application/{app}`. code_repositories about 0.54k. |
| analytics | `_common`: `handle_analytics_errors`, `_create_response`, the two param classes, filter-description constants, module `logger`. Single-owner: `_ensure_routing_analytics_enabled`, `_require_leaderboard_enabled`, `_run_enriched_user_insight`, `_authorize_admin_budget_view`, `_paginate_tabular`. `_build_key_spending_tabular_data` (with callees `_build_spending_row`, `_get_key_spending_columns`) and `_get_user_all_projects` have no caller in the file. | None. cli_insights about 0.5k. Cleanest boundary. |
| projects | A fourth module is needed: `_ensure_user_management_enabled` (every module), `_raise_project_not_found` + `logger`, `_authorize_project_access` (crud DELETE + all membership). crud imports spending (`_attach_*`, `_is_budget_provider_active`, `_can_see_project_spending`, `_build_project_detail_response`). `_spend_repo` is spending-only. | spending about 0.55k before step 7. File uses `from __future__ import annotations`. |
| conversation | `CONVERSATION_NOT_FOUND_MSG` (abort, stream, export); `_use_global_models_for_conversation` crud-only; `conversation_monitoring_service` singleton (L102) has no use in the file. | export (2 routes + `_get_streaming_response`), admin (2), assistant-folders (2), `speech-recognition`. |

Second priority: `project_budget_router.py` defines `_raise_access_denied`, `_can_read_project_budget` and `_ensure_*` at module level (use by the group routes was not read); `ProjectBudgetGroupCreateRequest` drags `CategoryBudgetSpec` and `_DURATION_PATTERN` into step 1; `_ensure_allowed_budget_group_update_fields` uses `ProjectBudgetGroupUpdateRequest` at runtime. `budget_router.py` needs no split. `workflow_executions.py` imports the private `_handle_streaming_execution` from `routers/utils.py` and owns `_marketplace_service`. `skill.py` and `files.py` were not read.

### Architecture and Layers Affected

API routers (`rest_api/routers`), request/response models (`rest_api/models`), services (`service/budget`, later `service/assistant|index|analytics|project`), `rest_api/handlers`, and tests.

### Integration Points

- `main.py` L61-134 imports router modules as module objects; `include_router(<module>.router)` runs at L1030-1100. Conditional: `index` (ES configured, L1035), `budget_router` (LiteLLM and `LLM_PROXY_BUDGET_CHECK_ENABLED`, L1079), user-management group (L1092). `llm_models` and `project_budget_router` register two routers each (L1052-1053, L1081-1082).
- Layer violations (criterion 5), all verified: `budget_service.py` imports 4 classes under `TYPE_CHECKING` (L51-56) and lazily (L664); `project_budget_service.py` 4 classes under `TYPE_CHECKING` (L59-65); `assistant_handlers.py` `ToolCallResumeRequest` under `TYPE_CHECKING` (L26-27). Router to router: `assistant_mapping.py` L31 `_get_assistant_by_id_or_raise`; `assistant_prompt_variable_mapping.py` L28 that plus `_check_user_can_access_assistant`; `ide.py` L30-31 `ask_assistant_by_id`, `_get_assistant_by_id_or_raise`, `get_request_handler` (a name re-exposed through assistant's namespace) and `conversation.create_conversation`. Other importers were not enumerated.

### Patterns and Conventions

Router-level `Depends(authenticate)` on index, conversation, analytics; `prefix="/v1"` with full paths in decorators; four routes carry the extra tag `Assistant Versioning` (L2995-3133); `workflow.py` L871 defines routes only `if config.WORKFLOW_GENERATION_ENABLED`; `analytics.py` and `projects.py` use `logging.getLogger(__name__)`, so log record names change when code moves; function-local imports guard cycles (`assistant.py` L2053).

#### Route-order pairs reproduced by reading (16)

| Module | Earlier route | Later route | Kind |
|---|---|---|---|
| assistant | `POST /assistants/virtual/model` | `POST /assistants/{assistant_id}/model` | plain |
| assistant | GET `id/{x}`, `prebuilt/{slug}`, `metrics/{cid}` (3); GET `slug/{path}` (3) | GET `{id}/versions`; `slug/{path}` also vs `{id}/versions/{n}` and `.../compare/{v2}` | literal or `:path` collision (6) |
| index | `GET /index/users`, `/index/find_id` | `GET /index/{index_id}` | plain (2) |
| conversation | `GET /conversations/search`, `/new` | `GET /conversations/{conversation_id}` | plain (2) |
| conversation | `PUT` and `DELETE` `{id}/history`, `{id}/history/{i}` (4) | `.../folder/{folder:path}` | `:path` collision; generic first, so a folder named `history` is already shadowed |
| conversation | `PUT .../folder/{folder:path}` | `PUT {id}/messages/{i}/output` | `:path` collision (1) |

`projects.py` and `analytics.py`: no overlapping pair. The ticket's order (crud, history, streaming, folders, messages) keeps the conversation pairs intact.

### Acceptance criteria against the tree

| # | Already true | Measurable now | Conflicts found |
|---|---|---|---|
| 1 | No | Yes (line counts, handler spans) | At least 25 handlers exceed 60 lines (13 in `index.py`, 5 in assistant) versus move-only (7); assistant marketplace and execution exceed 500 until step 7 |
| 2 | No test exists | After step 0 | Step 1 renames OpenAPI components of same-named classes (`PaginationInfo` x4); `.env.test` omits user-management routes and probably `budget_router`; extra route tag must survive |
| 3 | No test exists | After step 0; 16 pairs reproduced, "40" not | Test must model `{x:path}`; some pairs are generic-first (kept, not fixed) |
| 4 | `main.py` imports modules and `.router`, which works with packages | Yes | `ide.py` needs 4 names re-pointed; two-router modules cannot "export only router" if split; tests importing handlers/privates must use submodule paths (ImportError is loud) |
| 5 | No (3 type-only service/handler imports) | Partly; full list needs grep | 4 private names imported by 3 routers; an `assistant/_shared` module is still another router's private module; fix needs `routers/utils.py` or a service, yet services arrive in step 7 |
| 6 | Ruff `max-complexity = 16` set (pyproject L246-248) | `make ruff` and router tests, not run | Scope omits tests elsewhere that import routers; patches must follow each name's new owner, so retargets are not 1:1 |
| 7 | n/a | `git diff --color-moved` | New `__init__`/`_shared` files, import rewrites and test edits are not moved code |
| 8 | n/a (process) | MR text | Security-relevant moves are also in steps 2, 4, 5 (access check, auth dependencies, `_authorize_*`), not only step 7 |

---

## 3. Documentation Findings

### Guides and Architecture Docs

Read `layered-architecture.md`, `rest-api-patterns.md`, `endpoint-conventions.md`, `project-structure.md`, `quality-gates.md`, `git-workflow.md`, `testing-patterns.md`, `testing-api-patterns.md`. Rules: routers stay thin, schemas live in `rest_api/models/`, shared helpers go to `rest_api/utils/` or services, default merge is squash, commit format `EPMCDME-15651: Capital description`. Stale: guides cite `main.py:658/629/706/804`; the include block is at L1030-1100. `test_main_retrieval_backend_disabled.py` cites `main.py:894/902`, now L1028/1036.

### Architectural Decisions

No ADR on router packaging or module size. The ticket is the only decision record.

### Derived Conventions

Flat `routers/<resource>.py`; no package-style router exists today.

---

## 4. Testing Landscape

### Existing Coverage

- Router tests: 113 files in `tests/codemie/rest_api/routers/`, 2 in `tests/unit/routers/`. Largest: `test_projects_router.py` 3,868 lines, `test_analytics.py` 1,697, `test_index.py` 1,479, `test_assistant.py` 1,350, `test_workflow_executions.py` 1,084, `test_conversation.py` 865.
- Files per router by name: assistant at least 13 (ticket 11), index 4 (5), projects 2 (2), conversation 3-4 (5), analytics 7 (8), workflow_executions 7 (6).
- Outside the criterion 6 directory but tied to router modules: `tests/unit/routers/*`, `tests/codemie/rest_api/test_xwiki_index_endpoints.py`, `test_main_retrieval_backend_disabled.py` (reads `index.router.routes`), `tests/codemie/service/budget/*` (import router classes).
- No test snapshots the route table or `openapi.json`.

### Testing Framework and Patterns

pytest; `tests/.env.test` pins `ENABLE_USER_MANAGEMENT=false` and `LLM_PROXY_ENABLED=false`, and `tests/conftest.py` mocks the Postgres engine. Tests use `@patch("codemie.rest_api.routers.<module>.<name>")`, import private helpers and handlers directly (`test_projects_router.py`: 23 names; `test_assistant.py`: `_ask_assistant`, `_ask_virtual_assistant`) and call handlers as plain functions. After a split a patch must target the module that uses the name, not the module of the function under test: `test_assistant.py` patches `assistant.Ability` for `_ask_assistant`, but `Ability` is used inside `_check_user_can_access_assistant`, which would live in `_shared`.

### Coverage Gaps

Patch counts (135/168/265/132/79/55) and the "40 pairs" total are unverified: counting needs grep, and the pair computation needs a running app. Re-measure with `grep -rc "rest_api.routers.<module>" tests/`.

---

## 5. Configuration and Environment

### Environment Variables

`WORKFLOW_GENERATION_ENABLED` (import-time route gating), `ENABLE_USER_MANAGEMENT`, `IDP_PROVIDER`, `LLM_PROXY_BUDGET_CHECK_ENABLED`, `RETRIEVAL_BACKEND`, `ANALYTICS_DEFAULT_PAGE_SIZE` (a `Query` default evaluated at import).

### Configuration Files

`Makefile` (`ruff`, `test`, `test-harness`, `license-check`, `verify`); `pyproject.toml` ruff line-length 120 and mccabe 16; `tests/.env.test`. New `.py` files need the Apache header.

### Feature Flags and Deployment Concerns

No pipeline. `.gitlab/merge_request_templates/Default.md` requires a pasted `make test-harness` block and a `/sanity` request for security-relevant code. Customer-config flags read by routers: `routingAnalytics`, `userEnrichmentEnabled`, `subWorkflow`.

---

## 6. Risk Indicators

- `PaginationInfo` is defined four times (budget_router, projects, cost_centers, models/user_management); moving two renames their OpenAPI components, so step 1 must re-baseline the snapshot (criterion 2).
- `ide.py` imports `ask_assistant_by_id`, `get_request_handler`, `create_conversation` and a private helper from router modules; two more routers import assistant privates; `project_budget_router` and `llm_models` export two routers each (criteria 4-5).
- At least 25 handlers exceed 60 lines (13 in `index.py`); move-only MRs cannot shorten them (criteria 1, 7).
- Criterion 6 omits tests outside `tests/codemie/rest_api/routers/`; patches must follow each name's new owner (`Ability`, `config`, `logger`, `AnalyticsService`), so retargets are not 1:1.
- 16 overlapping route pairs reproduced by hand (5 plain, 11 literal or `{x:path}` collisions); the 40 total and all patch counts stay unverified (no grep). `.env.test` hides user-management routes and probably `budget_router`.
- Ticket sizes are stale versus the tree (`projects.py` +1 route, `files.py` 437 not 580); baseline from the base commit.
- Speculative: `/sanity` also applies to steps 2, 4, 5.

---

## 7. Summary for Complexity Assessment

Verdict: not one branch; deliver 12 sequential MRs (step 0, step 1, steps 2-6, step 7 per package; the ticket has no step 8). Steps 0 and 1 each stand alone and can ship before any split, as two MRs, 0 first.

Estimated sizes (engineer-days): step 0 S 1; step 1 M 1.5 (about 70 classes, 15 src files); step 2 assistant L 2.5-3; step 3 index L 2; step 4 analytics M 1-1.5 (85 mechanical routes); step 5 projects M 1.5; step 6 conversation M 1-1.5; step 7 L, about 6 in total. Overall about 17.

Boundaries hold for all five packages once `_common`/`_shared` modules exist; assistant marketplace (about 0.9k lines) and execution (about 0.8k) exceed 500 until step 7.

Recommendation: implement step 0 only: no production change, gates criteria 2-3, computes the unverified pair list.

---

## 8. External References

Resolved: `/home/taras_spashchenko/EPAM/cm/codemie/docs/superpowers/tasks/2026-10-08-epmcdme-15651-split-router-modules/ticket.md`, read in full. Used: plan steps 0-7 (0 guard tests, 1 models, 2 assistant, 3 index, 4 analytics, 5 projects, 6 conversation, 7 helpers to services); criteria 1-8; clarifications 1-3. Its 40 pairs and patch counts are the prior static scan.
