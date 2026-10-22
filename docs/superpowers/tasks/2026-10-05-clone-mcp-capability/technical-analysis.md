> **Superseded in part (2026-10-07):** the secret stripping, `copy_values`, `stripped_fields` and placeholder-warning parts described here were removed; the clone is now a full copy. Current requirements: `spec.md` (revised) in this directory; change plan: `docs/superpowers/tasks/2026-10-07-clone-mcp-full-copy-remove-stripping/plan.md`. Kept as an execution record.

# Technical Research

**Task**: mcp clone configuration
**Generated**: 2026-10-05
**Research path**: filesystem

---

## 1. Original Context

Summary: Enable users to clone an MCP server configuration from the MCP management list and edit the new configuration independently.
Story: As a user managing MCP server configurations, I want to clone an existing MCP so that I can quickly create a variant of a working configuration without rebuilding it from scratch. Matches the clone experience already available for other entities (Katas, Workflows, Assistants).
Preconditions: An existing MCP configuration is in the MCP management list; user has permission to create new MCP configurations.
Affected Areas: MCP management list and clone action; MCP configuration creation and editable view; creation permissions and error handling.
Acceptance Criteria:
1. Given a user viewing the MCP management list, when they select an existing MCP and click "Clone", a new MCP configuration is created with configuration, connection settings, permissions, and metadata copied from the original, and the user is taken directly into an editable view of the new entity.
2. A cloned MCP's name is automatically a distinguishable variation of the original name. Naming decision: "Copy of X" generalised with copy number N after "Copy": "Copy 1 of X", "Copy 2 of X" on repeated clones (resolve collisions).
3. Editing the clone or original is saved independently.
4. If clone fails (server/validation error), user sees a clear error message, no new MCP is created, original unchanged.
5. A user without permission to create MCP configurations sees Clone unavailable or disabled.
Out of Scope: carrying over usage history/counters/audit metadata (usage count, creator, system-assigned flags); re-linking downstream resources (assistants/workflows) to the clone; bulk clone.
Decisions on open questions: system-provided (non-user-created) MCP configurations ARE cloneable; sensitive connection credentials (API keys, tokens, secrets) must be skipped/not copied — user must re-enter them; naming collisions resolved via "Copy N of X" numbering.
This is the CodeMie backend repository (Python/FastAPI). Focus on backend: existing MCP config model/repository/service/router, how Assistant/Workflow/Kata clone is implemented (patterns to reuse), permission checks for creating MCPs, credential/secret fields in MCP config, system-provided MCP flag, and unique-name constraints. Note which parts (UI) are outside this repo.

---

## 2. Codebase Findings

Project: Python >=3.12, FastAPI, SQLModel/PostgreSQL, Alembic (`src/external/alembic/versions`), Poetry. Top-level dirs: `src`, `tests`, `config`, `docs`, `deploy-templates`, `scripts`.

### Existing Implementations
- `src/codemie/rest_api/models/mcp_config.py` — `MCPConfig` SQLModel table `mcp_configs` (extends `BaseModelWithSQLSupport`: id, date, update_date). Fields: `name` (indexed, NOT unique), `description`, `server_home_url`, `source_url`, `logo_url`, `categories` (JSONB), `config` (`MCPServerConfigData`, Pydantic JSON column), `required_env_vars`, `user_id`, `is_public`, `is_system`, `created_by` (`CreatedByUser`), `usage_count`, `is_active`. Also `MCPConfigCreateRequest` (name 1-255, description max 2000, categories max 3), `MCPConfigUpdateRequest`, `MCPConfigResponse`, `MCPConfigListResponse`.
- Indexes: `ix_mcp_configs_name_user` (name, user_id; non-unique), `ix_mcp_configs_is_public`, and a UNIQUE partial index `ix_mcp_configs_auth_config_id` on `config->'auth_config'->>'id'` (where not null). A verbatim copy of `auth_config` would violate this unique index.
- `MCPServerConfigData` fields: `command` (allow-list validated), `url`, `args`, `headers` (dict), `env` (dict), `type`, `auth_token`, `single_usage`, `tools`, `audience`, `auth_config` (raw dict; contains `id`, `auth_type`, `client_type`, encrypted `client_secret`, response-only `has_client_secret`), `allow_issuer_prefix_match`.
- `src/codemie/service/mcp_config_service.py` — `MCPConfigService` (classmethods): `create(request, user)`, `get_by_id`, `update`, `delete`, `list_configs`, `increment/decrement/adjust_usage`, `find_by_name(name, user_id)`, `_to_response` (strips `client_secret` from responses, sets `has_client_secret`).
  - `create`: duplicate check via `MCPConfig.get_by_fields({"name", "user_id"})` -> 400 "MCP configuration already exists"; strips `has_client_secret`; `_ensure_auth_config_id` (generates uuid if absent); `_validate_auth_config_for_save` (422); `_prepare_auth_config_for_persistence` (encrypts confidential OAuth2 `client_secret` via `EncryptionFactory`); forces `is_public=True`, `is_system=True`, `usage_count=0`, `is_active=True`, `created_by` from user; catches `IntegrityError` on auth_config.id index -> 409.
  - Request-supplied `is_public` is ignored in create (hard-coded True).
- `src/codemie/rest_api/routers/mcp_config.py` — `/v1/mcp-configs` POST/GET/GET list/PUT/DELETE. POST, PUT, DELETE use `Depends(authenticate), Depends(admin_access_only)`; GET and list only `authenticate`. No clone endpoint exists.
- `src/codemie/rest_api/routers/mcp_managed.py` — `/v1/mcp/managed-servers` (config-file-based managed catalog; unrelated to DB CRUD).
- `src/codemie/service/mcp/access_control.py` — `MCPAccessControlService`: assistants reference catalog entries by `mcp_config_id`; requires catalog entry `is_active` and `is_public`; `mcpCustomServersDisabled` customer component gates custom servers.
- Enterprise MCP auth: `src/codemie/enterprise/mcp_auth/` (`dependencies.py` provides `validate_auth_config_on_save`, `invalidate_credentials_for_auth_config`, `has_any_credentials_for_auth_config`, `is_mcp_auth_enabled`); per-user credentials are keyed by `auth_config.id`.

### Architecture and Layers Affected
- API/router: `mcp_config.py` (new endpoint or reuse of POST).
- Service: `MCPConfigService` (naming, copy/sanitize, persistence).
- Model: `MCPConfig` / request-response models (possibly a new clone request/none).
- Repository: active-record methods on `BaseModelWithSQLSupport` (`get_by_fields`, `get_all_by_fields`, `find_by_id`, `save`); no separate repository class for MCP.
- Enterprise integration (auth_config / token invalidation); no DB migration appears needed for the existing schema.

### Integration Points
- Assistants/workflows reference configs by `mcp_config_id` (`access_control.py`, `assistant.py` `MCPServerDetails`); `usage_count` adjusted via `adjust_usage`.
- `codemie.enterprise.mcp_auth.dependencies` imported directly by the service.
- Encryption service via `EncryptionFactory`.

### Patterns and Conventions
- Assistant clone (reference pattern): there is NO dedicated clone endpoint. The client sends a normal create request to the assistant create route with optional `source_assistant_id` (`rest_api/models/assistant.py:369-370`); after save, `_update_source_clone_count` (`rest_api/routers/assistant.py:682-700, ~799`) logs a clone event (`AssistantCloneEventRepository`, `rest_api/models/usage/assistant_clone_event.py`) and recomputes `clone_count` (`AssistantRepository.update_clone_count`), best-effort/swallowing errors. Naming/copy is done by the client.
- Workflow and Kata: grep of `workflow_service.py`, `ai_kata_service.py` finds no backend clone logic; clone for them appears to be UI-side (unconfirmed, no backend code found).
- Errors: `ExtendedHTTPException(code, message, details, help)` is the convention; messages as module constants.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/` exists. Relevant: `integration/mcp-integration.md` (no clone/catalog mentions found via grep for clone), `api/rest-api-patterns.md`, `architecture/service-layer-patterns.md`, `data/repository-patterns.md`, `development/security-patterns.md`, `development/error-handling.md`, `testing/testing-service-patterns.md`.
- No CHANGELOG mention of MCP clone.

### Architectural Decisions
- Inline comment in `create`: "Since only administrators can create MCP configs, they are public by default / system configs" — the create flow equates creation with admin and marks everything `is_system`/`is_public`.

### Derived Conventions
- Classmethod services; active-record persistence; typed `ExtendedHTTPException`; 400 for duplicate name, 409 for unique index conflict, 422 for auth_config validation.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/test_mcp_config_service_auth_crud.py`, `_auth_conflict.py`, `_auth_secret_storage.py`, `_auth_validation.py` — service CRUD, auth_config conflict, secret encryption, validation.
- `tests/codemie/service/mcp/test_access_control.py`; `tests/codemie/rest_api/routers/test_mcp_managed.py`; `tests/enterprise/mcp_auth/*`.

### Testing Framework and Patterns
- pytest (`pytest.ini`), unit tests under `tests/codemie`, mocks of `MCPConfig` persistence.

### Coverage Gaps
- No router-level tests for `/v1/mcp-configs` (POST/permission); no tests for name-collision numbering (none exists).

---

## 5. Configuration and Environment

### Environment Variables
- `MCP_AUTH_ENABLED` (referenced in warning text; gates `is_mcp_auth_enabled()`).

### Configuration Files
- `src/codemie/configs/customer_config.py` (component flag `mcpCustomServersDisabled`; file is currently modified in git status), `configs/managed_mcp_config.py`, `configs/mcp_commands_config.py`.

### Feature Flags and Deployment Concerns
- `mcpCustomServersDisabled` restricts assistant-level custom servers; no flag found for MCP catalog creation.
- Permission: `admin_access_only` -> `User.is_admin_or_maintainer` (`rest_api/security/authentication.py:180`, `security/user.py:93`). No separate "can create MCP" capability exists.

---

## 6. Risk Indicators

- Permission mismatch: create/update/delete are admin/maintainer-only; AC5 ("user without permission sees Clone disabled") maps to the existing admin check, but AC "user has permission to create" may imply broader users. Speculative: any clone endpoint should reuse `admin_access_only`; broadening would be a product decision. UI needs a way to know permission (no capabilities endpoint for MCP found; the UI likely uses user admin flags — outside this repo).
- `create` hard-codes `is_system=True`, `is_public=True`; clone output flags for "system-assigned flags are not carried over" conflict with current create semantics (a clone made through `create` will become `is_system=True`). Needs an explicit decision.
- `auth_config.id` partial unique index: copying `auth_config` verbatim yields 409; `create` generates an id only when absent, so the clone must drop/regenerate `id`. Also `client_secret` (encrypted) must be removed per decision; `_prepare_auth_config_for_persistence` for confidential OAuth2 without secret pops it, but `_validate_auth_config_for_save` may reject a confidential client missing a secret (unverified).
- Credential fields to be stripped: `config.auth_token`, `config.headers` values (e.g. Authorization), `config.env` values, `auth_config.client_secret`; there is no classification of which header/env keys are sensitive (free-form dicts). Risk of leaking or over-stripping; `required_env_vars` provides names only.
- Name uniqueness is only application-level per (name, user_id) with a non-unique index: race conditions on concurrent clones; "Copy N of X" numbering requires scanning existing names (`get_all_by_fields` loads all; `list_configs` is in-memory pagination).
- Name limit 255 chars: "Copy N of " prefix could exceed max_length of the original name.
- Uniqueness scope is by `user_id` (creator) while list is global; a collision across users isn't blocked.
- Clone-event tracking (assistant pattern) has no MCP equivalent; out of scope per ticket (no counters carried).
- UI (list action, redirect to editable view, disabled state) is not in this repo.

---

## 7. Summary for Complexity Assessment

The backend change surface is small and concentrated: `MCPConfigService` (new clone/naming logic), `routers/mcp_config.py` (new endpoint, or the client composes a GET plus the existing POST), and possibly request/response models. No new DB table or column appears necessary; the existing `mcp_configs` schema holds all cloneable fields. The reference clone pattern in this repo (Assistant) is client-driven: the UI prefills a normal create request and optionally passes `source_assistant_id`; no server-side clone endpoint or copy-naming logic exists for Assistant, Workflow or Kata, so the naming scheme ("Copy N of X") and credential stripping are novel.

The main technical complexity is credential handling and identity constraints: `auth_config.id` has a unique partial index, `client_secret` is encrypted and must be dropped, and `auth_token`/`headers`/`env` are free-form and may hold secrets. Also `create` forces `is_system=True`/`is_public=True` and is admin-only, which interacts with AC1/AC5 and the "system flags not carried over" scope. The enterprise `mcp_auth` integration adds validation on save.

Test posture: service-level tests for MCP config CRUD and auth exist (four `test_mcp_config_service_auth_*` files) and give templates; router tests for `/v1/mcp-configs` and naming logic do not exist. UI work is outside this repository.

---

## 8. External References

None named by the task
