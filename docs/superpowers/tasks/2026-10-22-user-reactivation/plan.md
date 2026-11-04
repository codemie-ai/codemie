# Reactivate Deactivated User Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** POST /v1/admin/users/{user_id}/reactivate restores a deactivated user (EPMCDME-13928).
**Architecture:** Mirror deactivation: router -> `UserManagementService` (inner method + flow) -> `user_repository`. As in deactivation, the USER_REACTIVATED event is staged in the same session before the single commit (atomic with the reactivation); the log line and cache invalidation run exactly once after a successful commit.
**Spec:** requirements in the dispatch; research in `technical-analysis.md`.

Commit per task using the repository's existing convention ("EPMCDME-13928: Capital sentence", local only, never push, never stage `.codemie/codemie-cli.config.json`). Add the Apache license header to any new test file. Tests: strict assertions (`assert_called_once`, `assert_not_called` on 404/409/failed-commit paths), mutation-check each (remove mechanism, see fail). `ExtendedHTTPException(code, message)` takes any int, so 409 is supported.

## Task 1: Event constant and repository method

**Files:** Modify `src/codemie/service/activity/activity_models.py:58` (add `USER_REACTIVATED = "user.reactivated"` to `UserManagementEvent`); modify `src/codemie/repository/user_repository.py` after `soft_delete` (l.155); test `tests/codemie/repository/test_user_repository_crud.py` (new `TestUserRepositoryReactivate`, template `TestUserRepositorySoftDelete`).
**Interfaces:** Produces `UserRepository.reactivate(session, user_id: str) -> bool`: loads via `get_by_id`, sets `is_active=True`, `deleted_at=None`, `update_date=now`, `session.add`, `flush`; returns False if user missing.

Test-first: yes — `test_reactivate_success` (uses `sample_inactive_user`; asserts is_active True, deleted_at None, update_date bumped, flush called once) fails (no method); `test_reactivate_user_not_found` returns False with no add/flush.

- [ ] Write tests, see fail, implement, see pass.

## Task 2: Service reactivation and PUT message

**Files:** Modify `src/codemie/service/user/user_management_service.py` (new static `reactivate_user(session, user_id)` near `deactivate_user` l.269; new `reactivate_user_flow(user_id, actor_user_id) -> CodeMieUserDetail` near `deactivate_user_flow` ~l.1214; PUT refusal ~l.1141-1146 and the `update_user_fields` docstring ~l.1120); tests `tests/codemie/service/user/test_user_management_service.py` (new `# reactivate_user` section) and `test_user_management_service_field_editability.py:858`.
**Interfaces:** Consumes `user_repository.reactivate`, `UserManagementEvent.USER_REACTIVATED`. Produces `reactivate_user_flow`.

Behavior:
- `reactivate_user`: `get_by_id`; 404 `_USER_NOT_FOUND` if none; 409 if `is_active` is true AND `deleted_at` is None (either flag indicating deactivation is reactivatable); otherwise `user_repository.reactivate`. No event, no log, no commit here.
- `reactivate_user_flow`: `with get_session()` (local import from `codemie.clients.postgres`), call `reactivate_user`, then `activity_event_repository.insert(ActivityEventCreate(domain=USER_MANAGEMENT, event_type=USER_REACTIVATED, entity_type=USER, entity_id=user_id, actor_id=actor_user_id), session)` in the same session (same pattern as `deactivate_user`), then ONE `session.commit()` (no second commit). Only after the commit succeeds, exactly once each: `logger.info("user_reactivated: actor_user_id=..., target_user_id=..., domain=user_management")` and `invalidate_user_from_cache(user_id)`; return `get_user_with_relationships(...)` using the actor-admin resolution from `update_user_fields` tail (~l.1195-1210). A failed commit leaves no event, log or cache invalidation (the exception propagates).
- PUT with `is_active=True`: keep 400, message `"Cannot reactivate user via update. Use POST /v1/admin/users/{user_id}/reactivate."` (retains the "Cannot reactivate user" substring so l.858 still passes; add an assertion that "reactivate" endpoint path appears). Fix docstring.

Test-first: yes — patch `codemie.clients.postgres.get_session` and the module's `user_repository`, `activity_event_repository`, `invalidate_user_from_cache`, `logger`. Tests: `test_reactivate_user_emits_user_reactivated_event` (domain, event_type, entity_type, entity_id, actor_id; insert called exactly once with the same session passed to commit, and before commit via a shared call-order manager mock; commit called once), `test_reactivate_user_logs_once_after_commit` (exact log line, once), `test_reactivate_user_invalidates_cache_once_after_commit`, `test_reactivate_user_not_found_404_adds_no_event` (insert, commit, logger, cache not called), `test_reactivate_user_already_active_409_adds_no_event` (repo.reactivate, insert, commit, logger, cache not called), `test_reactivate_user_half_state_is_reactivatable` (is_active True with deleted_at set; and inverse), `test_reactivate_user_commit_failure_skips_log_and_cache` (commit raises; exception propagates; logger info and `invalidate_user_from_cache` assert_not_called).

- [ ] Write tests, see fail, implement, see pass.

## Task 3: Router endpoint, route-map, baselines

**Files:** Modify `src/codemie/rest_api/routers/user_management_router.py` after `deactivate_user` (l.257): `@router.post("/{user_id}/reactivate", response_model=CodeMieUserDetail)` with `Depends(authenticate)`, `Depends(admin_access_only)`, 400 `_USER_MGMT_NOT_ENABLED` when `config.ENABLE_USER_MANAGEMENT` is off, calls `user_management_service.reactivate_user_flow(user_id, user.id)`. Tests: `tests/codemie/rest_api/routers/test_user_management_router_crud.py` (new `TestReactivateUser`, template `TestDeactivateUser` l.396); `test_user_management_router_auditor.py` (route depends on `admin_access_only`, not the auditor guard; auditor fixture refused); `test_user_management_router_authorization.py:306` add `"/v1/admin/users/{user_id}/reactivate": ["POST"]` to the map. Baselines: `tests/codemie/rest_api/router_guards/baselines/openapi.json` and `route_precedence.json`.

Test-first: yes — `test_reactivate_user_success` (service called once with `(user_id, admin.id)`, returns detail), `test_reactivate_user_disabled_management` (400, service not called), `test_reactivate_route_uses_admin_access_only`, `test_reactivate_regular_user_403` (via `admin_access_only` with regular user and auditor), service 404/409 propagate unchanged.

- [ ] Write tests, see fail, implement, see pass.
- [ ] Run `PATH=$HOME/.local/bin:$PATH poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines`; keep ONLY the new reactivate path and its schema changes in `openapi.json` (the local app drops 24 enterprise LLM proxy paths such as /chat/completions, /v1/messages: restore them via `git checkout -p` or manual edit); same for `route_precedence.json` (restore if only unrelated changes).

## Negative-constraint pass

- No stricter guard than `admin_access_only`: Task 3.
- Do not change deactivate flow or user lookups: Tasks touch only added methods plus the PUT message.
- Do not restore MCP auth tokens, no UI, memberships/roles/budgets untouched: Task 1/2 write only the flag fields.
- Nothing logged/changed on 404/409: Task 2. Event staged in the same session before the single commit (no second commit); log and cache invalidation only after a successful commit, exactly once: Task 2.
- Never push, no MR, never stage `.codemie/codemie-cli.config.json`: header.
