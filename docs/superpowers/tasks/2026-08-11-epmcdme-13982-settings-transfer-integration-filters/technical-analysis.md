# Technical Research

**Task**: settings transfer integrations api
**Generated**: 2026-08-11T00:00:00Z
**Research path**: codegraph

---

## 1. Original Context

Add project-to-project settings transfer filters for user and project integrations. Enhance POST /v1/settings/transfer to support type_of_integration filter (user_integrations, project_integrations) and optional list of integration identifiers to limit transfer scope.

Full ticket description:

Enhance the existing `POST /v1/settings/transfer` API to support transferring settings between projects with filters for user integrations and project integrations, and with an optional list of integrations to limit the transfer scope.

The current settings transfer API accepts source and target project names and supports `move` mode. The API must be extended so administrators can control which integration settings are transferred between projects. The transfer must support filtering by user-level integrations and project-level integrations, plus an optional `list` field with integration identifiers. If `list` is not provided, all integrations matching the selected `type_of_integration` filters must be transferred. If `list` is provided, only integrations from the list must be transferred.

Target schema:
```json
{
  "source_project_name": "string",
  "target_project_name": "string",
  "mode": "move",
  "type_of_integration": {
    "user_integrations": true,
    "project_integrations": true
  },
  "list": ["git-integration", "kb-integration"]
}
```

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/rest_api/routers/settings.py` — FastAPI router; single `POST /v1/settings/transfer` endpoint; delegates entirely to `SettingsTransferService.transfer()`
- `src/codemie/rest_api/models/settings_transfer.py` — Pydantic request/response models: `TransferSettingsRequest`, `TransferSettingsResponse`, `TransferItem`, `TransferMode` enum (`move`/`copy`)
- `src/codemie/service/settings/settings_transfer_service.py` — all transfer business logic: candidate selection, partition (copy-blocked types), alias validation, collision detection, advisory-locked transaction write
- `src/codemie/rest_api/models/settings.py` — `Settings` ORM model; `SettingType` enum (`user`/`project`); `check_alias_unique` used by collision validation; `PROJECT_NAME_TERM`, `USER_ID_TERM`, `ALIAS_TERM` query constants
- `src/codemie_tools/base/models.py` — `CredentialTypes` enum; `COPY_BLOCKED_TYPES` sourced from here (`WEBHOOK`, `SCHEDULER`)

### Architecture and Layers Affected

- **API layer**: `routers/settings.py` — add new request fields to schema, pass to service
- **Pydantic model layer**: `models/settings_transfer.py` — add `TypeOfIntegration` nested model and optional `list` field to `TransferSettingsRequest`
- **Service layer**: `service/settings/settings_transfer_service.py` — thread new filter params through `transfer()` → `_select_candidates()`, add optional list-based filtering, add validation for unrecognised identifiers

### Integration Points

- `codemie.clients.postgres.get_session` — session context manager inside `_apply`
- `codemie.repository.application_repository.application_repository` — project existence checks (`get_active_by_name`)
- `codemie.core.exceptions.ExtendedHTTPException` — shared typed exception for all error responses
- `codemie.enterprise.litellm.credentials.clear_litellm_user_credentials_cache` — conditionally called post-transfer when a `LITE_LLM` row was written
- `codemie.service.settings.settings.SettingsService` — provides `INTERNAL_PREFIX` and `ENFORCE_MEMBER_SPEND_LIMITS_ALIAS` for subsystem-managed filtering
- `codemie.service.settings.scheduler_settings_service.DATASOURCE_SCHEDULE_ALIAS_PREFIX` — another subsystem prefix excluded from transfer
- `Settings.get_all_by_fields` — Elasticsearch query used in `_select_candidates`

### Patterns and Conventions

- Stateless `@classmethod`-only service (`SettingsTransferService`) — no instance state
- Advisory lock (`pg_advisory_xact_lock`) wraps all writes for atomicity; lock id is a SHA-256-derived bigint
- All-or-nothing: collision check runs inside the locked transaction against re-read (FOR UPDATE) rows
- `TransferItem` DTOs materialized before `session.commit()` to avoid `DetachedInstanceError`
- Subsystem-managed rows identified by alias prefix (`SUBSYSTEM_MANAGED_PREFIXES`) are silently excluded from candidates
- `SettingType.USER` vs `SettingType.PROJECT` distinction already exists on the `Settings` model

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/api/rest-api-patterns.md` — router registration, shared exceptions, auth dependency patterns
- `.ai-run/guides/api/endpoint-conventions.md` — typed request/response models, delegate to services
- `.ai-run/guides/architecture/layered-architecture.md` — router → service → repository; shared exceptions from `codemie.core`
- `.ai-run/guides/architecture/project-structure.md` — package boundaries; models under `rest_api/models/`, services under `service/`
- `.ai-run/guides/testing/testing-patterns.md` — test placement mirrors `src/`, mock provider boundaries

### Architectural Decisions

- Advisory lock pattern (pg_advisory_xact_lock) is an established decision for transfer atomicity — must be preserved
- `COPY_BLOCKED_TYPES` from `codemie_tools.base.models` is the canonical list of non-transferable credential types — `_partition` must remain the authority for that check, not the new type filter

### Derived Conventions

- New nested request fields follow the pattern of other nested Pydantic models in the codebase (e.g. inline `class` inside the request model or separate `TypeOfIntegration` model)
- Validation errors use `ExtendedHTTPException` with 422 status for bad request data

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/rest_api/routers/test_settings_transfer.py` — router-level: happy path, auth guard wiring, invalid payloads (422), service error propagation; all current assertions call `mock_transfer.assert_called_once_with` with the old three-argument signature
- `tests/codemie/service/settings/test_settings_transfer_service.py` — service-level: `_select_candidates`, `_partition`, `_validate_projects`, `_validate_aliases_present`, `_validate_no_collisions`, `_lock_source_rows`, `_apply` (advisory lock ordering, DetachedInstance regression CR-001, CR-002 null-alias regression), collision matrix

### Testing Framework and Patterns

- pytest with `anyio` for async router tests
- `unittest.mock` patch/MagicMock throughout
- `TestTransfer` class in service tests has 9 tests that mock `_select_candidates` — all require parameter updates if new filter args are added to `transfer()`

### Coverage Gaps

- No tests for `type_of_integration` filtering (both flags, one flag, neither flag)
- No tests for the optional `list` field (omitted → all, provided → subset, provided with unknown identifier → 422)
- No tests for the response reporting transferred/skipped/failed items per the new AC

---

## 5. Configuration and Environment

### Environment Variables

No env vars specific to the transfer feature — uses global Postgres session and Elasticsearch via `Settings.get_all_by_fields`.

### Configuration Files

No feature-specific config files. Transfer relies on the global ORM/ES configuration.

### Feature Flags and Deployment Concerns

None identified. The endpoint is already gated by admin auth dependency.

---

## 6. Risk Indicators

- **Signature propagation risk**: `transfer()` currently takes three positional args (`source_project_name`, `target_project_name`, `mode`); adding `type_of_integration` and `list` requires updating: the service method, the router call site, and every `assert_called_once_with` in router tests — a missed assertion update will silently pass tests with old signatures.
- **`_select_candidates` has no `setting_type` filter yet**: adding `type_of_integration` filtering requires propagating the new parameter through `transfer()` → `_select_candidates()` and correctly splitting USER vs PROJECT rows.
- **`_partition` separation**: `_partition` currently uses `credential_type` (COPY_BLOCKED_TYPES) to block copy — this is orthogonal to `setting_type` filtering; they must remain independent and not be conflated.
- **Unknown identifier validation gap**: the optional `list` field introduces a new validation case — identifiers provided that do not match any candidate in the source project need a clear 422 signal; no such validation helper exists yet.
- **Collision surface change for `user_integrations`**: `SettingType.USER` rows have `user_id`-scoped alias uniqueness; when only one type is filtered, the collision check must still use each row's own `setting_type` and `user_id` — confirm `check_alias_unique` handles this correctly without change.
- **`TestTransfer` in service tests**: 9 tests mock `_select_candidates`; all need parameter updates after signature change.

---

## 7. Summary for Complexity Assessment

The task adds two new optional request fields — `type_of_integration` (with `user_integrations` and `project_integrations` boolean sub-fields) and `list` (an optional list of integration identifiers) — to the already-existing `POST /v1/settings/transfer` endpoint. The surface area is limited to three files: the Pydantic request model, the router call site, and the service class. The `SettingType` enum (`USER`/`PROJECT`) already exists on the `Settings` ORM model, so no schema migration is required.

The main technical challenge is propagating the new filter parameters cleanly through the service's internal call chain (`transfer()` → `_select_candidates()`), adding identifier-list filtering post-candidate-selection, and adding a validation step for unrecognised identifiers in `list`. The advisory-lock and collision-check logic does not need to change, but care is required not to conflate `setting_type` filtering (new) with `credential_type`-based copy-blocking (`_partition`, existing).

Test surface is moderate: existing router tests all assert the old three-argument call signature and will need updating, and the service's `TestTransfer` class mocks `_select_candidates` across 9 tests requiring parameter updates. New test cases are needed for each combination of the new AC (user-only, project-only, both, list omitted, list provided, unknown identifier in list).
