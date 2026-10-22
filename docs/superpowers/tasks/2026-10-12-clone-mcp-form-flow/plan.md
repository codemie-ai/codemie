# MCP Server Clone — Form-Based Flow (Backend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the no-body backend `clone` with a form-based flow: a read-only suggested-name
endpoint, and a `clone_from_form` create path that takes a submitted payload, fixes the
`is_system` copy-from-source bug, and carries over an unchanged `client_secret` without
double-encrypting it.

**Architecture:** Add `GET /v1/mcp-configs/{config_id}/suggested-clone-name` (pure read, reuses
`clone_naming.compute_base`/`allocate_name` verbatim). Replace `POST /v1/mcp-configs/{config_id}/clone`
to accept a new `MCPConfigCloneRequest` body and call a new `MCPConfigService.clone_from_form`,
which mirrors `create`'s validation/persistence pipeline but copies `is_system`/`is_public` from
the source and carries over the source's encrypted `client_secret` the same way `update` does
(`_prepare_auth_config_for_persistence` with both `existing_encrypted_secret` and
`incoming_auth_config`). Remove the old `clone` method and its direct-`Session` persistence
entirely.

**Tech Stack:** FastAPI router, SQLModel/`MCPConfig`, pytest + `unittest.mock`.

## Global Constraints

- Backend-only (`codemie` repo); no `codemie-ui` changes (spec Non-goals).
- No change to how `client_secret` is displayed/edited for plain `update`/Edit.
- No new masking of `headers`/`env`/`args`/`auth_token`/`url`.
- No `copy_values`/clone-policy/field-stripping concept reintroduced.
- Suggested-name lookup is pure read: no DB write, no reservation; stays per-user scoped
  (`allocate_name`'s existing behavior), not globalized.
- No SAML/non-OAuth2 auth-type carry-over handling (confirmed out of scope; SAML has no secret
  fields).
- `clone_naming.py` is not modified.
- Commit per task using the repository's existing convention.

---

### Task 1: `MCPConfigCloneRequest` model

**Files:**
- Modify: `src/codemie/rest_api/models/mcp_config.py` (add after `MCPConfigCreateRequest`, ~line 274)
- Test: `tests/codemie/rest_api/models/test_mcp_config_models.py` (new, or add to nearest existing model test file if one exists)

**Interfaces:**
- Produces: `MCPConfigCloneRequest(name, description, server_home_url, source_url, logo_url, categories, config: MCPServerConfigData, required_env_vars)` — field-identical to `MCPConfigCreateRequest` minus `is_public`.

Test-first: yes — failing test asserting `MCPConfigCloneRequest` validates a payload with `name`+`config` and rejects a payload carrying an `is_system`/`is_public` field being silently ignored (extra fields ignored by default, not an error) plus the existing `categories` max-3 validator still applies.

- [ ] **Step 1: Write the failing test**
```python
from codemie.rest_api.models.mcp_config import MCPConfigCloneRequest, MCPServerConfigData

def test_clone_request_accepts_core_fields_and_rejects_too_many_categories():
    MCPConfigCloneRequest(name="Clone 1", config=MCPServerConfigData(url="https://x"))
    import pytest
    with pytest.raises(ValueError):
        MCPConfigCloneRequest(
            name="Clone 1",
            config=MCPServerConfigData(url="https://x"),
            categories=["a", "b", "c", "d"],
        )
```
- [ ] **Step 2: Run test, confirm it fails** — `MCPConfigCloneRequest` does not exist yet (ImportError).
- [ ] **Step 3: Implement** — add class mirroring `MCPConfigCreateRequest` (lines 252-273) exactly but without the `is_public` field; reuse the same `validate_categories` validator body.
- [ ] **Step 4: Run test, confirm it passes.**
- [ ] **Step 5: Commit.**

---

### Task 2: `MCPConfigService.clone_from_form`

**Files:**
- Modify: `src/codemie/service/mcp_config_service.py` — remove `clone` (lines 339-408); add `clone_from_form` as a new classmethod in its place; import `MCPConfigCloneRequest`.
- Test: rewrite `tests/codemie/service/test_mcp_config_service_clone.py` fully (old file assumes `clone(source_id, user)` with direct-`Session`/`allocate_name` persistence — none of that applies).

**Interfaces:**
- Consumes: `MCPConfigCloneRequest` (Task 1); existing helpers `_strip_response_only_auth_metadata`, `_validate_auth_config_for_save`, `_prepare_auth_config_for_persistence`, `_get_stored_encrypted_secret`, `_get_raw_auth_config`, `_is_auth_config_id_conflict`, `_AUTH_CONFIG_ID_FIELD`.
- Produces: `MCPConfigService.clone_from_form(config_id: str, request: MCPConfigCloneRequest, user: User) -> MCPConfigResponse`.

Test-first: yes — failing tests for: (a) is_system/is_public always copied from source regardless of request body, for both a private and a system/public source; (b) `auth_config.id` always regenerated and differs from source's; (c) client_secret untouched in request → carried over byte-for-byte from source, source unmutated; (d) client_secret replaced in request → freshly encrypted to the new value, source unmutated; (e) duplicate-name raises the same error as `create`; (f) invalid auth_config raises 422; (g) 404 when source missing; (h) persists via `.save()` (not direct `Session`), with `IntegrityError` → `_is_auth_config_id_conflict` → 409.

- [ ] **Step 1: Write failing tests** (new file, replacing the old one). Example for the secret-carry-over cases:
```python
def test_untouched_client_secret_is_carried_over_unchanged(encrypt):
    source = _make_source(auth_config={
        "auth_type": "oauth2", "client_type": "confidential",
        "client_id": "cid", "client_secret": "enc-secret", "id": "old-id",
    })
    request = MCPConfigCloneRequest(
        name="Clone 1",
        config=MCPServerConfigData(auth_config={
            "auth_type": "oauth2", "client_type": "confidential", "client_id": "cid",
        }),
    )
    response = MCPConfigService.clone_from_form("src-1", request, _make_user())
    assert response.config.auth_config["has_client_secret"] is True
    assert source.config.auth_config["client_secret"] == "enc-secret"  # source unmutated

def test_new_client_secret_is_freshly_encrypted(encrypt):
    source = _make_source(auth_config={
        "auth_type": "oauth2", "client_type": "confidential",
        "client_id": "cid", "client_secret": "enc-secret", "id": "old-id",
    })
    request = MCPConfigCloneRequest(
        name="Clone 1",
        config=MCPServerConfigData(auth_config={
            "auth_type": "oauth2", "client_type": "confidential",
            "client_id": "cid", "client_secret": "new-plaintext",
        }),
    )
    MCPConfigService.clone_from_form("src-1", request, _make_user())
    encrypt.assert_called_once_with("new-plaintext")
    assert source.config.auth_config["client_secret"] == "enc-secret"
```
  (Mock `MCPConfig.find_by_id` to return `source`; mock `MCPConfigService.encryption_service.encrypt`;
  mock `MCPConfig.get_by_fields`/`.save()` as MagicMocks, same patching style as the old
  `test_mcp_config_service_clone.py`.)
- [ ] **Step 2: Run tests, confirm they fail** — `clone_from_form` does not exist.
- [ ] **Step 3: Implement** `clone_from_form`:
  - Look up `source = MCPConfig.find_by_id(config_id)`; 404 if missing (same shape as old `clone`, lines 357-364).
  - Duplicate-name check via `MCPConfig.get_by_fields({"name": request.name, "user_id": user.id})`, same 400 as `create` (lines 278-290).
  - `auth_config = _get_raw_auth_config(request.config)`; `_strip_response_only_auth_metadata(auth_config)`.
  - Regenerate id explicitly: `if isinstance(auth_config, dict): auth_config[_AUTH_CONFIG_ID_FIELD] = str(uuid.uuid4())` (always overwrite, never fill-if-missing — do not call `_ensure_auth_config_id`).
  - `_validate_auth_config_for_save(request.config)`.
  - `cls._prepare_auth_config_for_persistence(auth_config, existing_encrypted_secret=_get_stored_encrypted_secret(source.config), incoming_auth_config=auth_config)`.
  - Build `MCPConfig(id=str(uuid.uuid4()), name=request.name, description=request.description, server_home_url=request.server_home_url, source_url=request.source_url, logo_url=request.logo_url, categories=request.categories, config=request.config, required_env_vars=request.required_env_vars, user_id=user.id, is_public=source.is_public, is_system=source.is_system, created_by=CreatedByUser(id=user.id, name=user.name, username=user.username), usage_count=0, is_active=True)`.
  - `try: save_response = mcp_config.save() except IntegrityError as e: ...` identical 409 mapping to `create` (lines 322-333).
  - `return cls._to_response(mcp_config)`.
  - Delete old `clone` method body and its now-unused imports (`copy`, `Session`/`select` stay — still used by `adjust_usage`; `compute_base`/`allocate_name` imports are removed from this module since no longer called here).
- [ ] **Step 4: Run tests, confirm they pass.**
- [ ] **Step 5: Commit.**

---

### Task 3: Router — suggested-name endpoint and clone-from-form route

**Files:**
- Modify: `src/codemie/rest_api/routers/mcp_config.py:88-113` — replace `clone_mcp_config` body to take `MCPConfigCloneRequest`; add new `GET /mcp-configs/{config_id}/suggested-clone-name` route above or below it.
- Modify: `src/codemie/service/mcp_config_service.py` — add `MCPConfigService.suggested_clone_name(config_id: str, user: User) -> str` classmethod: 404 if `MCPConfig.find_by_id(config_id)` missing, else `with Session(MCPConfig.get_engine()) as session: return allocate_name(session, user.id, compute_base(source.name))`.
- Test: new/extended cases in `tests/codemie/rest_api/routers/test_mcp_config.py`; add service-level tests to a `test_mcp_config_service_suggested_name.py` (or inline in the clone test module).

**Interfaces:**
- Consumes: `MCPConfigService.clone_from_form` (Task 2), `MCPConfigCloneRequest` (Task 1), `clone_naming.compute_base`/`allocate_name` (unmodified).
- Produces: `GET /v1/mcp-configs/{config_id}/suggested-clone-name` → `{"suggested_name": str}`; `POST /v1/mcp-configs/{config_id}/clone` body `MCPConfigCloneRequest` → `MCPConfigResponse`.

Test-first: yes — failing tests for: suggested-name 200 with correct body shape, 404 when source missing, 403 for a non-admin caller (same gating as today); clone route 201 with submitted payload creating exactly one record, 404 source missing, 403 non-admin, 409 duplicate name, 422 invalid auth_config.

- [ ] **Step 1: Write failing router tests**, patching `MCPConfigService.suggested_clone_name`/`clone_from_form` the same way existing router tests patch `MCPConfigService.clone`/`create` (see existing patterns in `test_mcp_config.py`), asserting status codes and that the service method was called with the right args.
- [ ] **Step 2: Run tests, confirm they fail** (route/model not wired yet).
- [ ] **Step 3: Implement**:
```python
@router.get(
    "/mcp-configs/{config_id}/suggested-clone-name",
    status_code=status.HTTP_200_OK,
    summary="Suggest Clone Name",
    description="Return the next available Copy N of <name> for this user (Admin only, read-only)",
    dependencies=[Depends(authenticate), Depends(admin_access_only)],
)
def suggest_mcp_config_clone_name(config_id: str, user: User = Depends(authenticate)) -> dict:
    logger.info(f"Suggesting clone name for MCP config: {config_id} by user: {user.id}")
    return {"suggested_name": MCPConfigService.suggested_clone_name(config_id, user)}
```
  Replace `clone_mcp_config`'s signature/body:
```python
def clone_mcp_config(
    config_id: str,
    request: MCPConfigCloneRequest,
    user: User = Depends(authenticate),
) -> MCPConfigResponse:
    logger.info(f"Cloning MCP config: {config_id} by user: {user.id}")
    return MCPConfigService.clone_from_form(config_id, request, user)
```
  Add `MCPConfigCloneRequest` to the router's import block.
- [ ] **Step 4: Run tests, confirm they pass.**
- [ ] **Step 5: Commit.**

---

### Task 4: Remove dead `clone` references and confirm no stale callers

**Files:**
- Verify: `src/codemie/service/mcp_config_service.py` no longer defines `clone` (removed in Task 2); confirm no remaining imports of `copy` module if unused, no remaining references to `_CLONE_COPIED_FIELDS`/old clone's local variables.
- Verify: `grep -rn "MCPConfigService.clone(" src/` returns no matches outside `clone_from_form`'s own name.
- Test: no new test — this is a verification task folded into Task 2/3's existing test suite; run the full `tests/codemie/service/test_mcp_config_service_clone.py` and `tests/codemie/rest_api/routers/test_mcp_config.py` modules to confirm nothing else references the removed method.

Test-first: no — this task only removes dead code and confirms via grep/full-module test run that Tasks 2-3 left no stale references; no new behavior to drive with a failing test.

- [ ] **Step 1: Grep for stale references**: `grep -rn "_CLONE_COPIED_FIELDS\|MCPConfigService.clone\b" src/ tests/` (excluding `clone_from_form`/`suggested_clone_name`).
- [ ] **Step 2: Remove** `_CLONE_COPIED_FIELDS` constant (lines 65-73) if Task 2 left it unused, and drop the `copy` import if no longer referenced.
- [ ] **Step 3: Run** `pytest tests/codemie/service/test_mcp_config_service_clone.py tests/codemie/rest_api/routers/test_mcp_config.py -v` and confirm all pass with no leftover references.
- [ ] **Step 4: Commit.**
