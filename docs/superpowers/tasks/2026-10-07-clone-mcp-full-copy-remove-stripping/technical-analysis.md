# Technical Research

**Task**: mcp config clone service router
**Generated**: 2026-10-07
**Research path**: filesystem

---

## 1. Original Context

Title: Clone MCP (backend): remove secret stripping and copy_values; clone as a full copy

Scope: Backend only, in this repository. UI changes are a separate task in codemie-ui. Nothing here touches the UI.

Context: The clone-mcp-capability work (EPMCDME-15538) is finished and committed on branch EPMCDME-15538-Add-clone-mcp-capability. Requirements changed: clones must carry the source's values as is. Nobody uses copy_values, so the contract and the logic behind it are dead weight. Previous spec/plan: docs/superpowers/tasks/2026-10-05-clone-mcp-capability/ (spec.md, plan.md). Reference only; do not rewrite it.

Contract changes:
- POST /v1/mcp-configs/{id}/clone takes no body.
- Response is plain MCPConfigResponse, status 201.
- MCPConfigCloneRequest, MCPConfigCloneResponse and stripped_fields are removed.
- The placeholder_value_present warning is removed from create, update and clone responses.

Behavior:
- The clone deep-copies the whole config and descriptive fields: headers, env, args, URL (userinfo/query/fragment), auth_token, auth_config and client_secret.
- Only auth_config.id is regenerated (unique index). client_secret is copied in its stored encrypted form, with no re-validation or re-encryption.
- Unchanged: is_system=False, usage_count=0, is_active=True, is_public copied, owner and created_by set to the cloner, "Copy N of <base>" naming (src/codemie/service/mcp/clone_naming.py and its tests stay), 404 for unknown source, 409 for auth_config.id conflict, admin-only permission.

Code to remove: src/codemie/service/mcp/clone_sanitizer.py, clone_constants.py, the CLONE_POLICY maps and get_clone_policy in src/codemie/rest_api/models/mcp_config.py, _add_placeholder_warnings in src/codemie/service/mcp_config_service.py, and the tests for the sanitizer, the placeholder warning, the clone policy and the OAuth2 auto-discovery behavior of a clone without auth_config. Rewrite the remaining service and router clone tests (tests/codemie/service/test_mcp_config_service_clone.py, tests/codemie/rest_api/routers/test_mcp_config.py) for the new behavior.

Non-goals: any UI change; a new migration or constraint; locking for name allocation; permission model changes.

---

## 2. Codebase Findings

### Existing Implementations

Complete list of files referencing the removal targets (repo-wide grep, excluding .git; no hits in `.ai-run/guides/`, README or other `src/` files):

**Delete whole file (src)**
- `src/codemie/service/mcp/clone_sanitizer.py` (253 lines): `sanitize_config`, `validate_copy_values`, `config_contains_placeholder`. Only importer is `mcp_config_service.py:57` and the tests below.
- `src/codemie/service/mcp/clone_constants.py` (24 lines): `TEXT_PLACEHOLDER`, `URL_PLACEHOLDER`, `PLACEHOLDER_WARNING_CODE`. Importers: `mcp_config_service.py:56`, the sanitizer, and three test files.

**Delete whole file (tests)**
- `tests/codemie/service/mcp/test_clone_sanitizer_config.py` (192 lines)
- `tests/codemie/service/mcp/test_clone_sanitizer_url_args.py` (164 lines)
- `tests/codemie/service/mcp/test_clone_auto_discovery_behavior.py` (89 lines); the clone docstring in the service points at it.
- `tests/codemie/service/test_mcp_config_service_placeholder_warning.py`: covers create/update/clone warnings; imports `PLACEHOLDER_WARNING_CODE`, `TEXT_PLACEHOLDER`, `URL_PLACEHOLDER`.
- `tests/codemie/rest_api/models/test_mcp_config_clone_policy.py`: drift-guard tests for `get_clone_policy`, plus request/response model tests (`test_clone_request_defaults_to_empty_copy_values`, `test_clone_response_extends_config_response_with_stripped_fields`).

**Keep unchanged**
- `src/codemie/service/mcp/clone_naming.py` (`allocate_name`, `compute_base`) and `tests/codemie/service/mcp/test_clone_naming.py`.

**Edit: `src/codemie/rest_api/models/mcp_config.py`**
- `MCPServerConfigData.CLONE_POLICY` (lines ~158-175, with its comment).
- `MCPConfig.CLONE_POLICY` (lines ~239-262, with its comment).
- `MCPConfigCloneRequest` (371), `MCPConfigCloneResponse` (381), `CLONE_POLICY_VALUES` (387), `get_clone_policy` (390).
- After removal, `ClassVar` (`from typing import Any, ClassVar`, line 26) has no other use (only the two CLONE_POLICY declarations use it) and must be dropped from the import to keep ruff clean. `Field` and `BaseModel` remain used elsewhere.

**Edit: `src/codemie/service/mcp_config_service.py`**
- Imports (lines 44, 50, 56, 57): drop `MCPConfigCloneResponse`, `get_clone_policy`, `PLACEHOLDER_WARNING_CODE`, and the three sanitizer functions. `import copy` (line 22) is used only by the clone `copy.deepcopy` at line 384, so it stays only if the new clone still deep-copies via `copy.`; otherwise it becomes unused.
- `create` line 332 and `update` line 527: `return cls._add_placeholder_warnings(cls._to_response(...))` becomes `return cls._to_response(...)`.
- `_add_placeholder_warnings` (def at ~808-819) deleted. `ConfigWarning` is still used elsewhere (lines ~794, ~812 area: the inactive-auth-config warning in `_to_response`), so keep that import.
- `clone(cls, source_id, user, copy_values)` (lines 335-421) rewritten. The signature loses `copy_values`, the return type becomes `MCPConfigResponse`, and the docstring (stripped / 422 invalid copy_values / confidential-OAuth2 auto-discovery note) is rewritten. Current flow:
  1. `MCPConfig.find_by_id` returns 404 `ExtendedHTTPException` when missing (keep).
  2. `validate_copy_values` and `sanitize_config` (remove).
  3. `_get_raw_auth_config`, `_ensure_auth_config_id`, `_validate_auth_config_for_save`, `_prepare_auth_config_for_persistence` on the cloned config (see the encryption section).
  4. `copied_fields` is built from `get_clone_policy(MCPConfig)` entries equal to "copy": description, server_home_url, source_url, logo_url, categories, required_env_vars, is_public. This is replaced by an explicit list or by direct deep copies.
  5. `Session(MCPConfig.get_engine())`: `allocate_name`, insert, commit, refresh. `IntegrityError` goes through `_is_auth_config_id_conflict` to 409 (keep). Other errors are re-raised after rollback.
  6. `_to_response(row)` then `_add_placeholder_warnings`, then `MCPConfigCloneResponse(**dict(response), stripped_fields=...)`. The new version returns the plain `_to_response(row)`.
  7. The audit log line at ~377 logs `copied_paths=` (remove that part).

**Edit: `src/codemie/rest_api/routers/mcp_config.py`**
- Imports at lines 26-27 (`MCPConfigCloneRequest`, `MCPConfigCloneResponse`). The `Body` import (line 22) has no use other than the clone endpoint's `Body(default=None)`, so it becomes unused; verify with ruff.
- Endpoint at lines 90-121: `response_model=MCPConfigCloneResponse` becomes `MCPConfigResponse`, the `request` param is removed, the `copy_values = ...` line is removed, and the call becomes `MCPConfigService.clone(config_id, user)`. Update the summary/description ("secret-stripped") and the docstring's "Request Body (optional)" and `stripped_fields` text. Dependencies `authenticate` and `admin_access_only` stay.

### Architecture and Layers Affected
- REST router (`rest_api/routers/mcp_config.py`), API models (`rest_api/models/mcp_config.py`), service (`service/mcp_config_service.py`, `service/mcp/`), and tests. No repository, DB schema or migration change (the `_AUTH_CONFIG_ID_INDEX` unique index is already in place).

### Integration Points
- `MCPConfig` rows stored via SQLModel `Session` directly (clone does not use `create`). Encryption via `EncryptionFactory().get_current_encryption_service()` (class attribute `MCPConfigService.encryption_service`). `invalidate_credentials_for_auth_config` is not invoked by clone.
- No other `src/` module imports the removed symbols.

### How auth_config copying interacts with encryption (current code)
- `_prepare_auth_config_for_persistence(auth_config, existing_encrypted_secret=None, incoming_auth_config=None)` (service ~227-252):
  - not a confidential OAuth2 config: `_remove_client_secret_fields` pops `client_secret`.
  - confidential OAuth2 and `incoming_auth_config is None`: `_encrypt_confidential_client_secret` encrypts `auth_config["client_secret"]` again. For an already-stored value this would double-encrypt, so calling it on a copied stored config is wrong for the new requirement.
  - only the `incoming_auth_config` / `existing_encrypted_secret` branches preserve a stored ciphertext, and those are update-path shapes.
- `_validate_auth_config_for_save(config)` (~194) calls `validate_auth_config_on_save(auth_config, transport)`. This is the enterprise validator. It currently runs in clone, and the requirement says no re-validation, so it is removed from the clone path.
- `_ensure_auth_config_id` only fills a missing/blank id. Clone must instead overwrite `auth_config["id"]` with a fresh `str(uuid.uuid4())` unconditionally, because the source id would collide with the unique index. This is a behavior difference from the existing helper: it needs either a direct assignment or a new helper. The constant is `_AUTH_CONFIG_ID_FIELD`.
- The stored `auth_config` is a plain `dict` on `MCPServerConfigData.auth_config`. A copy via `source_config.model_copy(deep=True)` or `copy.deepcopy` keeps the encrypted `client_secret` string intact. `_get_raw_auth_config` returns the live dict, so mutating the id on the deep copy leaves the source untouched.
- The response path `_to_response` already removes `client_secret` from the returned `auth_config` and sets `has_client_secret` for `oauth2`. So returning the copied secret to the client is safe at the response layer. The persisted row retains the ciphertext.
- Source with `config=None`: current code uses `source.config or MCPServerConfigData()`. Keep an equivalent guard.

### Patterns and Conventions
- Clone bypasses `create` so that `is_system` / `is_public` are not forced. IntegrityError handling pattern (`_is_auth_config_id_conflict`, rollback, 409 `ExtendedHTTPException`) is shared with create/update and stays.
- Files carry Apache license headers; tests follow the same header convention (a recent commit added them to clone tests).

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/` has no mention of clone, copy_values, stripped_fields or placeholder_value_present (grep clean). No guide update is needed.
- Documents that mention the old contract but are historical: `docs/superpowers/tasks/2026-10-05-clone-mcp-capability/` (spec.md, plan.md, task-1/2/3/5/6-reports, decisions.jsonl, actual-complexity.json, code-review-final.json). The task says reference only, so do not rewrite. `docs/stories/2026-10-06-mcp-clone-backend-sanitization/` (story.md, technical-analysis.md, complexity-assessment.json) mentions clone sanitization; treat as historical too.

### Architectural Decisions
- The old spec's decision to blank secrets and fail-closed on `copy_values` is reversed by this task. The source of truth is the task_context.

### Derived Conventions
- Ruff is the gate (`make ruff`); unused imports (`ClassVar`, `Body`, possibly `copy`) will fail it.

---

## 4. Testing Landscape

### Existing Coverage
- Delete: the five files listed in Section 2.
- Rewrite `tests/codemie/service/test_mcp_config_service_clone.py` (235 lines, pytest, MagicMock, patch of `Session`/`MCPConfig.find_by_id`). It imports `MCPConfigCloneResponse` and `TEXT_PLACEHOLDER`, and `_clone(source, copy_values=None)` passes `copy_values`. Tests by fate:
  - Keep or adapt: `test_clone_sets_flags_owner_and_copies_descriptive_fields`, `test_clone_allocates_name_from_cloner_and_source_name`, `test_clone_is_a_deep_copy_independent_of_the_source`, `test_system_source_is_cloneable_and_source_flags_are_untouched`, `test_unknown_source_returns_404_and_opens_no_session`, `test_auth_config_id_conflict_maps_to_409_and_rolls_back`, `test_other_integrity_errors_are_reraised_after_rollback`.
  - Rewrite: `test_clone_blanks_secrets_and_reports_stripped_fields` becomes asserting verbatim headers, env, args, URL and auth_token; `test_non_confidential_auth_config_gets_new_id_and_no_secret` and `test_confidential_oauth2_clone_has_no_auth_config_and_skips_enterprise_validation` become new-id plus copied ciphertext assertions (encrypt must not be called, validator not called).
  - Delete: `test_clone_copies_selected_value_and_omits_it_from_stripped`, `test_invalid_copy_values_return_422_and_persist_nothing`, `test_audit_log_contains_copied_paths_and_no_values`. The fixture `_make_source` uses the `Bearer super-secret-value` header and `auth_token`, which suits the verbatim assertions.
- Rewrite `tests/codemie/rest_api/routers/test_mcp_config.py`: it imports `MCPConfigCloneResponse` (line 23) and `TEXT_PLACEHOLDER` (line 26), `_clone_response()` builds `MCPConfigCloneResponse` (line 61-83), and at lines ~118-133 the assertions `clone.assert_called_once_with("src-1", admin, [])` and `..., ["headers.Authorization"])` encode copy_values. These change to `("src-1", admin)`, and the body-with-copy_values test is deleted (or replaced by a check that a body is ignored). Admin/non-admin 403 tests (lines ~95-111) and the error-passthrough test (~160) stay.
- `tests/codemie/service/mcp/test_clone_naming.py` stays.

### Testing Framework and Patterns
- pytest with `unittest.mock` and FastAPI TestClient; no DB (Session is mocked).

### Coverage Gaps
- No existing test covers the encrypted-secret copy-through (encryption service not called) or the regenerated `auth_config.id` distinct from the source. These are new tests in the service clone test file. Per AGENTS.md, running tests only happens when asked.

---

## 5. Configuration and Environment

### Environment Variables
- `MCP_AUTH_ENABLED` (via `is_mcp_auth_enabled()`) affects `_to_response` warnings and enterprise validation. It is not clone-specific.

### Configuration Files
- None related. No migration needed (the unique index on auth_config.id exists).

### Feature Flags and Deployment Concerns
- None. The API contract is breaking for clients that send a body or read `stripped_fields` (the UI is a separate task, so cross-repo coordination is a deployment sequencing concern).

---

## 6. Risk Indicators

- Speculative: reusing `_prepare_auth_config_for_persistence` on the copied config would re-encrypt a stored ciphertext (double encryption, so the secret is unusable) or strip `client_secret` for non-confidential configs. The clone must skip it entirely.
- Speculative: reusing `_ensure_auth_config_id` is not enough, because it keeps an existing id and would trigger the 409 on every clone of a source that has an auth_config. An unconditional regeneration is needed.
- Dropping `_validate_auth_config_for_save` from clone removes the 422 "Invalid auth_config" path; any remaining test or docstring referring to 422 for clone needs removal.
- Unused-import lint failures are likely in three places: `ClassVar` in models, `Body` in the router, and `copy` in the service (depends on the rewrite).
- The `CLONE_POLICY` drift guard disappears. New `MCPConfig` fields will no longer be forced to declare a clone policy, so the explicit field-copy list in `clone` must be maintained by hand. Copying through `model_dump` of descriptive fields is an alternative design choice for the plan.
- Copied `auth_token` and `client_secret` are persisted to a new row owned by the cloner (an admin). This is intentional per the task, but the log line must not print config values.
- `placeholder_value_present` removal from create/update changes response warnings for all MCP callers, not only clone. Check for other tests asserting warnings on create/update (grep found only the placeholder-warning test file).
- The historical task docs and the `docs/stories/2026-10-06-...` folder still describe the old contract; they are not part of the change.

---

## 7. Summary for Complexity Assessment

The change is a removal-dominated refactor across three source files (service, router, model) plus two deleted modules in `src/codemie/service/mcp/`. The layers are router, API model and service. There is no schema, migration or config change. The only real logic is the rewritten `MCPConfigService.clone` (about 90 lines): deep-copy the source, assign a fresh `auth_config.id`, copy the encrypted `client_secret` untouched, and skip validation, encryption and sanitizing.

Technical novelty is low. The subtle part is the interaction with `_prepare_auth_config_for_persistence` and `_ensure_auth_config_id`, which must not be applied to the copied auth_config. The test surface is larger than the source change: five test files are deleted outright, and two (service clone with 235 lines, router clone tests) are rewritten. Coverage of the new behavior must be written fresh, and the existing fixtures are reusable.

The risks are breaking the API contract for the not-yet-updated UI, lint fallout from unused imports, and the loss of the CLONE_POLICY drift guard. Documentation impact is nil in `.ai-run/guides/`. Expect a small-to-medium band: low design risk, many touched files.

---

## 8. External References

None named by the task. The previous spec/plan at `docs/superpowers/tasks/2026-10-05-clone-mcp-capability/` is named as reference only, and it was not re-read beyond confirming that it mentions the removed symbols.
