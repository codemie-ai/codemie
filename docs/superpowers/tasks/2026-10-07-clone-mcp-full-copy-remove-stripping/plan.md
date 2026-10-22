# Clone MCP as a Full Copy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** `POST /v1/mcp-configs/{id}/clone` copies the source config verbatim (no secret stripping, no `copy_values`), regenerating only `auth_config.id`.

**Architecture:** Removal-dominated refactor of the service, router and API models, plus deletion of the sanitizer modules and their tests. `MCPConfigService.clone(source_id, user)` deep-copies the source and persists it directly through a `Session`, as today.

**Tech Stack:** Python, FastAPI, SQLModel, pytest + unittest.mock.

Commit per task using the repository's existing convention. Do not push or open an MR unless asked.

## Global Constraints

- Backend only; no UI, migration, new constraint, locking or permission-model change.
- Keep `src/codemie/service/mcp/clone_naming.py` and `tests/codemie/service/mcp/test_clone_naming.py` unchanged.
- Do not edit historical docs under `docs/superpowers/tasks/2026-10-05-clone-mcp-capability/` or `docs/stories/2026-10-06-mcp-clone-backend-sanitization/`.
- Tests only as listed here. Load `.ai-run/guides/testing/testing-patterns.md` before test work. Ruff must stay clean (no unused imports: `ClassVar`, `Body`, `copy`, `uuid`).
- Test files keep their Apache license header.

## Acceptance criteria

The spec is the old clone spec, so these keep their old numbers.

- AC1: the clone is a full copy of config and descriptive fields (description, server_home_url, source_url, logo_url, categories, required_env_vars, is_public), including headers, env, args, URL, auth_token, auth_config and client_secret.
- AC2-AC6, AC13, AC14: unchanged from the old spec. Covered by the behaviors under "Unchanged" below.
- AC12 (replaced): a confidential OAuth2 auth_config is copied with a NEW id and the stored client_secret unchanged.
- AC21 (without the copy_values half): the info log prints ids only, never config values.
- Removed: AC7-AC11, AC15-AC20.
- Unchanged: is_system=False, usage_count=0, is_active=True, user_id/created_by = cloner, "Copy N of <base>" name, 404 unknown source, 409 on auth_config.id IntegrityError, other IntegrityErrors re-raised after rollback, admin-only, source lookup before any write.

## Negative-constraint pass

- Do not call `_prepare_auth_config_for_persistence`: it double-encrypts or strips. Honored in Task 1.
- Do not call `_validate_auth_config_for_save`: there is no 422 path. Honored in Task 1.
- Do not use `_ensure_auth_config_id`: it keeps the old id. Task 1 regenerates unconditionally.
- Info log must not print config values. Honored in Task 1.
- Non-goals (UI, migration, locking, permission changes, rewriting historical docs): no task touches them.

---

### Task 1: Service clone becomes a full copy; remove the placeholder warning

**Files:**
- Modify: `src/codemie/service/mcp_config_service.py` (imports at ~22, 44, 50, 56-57; `create` return at 332; `clone` at 335-421; `update` return at 527; `_add_placeholder_warnings` at ~808-819)
- Delete: `src/codemie/service/mcp/clone_sanitizer.py`, `src/codemie/service/mcp/clone_constants.py`
- Test: `tests/codemie/service/test_mcp_config_service_clone.py` (rewrite)
- Delete: `tests/codemie/service/mcp/test_clone_sanitizer_config.py`, `tests/codemie/service/mcp/test_clone_sanitizer_url_args.py`, `tests/codemie/service/mcp/test_clone_auto_discovery_behavior.py`, `tests/codemie/service/test_mcp_config_service_placeholder_warning.py`

**Interfaces:**
- Produces: `MCPConfigService.clone(cls, source_id: str, user: User) -> MCPConfigResponse`. Task 2 calls it as `clone(config_id, user)`.

- [ ] **Step 1: Rewrite the service clone tests (failing first).** Keep the `_make_source` fixture and the Session/`find_by_id` patching. Drop the `copy_values` parameter from the `_clone` helper. Tests:
  - Kept or adapted: flags/owner/descriptive fields; name allocation; deep-copy independence; system source cloneable with source flags untouched; 404 opens no Session; auth_config.id IntegrityError gives 409 plus rollback; other IntegrityError re-raised after rollback.
  - Full copy, nothing stripped: headers (`Bearer super-secret-value`), env, args, URL with userinfo/query/fragment, and `auth_token` are all identical to the source.
  - auth_config is copied with a NEW id, different from the source id (also when the source auth_config has an id). The stored `client_secret` string is identical. `encryption_service.encrypt` and `validate_auth_config_on_save` are not called. The source's auth_config id and secret stay unchanged.
  - A source without auth_config stays `None`.
  - Info log: assert the captured message contains ids and not the secret values.
  - Delete the `copy_values`, 422 and `copied_paths` tests.

- [ ] **Step 2: Run** `pytest tests/codemie/service/test_mcp_config_service_clone.py -v`. Expected: FAIL (the signature and stripping behavior still exist).

- [ ] **Step 3: Rewrite `clone` (`mcp_config_service.py:335-421`).**
  - Keep the 404 lookup via `MCPConfig.find_by_id` before any write.
  - Build the new config with `source.config.model_copy(deep=True)`, falling back to `MCPServerConfigData()` when `source.config` is None.
  - If the copy has an `auth_config` dict, do `auth_config.pop(_AUTH_CONFIG_ID_FIELD, None)` and then set `auth_config[_AUTH_CONFIG_ID_FIELD] = str(uuid.uuid4())`. Do nothing else to it.
  - Copy the descriptive fields via an explicit tuple (description, server_home_url, source_url, logo_url, categories, required_env_vars, is_public) using `copy.deepcopy(getattr(source, f))`.
  - Keep the `Session` block: `allocate_name`, insert, commit, refresh. Keep the existing `_is_auth_config_id_conflict` to 409 mapping, and rollback then re-raise for other IntegrityErrors.
  - Return `cls._to_response(row)`. Change the return type to `MCPConfigResponse`.
  - Rewrite the docstring (no stripping, no 422, no copy_values). The info log carries `source_id`, the new id and the user id only.

- [ ] **Step 4: Remove the placeholder warning.** `create` (332) and `update` (527) return `cls._to_response(...)`. Delete `_add_placeholder_warnings`. Drop the now-unused imports (`MCPConfigCloneResponse`, `get_clone_policy`, `PLACEHOLDER_WARNING_CODE`, the sanitizer functions). Keep `ConfigWarning` and `import copy` (still used). Delete the two sanitizer modules and the four test files listed above.

- [ ] **Step 5: Run** `pytest tests/codemie/service -k "mcp" -v`. Expected: PASS.

Test-first: yes — `test_clone_copies_headers_auth_token_and_secret_verbatim_with_new_auth_config_id` fails until stripping is removed and `clone` stops taking `copy_values`.

### Task 2: Router and API models

**Files:**
- Modify: `src/codemie/rest_api/routers/mcp_config.py` (imports at 22, 26-27; endpoint at 90-121)
- Modify: `src/codemie/rest_api/models/mcp_config.py` (`ClassVar` import at 26; `MCPServerConfigData.CLONE_POLICY` ~158-175; `MCPConfig.CLONE_POLICY` ~239-262; `MCPConfigCloneRequest` 371; `MCPConfigCloneResponse` 381; `CLONE_POLICY_VALUES` 387; `get_clone_policy` 390)
- Test: `tests/codemie/rest_api/routers/test_mcp_config.py` (rewrite)
- Delete: `tests/codemie/rest_api/models/test_mcp_config_clone_policy.py`

**Interfaces:**
- Consumes: `MCPConfigService.clone(config_id: str, user: User) -> MCPConfigResponse` (Task 1).
- Produces: `POST /v1/mcp-configs/{config_id}/clone`: no body, `response_model=MCPConfigResponse`, status 201.

- [ ] **Step 1: Rewrite the router tests (failing first).** Remove the `MCPConfigCloneResponse` and `TEXT_PLACEHOLDER` imports. `_clone_response()` builds a plain `MCPConfigResponse`. Tests:
  - A non-admin gets 403 and the service is not called.
  - An admin gets 201 and `clone.assert_called_once_with("src-1", admin)`, with no body sent.
  - The response is flat, with no `stripped_fields` key.
  - Typed 404 and 409 bodies pass through.
  - Delete the `copy_values` body tests.

- [ ] **Step 2: Run** `pytest tests/codemie/rest_api/routers/test_mcp_config.py -v`. Expected: FAIL (the call still passes `copy_values`).

- [ ] **Step 3: Edit the endpoint (`routers/mcp_config.py:90-121`).** Set `response_model=MCPConfigResponse`. Remove the `request` parameter and the `copy_values` line. Call `MCPConfigService.clone(config_id, user)`. Update the summary, description and docstring (full copy; remove the "secret-stripped", "Request Body" and `stripped_fields` text). Keep the `authenticate` and `admin_access_only` dependencies. Drop the `Body` and clone model imports.

- [ ] **Step 4: Edit the models.** Delete both `CLONE_POLICY` maps with their comments, `MCPConfigCloneRequest`, `MCPConfigCloneResponse`, `CLONE_POLICY_VALUES` and `get_clone_policy`. Remove `ClassVar` from the typing import. Delete the clone-policy test file.

- [ ] **Step 5: Run** `pytest tests/codemie/rest_api/routers/test_mcp_config.py -v`. Expected: PASS. Then grep `src/` and `tests/` for `copy_values|stripped_fields|CLONE_POLICY|get_clone_policy|clone_sanitizer|clone_constants|PLACEHOLDER_WARNING_CODE|placeholder_value_present|MCPConfigClone`. Expected: no hits (historical docs excluded).

Test-first: yes — `test_clone_calls_service_with_id_and_user_only_and_returns_flat_response` fails while the endpoint still passes `copy_values` and returns `stripped_fields`.
