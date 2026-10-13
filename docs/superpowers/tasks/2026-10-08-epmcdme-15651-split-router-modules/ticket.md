# EPMCDME-15651 — Split oversized REST API router modules into smaller per-concern modules

Jira Story, project EPMCDME, priority Major, labels AI/Run and AI-Generated, no epic link.
This file is the verbatim ticket body used as the requirements for the SDLC run.

## Context / problem

The package `src/codemie/rest_api/routers` has 62 modules, 26,061 lines in total. Five files hold 45.6% of it:

- `assistant.py`: 3,214 lines, 36 routes. Its 52 helper functions (1,370 lines) outweigh the route handlers (1,292 lines).
- `analytics.py`: 3,113 lines, 85 routes, almost all identical 16-line wrappers.
- `index.py`: 2,884 lines, 45 routes. Each of 13 datasource types has its own create and update endpoint, and some also have a reindex endpoint.
- `projects.py`: 1,483 lines, 13 routes, 29 Pydantic classes, plus about 470 lines of spending/budget calculation.
- `conversation.py`: 1,187 lines, 29 routes.

Files between 500 and 1,000 lines: `workflow_executions.py` (921), `skill.py` (866), `workflow.py` (847), `project_budget_router.py` (786, two routers in one file), `cli_analytics.py` (656), `files.py` (580).

Imports also run in the wrong direction between layers:

- Services import from routers: `service/budget/budget_service.py` imports from `routers/budget_router.py`, and `service/budget/project_budget_service.py` imports from `routers/project_budget_router.py`.
- A handler imports from a router: `rest_api/handlers/assistant_handlers.py` imports `ToolCallResumeRequest` from `routers/assistant.py`.
- Routers import private helpers from other routers: `assistant_mapping.py`, `assistant_prompt_variable_mapping.py` and `ide.py` import `_get_assistant_by_id_or_raise` and `_check_user_can_access_assistant` from `routers/assistant.py`.

## Goal

Split the oversized router modules into packages of small, cohesive modules (soft target: 400-500 lines per module, no handler over about 60 lines) without changing any API behavior.

## Approach

1. Turn each large module into a package of the same name (for example `routers/assistant.py` becomes `routers/assistant/`). The package `__init__.py` builds one combined `router` that includes the sub-routers in a fixed order. `main.py` and the import path stay unchanged.
2. Set the router prefix, tags and shared dependencies (for example `Depends(authenticate)`) once on the combined router.
3. Keep endpoint function names, paths, tags and response models unchanged, so OpenAPI operationIds and the API contract stay identical.
4. The package `__init__.py` exports only `router`. No re-exports, so stale test `mock.patch` targets fail loudly and get migrated to the new module paths.
5. Move request/response Pydantic classes from routers into `rest_api/models/` in a separate merge request BEFORE the router splits (delivery step 1). Move domain helpers into `service/` in separate merge requests AFTER the move-only splits (delivery step 7).

## Target split

- `assistant/`: catalog, toolkits, management (CRUD), execution (model/chat, tool-call resume, evaluate, metrics), marketplace, generation, versions, `_shared` (lookup, access check, metrics, error builders). Move the inline-credential detection and prompt-variable encryption helpers into `service/assistant/`.
- `analytics/`: `_common`, usage, activity, cli, cli_insights, cli_enriched_users, spending, leaderboard, routing. Move the spending formatting helpers into `service/analytics/`.
- `index/`: `_common`, queries, code_repositories (git/svn applications), kb_atlassian, kb_azure_devops, kb_google, kb_sharepoint, kb_xwiki, kb_git_faq, kb_files, providers.
- `projects/`: crud, spending, membership. Move the classes to `rest_api/models/project.py` and the spending helpers to `service/project/project_spending_service.py`.
- `conversation/`: crud, history, streaming, folders, messages.
- Second priority, when those files are next touched: split `project_budget_router.py` into project budgets and project budget groups, with schemas moved to `rest_api/models/project_budget.py` (this removes the service-imports-router problem); do the same for `budget_router.py`. Split `workflow_executions.py` into executions, states/transitions and export. Split `skill.py` into catalog, management, assistant links, marketplace and generation. Move `files.py` authorization and sanitizing into `service/file_service/`.

## Delivery plan (one merge request per step; each split merge request contains only moved code)

0. Guard tests: an OpenAPI snapshot test (`app.openapi()` must be identical) and a route-precedence test (route pairs whose paths can match the same URL must keep their relative registration order in `app.routes`).
1. Move request/response classes out of routers into `rest_api/models/`.
2. `assistant/`
3. `index/`
4. `analytics/`
5. `projects/`
6. `conversation/`
7. Move domain helpers into services, one merge request per package.

## Risks

- FastAPI route precedence: a static scan found 40 candidate route pairs whose matching depends on registration order. Examples: `GET /v1/index/users` and `/v1/index/find_id` must stay before `/v1/index/{index_id}`; `GET /v1/conversations/search` and `/new` before `/v1/conversations/{conversation_id}`; `GET /v1/skills/categories`, `/users` and `/config` before `/v1/skills/{skill_id}`; `POST /v1/assistants/virtual/model` before `/v1/assistants/{assistant_id}/model`. Mitigated by the step 0 guard tests.
- Test patch targets must be migrated. `mock.patch` references to the current modules: assistant 135 (11 test files), index 168 (5), projects 265 (2), workflow_executions 132 (6), conversation 79 (5), analytics 55 (8).

## Acceptance criteria

1. `assistant.py`, `analytics.py`, `index.py`, `projects.py` and `conversation.py` are replaced by same-named packages. Resulting split modules are approximately 500 lines or fewer, and handlers approximately 60 lines or fewer.
2. The OpenAPI schema (paths, operationIds, tags, request/response models) is identical before and after the refactoring.
3. The route-precedence guard test passes: every overlapping route pair keeps its original relative order in `app.routes`. The baseline covers the 40 candidate pairs identified by the static scan.
4. `main.py` router registration and conditional includes are unchanged. Each new `__init__.py` exports only `router`; the combined router preserves registration order and sets its shared prefix, tags and dependencies once.
5. No module under `service/` or `rest_api/handlers/` imports from `rest_api/routers/` (except the existing `routers/utils.py` helpers). No router imports underscore-prefixed private helpers from another router module.
6. All tests under `tests/codemie/rest_api/routers` pass, with `mock.patch` targets updated to the new module paths. Ruff lint passes (max-complexity 16).
7. Each split merge request contains only moved code (reviewable with `git diff --color-moved`). Moving logic into services is done in separate merge requests.
8. Merge requests that move security-relevant code (file access authorization, inline-credential detection, prompt-variable encryption) ask a reviewer to post `/sanity` in the description.

## Out of scope

Changing endpoint behavior, paths or response shapes; collapsing the analytics wrappers with a route factory (possible later follow-up).

## Clarifications agreed when the ticket was created

1. No epic link.
2. The numbered delivery plan wins: request/response classes move to `rest_api/models/` BEFORE the router splits (step 1); domain helpers move into services AFTER the splits (step 7).
3. The services import only Pydantic request/result classes from the budget routers (`BudgetCreateRequest`, `BudgetUpdateRequest`, `BudgetSyncResult`, `BudgetAssignmentBackfillResult` from `budget_router`; `ProjectBudgetCreateRequest`, `ProjectBudgetUpdateRequest`, `ProjectBudgetGroupCreateRequest`, `ProjectBudgetGroupUpdateRequest` from `project_budget_router`). Delivery step 1 moves those classes into `rest_api/models/`, which removes the imports without splitting the budget routers. Splitting the budget routers themselves stays second priority. Acceptance criterion 5 stays in this story.
