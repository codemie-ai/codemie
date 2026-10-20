# EPMCDME-16025 BE review fixes (BE-1, BE-2, OpenAPI contract) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the two P2 visibility leaks of `cli_analytics_enabled` and record the intended OpenAPI contract change.

**Architecture:** One viewer rule, `cli_analytics_policy` (`can_view`, `require_write`; `src/codemie/service/project/cli_analytics_policy.py:26-45`), applied on every path that returns an Application or project DTO. Fixes go on top of HEAD 2dd1d6785 as NEW commits (no history rewrite).

**Tech Stack:** FastAPI, SQLModel, pytest (`TestClient`, handlers called directly), Poetry on Windows.

## Global Constraints

- Commit per task, subject `EPMCDME-16025: <Description>` (given per task); stage explicit paths only. Never touch `.codemie/codemie-cli.config.json`, untracked `docs/superpowers/tasks/2026-09-17*` and `2026-09-18*`, `img.png`.
- Targeted test command (substitute paths): `ENV=local PG_URL=postgresql://pg:pg123@localhost:111/postgres poetry run pytest -o addopts="--import-mode=importlib" <paths>`. Run only the named files; no full suite.
- Read visibility: global admin/maintainer and Project Admin of THAT project. Write: `is_admin_or_maintainer` only. Never drop the field globally.
- Test users are built with `config.ENV="dev"` (`make_user`, `test_projects_cli_analytics.py:55-58`); with ENV=local every `User` is admin.

## Acceptance criteria

- Legacy `GET /v1/application/{app_name}`: key present for admin, maintainer, Project Admin of that project; absent for plain member, auditor, Project Admin of another project; outsider gets 403. Viewer body is otherwise unchanged.
- `POST /v1/projects` by an ordinary user (no prior global/project-admin rights) contains the key; the same user still cannot write it (field in body -> 403 before any mutation).
- Every other path returning an Application or project DTO is verified to apply the rule (enumeration below).
- Router contract guards run green on this machine; OpenAPI baseline changes only by one optional boolean in six schemas.

## Assumptions (veto at plan approval)

1. BE-2 fix lives in `ProjectService.create_shared_project`, not in the router: that is where the membership is granted, the router and `visible_value` stay untouched, and the request `User` is a per-request copy (`persistent.py:174-176`). Consequence: `test_projects_cli_analytics.py:112-120` (mocked service) and `test_projects_router.py:111-115` (kwargs pin) stay valid and are NOT behaviour-edited. Alternative (router derives visibility from "creator is admin") would flip :112-120 to "visible for every role" and bypasses the policy helper; rejected.
2. BE-1 keeps `response_model=Application` (snapshot unchanged for that schema); non-viewers get the same JSON minus the key via `JSONResponse(jsonable_encoder(application, exclude={FIELD}))`. The entity attribute is never set to None (column is `bool NOT NULL`, `models.py:397`).
3. Other `Application` fields stay visible to members as today (out of scope).
4. Enumeration (analysis + re-grep of `response_model=Application|-> Application` under `src/codemie/rest_api`): the only unfiltered entity path is the legacy GET. `GET/POST/PATCH /v1/projects*` already filter (`crud.py:204,277,331`; `management.py:89`); `GET /v1/applications` is a different `Application` class. Raw-value dict builders (`crud.py:353-372`, `project_visibility_service.py:120,199`) are internal, always fed through the router filter, covered by existing list/detail tests; no change.
5. Guard on Windows is run through a scratch wrapper OUTSIDE the repo; the guard file is never edited.

---

### Task 1: BE-1 legacy GET viewer rule

**Files:**
- Modify: `src/codemie/rest_api/routers/index/code_repositories.py:406-437` (`get_application`)
- Test: `tests/codemie/rest_api/routers/test_projects_cli_analytics.py`

Test-first: yes — `test_legacy_application_get_exposes_value_only_to_viewers[member]` fails today (key present for a plain member).

- [ ] Generalise `_client` (`:267-280`) to `_client(user, router=projects_router)`; its `authenticate` override must also set `request.state.user = user` (take `request: Request`). Existing wire tests keep passing.
- [ ] Add a parametrized matrix over `ROLES` (reuse `make_user`) mounting `codemie.rest_api.routers.index.router` (already `/v1`, router-level `authenticate`). Patch `code_repositories.Application.get_by_id` (return `Application(name="proj", cli_analytics_enabled=True)`), `CodeIndexInfo.get_all`/`filter_by_names_or_user` (empty list), `GitRepo.get_by_app_id` (empty list). Expect: admin, maintainer, project_admin -> 200 with `True`; member, other_project_admin, auditor -> 200 without the key; outsider -> 403. Add one parity assertion: non-viewer body == viewer body minus the key.
- [ ] Run; confirm the member/other_project_admin/auditor cases FAIL.
- [ ] In `get_application` replace the final `return application` (:437): `if cli_analytics_policy.can_view(request.state.user, application.name): return application`, else `return JSONResponse(jsonable_encoder(application, exclude={cli_analytics_policy.FIELD}))`. Import `JSONResponse`, `jsonable_encoder`, `cli_analytics_policy`; no change to decorator.
- [ ] Re-run the file plus `tests/codemie/rest_api/routers/test_index*.py`; all pass. Re-run the enumeration grep from Assumption 4 and list any new hit in the digest.
- [ ] Commit `EPMCDME-16025: Hide cli_analytics_enabled from non-viewers on the legacy application endpoint`.

### Task 2: BE-2 creator membership in the create response

**Files:**
- Modify: `src/codemie/service/project/project_service.py:200-202` (after `session.commit()` / `invalidate_user_from_cache`)
- Test: `tests/codemie/service/project/test_project_service_cli_analytics.py`, `tests/codemie/rest_api/routers/test_projects_cli_analytics.py`

Test-first: yes — real-service create by an ordinary user returns a body without the key today.

- [ ] Service test: with the `repo` fixture (:44-55) and a REAL ordinary `User` (config patched to `dev`, no admin names), call `create_shared_project`; assert the project name is in `user.admin_project_names` and `project_names`, `user.is_admin_or_maintainer` is False, and `cli_analytics_policy.require_write(user, {FIELD})` still raises 403. Extend `test_create_rejects_value_without_global_write` (:118-124) to also assert the user's `admin_project_names` is unchanged. Patch `llm_service` in the module if the real user needs it.
- [ ] Router test (real `project_service`, repositories patched as in the service fixture, `_enforce_project_limit` patched): `create_project(ProjectCreateRequest(name="proj"), user=ordinary)` -> `dumped(response)` contains the key; same user with `{KEY: True}` -> 403 and `application_repository.create` not called.
- [ ] Run; confirm the new tests FAIL.
- [ ] After `invalidate_user_from_cache(user.id)` add the created name (`validated_name`) to `user.admin_project_names` and `user.project_names` when absent (assign new lists; do not touch `is_admin`/`is_maintainer`). Comment: the request `User` is a snapshot taken before the grant.
- [ ] Add a docstring to `test_create_response_exposes_value_only_to_viewers` (:112-120): it mocks the service, so it pins "router applies the policy to the rights the user holds"; the real grant is covered by the new tests. No expectation change; `test_projects_router.py:111-115` untouched (kwargs unchanged).
- [ ] Run: `tests/codemie/service/project/test_project_service_cli_analytics.py tests/codemie/service/project/test_cli_analytics_policy.py tests/codemie/rest_api/routers/test_projects_cli_analytics.py tests/codemie/rest_api/routers/test_projects_router.py`. All pass.
- [ ] Commit `EPMCDME-16025: Reflect the creator's new project admin membership in the create response`.

### Task 3: Router contract guards and OpenAPI baseline

**Files:**
- Create (outside repo, not committed): `C:/codemie-dev/_local/cli-analytics-project-gate/run_guards.py`
- Modify: `tests/codemie/rest_api/router_guards/baselines/openapi.json` (only through the repo's own command)

Test-first: yes — `test_openapi_snapshot.py` must FAIL on the six-schema diff before the baseline is regenerated, then pass.

- [ ] Wrapper `run_guards.py` (`sys.path.insert(0, <repo root>)`; import `tests.codemie.rest_api.router_guards.guarded_app as g`; `g._INHERITED_ENV_NAMES += ("SYSTEMROOT","SYSTEMDRIVE","WINDIR","TEMP","TMP","USERPROFILE","APPDATA","LOCALAPPDATA")`; then by `argv[1]`: `pytest` -> `pytest.main(["-o","addopts=--import-mode=importlib","tests/codemie/rest_api/router_guards","-q","--tb=short","-p","no:cacheprovider"])` (no `-n 3`); `dump <file>` -> write `canonical_json(g.load_guarded_app_dump()["openapi"])` (from `baseline.py`) to a file under `C:/codemie-dev/_local/tmp`; `regen` -> `regenerate_baselines.main()`).
- [ ] PowerShell from the repo root: `New-Item -ItemType Directory -Force C:\codemie-dev\_local\tmp`; `$env:TMP="C:\codemie-dev\_local\tmp"; $env:TEMP=$env:TMP`; `poetry run python C:/codemie-dev/_local/cli-analytics-project-gate/run_guards.py pytest`. If the child names another missing variable, add it to the tuple. Expected now: only `test_openapi_snapshot` fails (about 20 s for the one probe).
- [ ] If the guard still cannot run: STOP. Do not hand-edit the baseline, do not edit the guard. Report BLOCKED with the stderr; the QA result stays FAIL/BLOCKED, never PASS.
- [ ] Review: `run_guards.py dump C:/codemie-dev/_local/tmp/openapi.new.json`, then `git diff --no-index --stat` and full diff against `baselines/openapi.json`. Allowed: exactly six schemas gain `cli_analytics_enabled` (`codemie__core__models__Application`: `{"default": false, "title": "Cli Analytics Enabled", "type": "boolean"}`; `ProjectCreateRequest`, `ProjectCreateResponse`, `ProjectDetailResponse`, `ProjectListItem`, `ProjectUpdateRequest`: `anyOf` boolean|null, same title), none added to `required`. Anything else (paths, operationIds, `-Input/-Output` split, descriptions) -> STOP and report; do not regenerate.
- [ ] Only if the diff is exactly as allowed: `run_guards.py regen` (the repo's documented `regenerate_baselines`, `.ai-run/guides/testing/testing-api-patterns.md:40`). Then `git status --short`: only `openapi.json` changed (`route_precedence.json` unchanged, else STOP and report).
- [ ] Re-run `run_guards.py pytest`: all guards pass.
- [ ] Commit only `tests/codemie/rest_api/router_guards/baselines/openapi.json` as `EPMCDME-16025: Record cli_analytics_enabled in the OpenAPI contract baseline`.

---

## Self-review

- negative-constraints: (1) do not drop the field globally -> T1 keeps `response_model`, viewers asserted; (2) creator must not gain write -> T2 asserts `is_admin_or_maintainer` False and `require_write` 403; (3) non-global create with field is 403 before mutation -> T2 asserts `create` not called and names list unchanged; (4) do not edit the guard / no PASS if it cannot run -> T3 wrapper outside repo and BLOCKED rule; (5) unexpected snapshot diff stops work -> T3 review step; (6) no blind test edits, no history rewrite, no unrelated files -> T2 docstring-only edit, Global Constraints.
- Coverage: BE-1 T1, BE-2 T2, tests T1/T2, guards and snapshot T3. 3 tasks, 3 `Test-first: yes`.
