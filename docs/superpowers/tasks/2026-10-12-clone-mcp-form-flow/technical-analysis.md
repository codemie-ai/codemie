# Technical Research

**Task**: mcp-config clone mcp-server-modal auth-config client-secret admin-access
**Generated**: 2026-10-09
**Research path**: filesystem

---

## 1. Original Context

MCP server clone needs to change from a one-click backend-only copy into a form-based flow matching Edit's UX.

Frontend (codemie-ui):
- Remove ConfirmationModal clone-confirmation step in MCPServerActions.tsx (the showCloneConfirmation/confirmClone wiring).
- Reuse MCPServerModal for Clone with title "Clone MCP Server", submit label "Clone Server", prefill all fields from source row already in table/list state (same pattern as useMCPServerModal.ts), name field prefilled with server-suggested "Copy {N} of {Name}" but editable.
- Clicking Clone opens prefilled modal directly (no confirmation step); trigger the name-suggestion fetch at that point.
- Error handling/post-submit UX identical to Edit (toast on error, yup inline validation, modal closes and new row appears on success).
- client_secret field stays masked/never populated with real value, same as Edit; handled server-side on clone.

Backend:
- New read-only endpoint GET /v1/mcp-configs/{id}/suggested-clone-name wrapping compute_base/allocate_name logic in clone_naming.py, no DB write, admin/maintainer-gated same as current clone access.
- Replace no-body POST /v1/mcp-configs/{id}/clone endpoint in routers/mcp_config.py with a new version taking a request body; replace MCPConfigService.clone with a new service method (e.g. clone_from_form) reusing create's validation/encryption helpers (_prepare_auth_config_for_persistence, _validate_auth_config_for_save, _ensure_auth_config_id); guarded by admin_access_only, same as create and the old clone endpoint.
- is_system/is_public copied from source record, not forced True (today's create forces both True).
- client_secret carry-over logic: reuse _prepare_auth_config_for_persistence with existing_encrypted_secret from source (via _get_stored_encrypted_secret) when field left masked/untouched; fresh encryption via _encrypt_confidential_client_secret when a new value is submitted.
- New record metadata set explicitly: fresh id, user_id/created_by = cloning user, is_active=True, usage_count=0, fresh date/update_date.
- No atomic name reservation at submit time; standard duplicate-name validation error on collision, no auto-increment retry.
- Remove dead code from the old no-body clone implementation; clone_naming.py stays in use, now called from the new suggested-name endpoint.
- Verification gap to confirm: in the external codemie-enterprise package (not in this repo), check whether any non-oauth2 auth-type field (e.g. SAML, idp_x509cert) needs the same mask-and-carry-over treatment as client_secret, or whether it's already plaintext today (no new regression).

Key existing code locations (already known from prior investigation, verify these are still accurate):
- src/codemie/service/mcp_config_service.py:339-408 (MCPConfigService.clone)
- src/codemie/service/mcp_config_service.py:232-258 (_prepare_auth_config_for_persistence)
- src/codemie/service/mcp_config_service.py:220-229 (_encrypt_confidential_client_secret)
- src/codemie/service/mcp_config_service.py:482 (_get_stored_encrypted_secret)
- src/codemie/service/mcp_config_service.py:559-563 (update() calling _prepare_auth_config_for_persistence)
- src/codemie/service/mcp_config_service.py:311-312 (create forcing is_system/is_public True)
- src/codemie/rest_api/routers/mcp_config.py:89-116 (POST /clone endpoint)
- src/codemie/service/mcp/clone_naming.py (compute_base, allocate_name)
- codemie-ui: src/pages/settings/administration/components/MCPServerModal/index.tsx
- codemie-ui: MCPServerActions.tsx:132-141 (ConfirmationModal clone wiring)
- codemie-ui: useMCPServerModal.ts:65-71 (Edit prefill pattern)

Also look for the external codemie-enterprise package if it's checked out anywhere alongside this repo (sibling directory) and inspect its SAML/auth-type handling for mcp configs if found. If not found locally, note that it's out of reach and flag as a documented verification gap.

---

## 2. Codebase Findings

### Existing Implementations

Backend (`codemie` repo, `src/codemie/service/mcp_config_service.py`):
- `MCPConfigService.clone(source_id, user)` (lines 339-408): finds source, deep-copies `source.config`, regenerates `auth_config["id"]` unconditionally, copies `_CLONE_COPIED_FIELDS = (description, server_home_url, source_url, logo_url, categories, required_env_vars, is_public)` (line 65-73), then builds a new `MCPConfig` row directly via `Session`, with `is_system=False` **hardcoded** (line 385, not copied from source), `usage_count=0`, `is_active=True`, fresh `id`/`created_by`/`user_id`/`date`/`update_date`. Does not call `create`'s validation/encryption helpers at all — bypasses `_prepare_auth_config_for_persistence`, `_validate_auth_config_for_save`, `_ensure_auth_config_id` entirely.
- `MCPConfigService.create` (lines 260-337): validates duplicate name, then `_strip_response_only_auth_metadata` → `_ensure_auth_config_id` → `_validate_auth_config_for_save` → `cls._prepare_auth_config_for_persistence(auth_config)` (no `existing_encrypted_secret`/`incoming_auth_config`, so a confidential OAuth2 config always gets freshly encrypted from a plaintext input). Forces `is_public=True`, `is_system=True` (lines 311-312).
- `MCPConfigService.update` (lines 436-514) + `_apply_config_field_update` (516-569): the only existing caller of `_prepare_auth_config_for_persistence` with `existing_encrypted_secret=_get_stored_encrypted_secret(mcp_config.config)` and `incoming_auth_config=_get_raw_auth_config(request.config)` — this is the exact mask-and-carry-over mechanism the task wants reused for clone, but today it operates on the *same* record (update), not a new one.
- `_prepare_auth_config_for_persistence` (232-258): non-confidential auth → strips `client_secret`; confidential + `incoming_auth_config is None` → always re-encrypts (would double-encrypt an already-encrypted value, confirmed risk below); confidential + incoming has a real new secret → encrypts it; confidential + incoming omits/blanks it → falls back to `existing_encrypted_secret` if given, else drops the field.
- `_get_stored_encrypted_secret` (187-196), `_encrypt_confidential_client_secret` (223-229), `_ensure_auth_config_id` (160-164, fills only a missing id — would keep the source's id and collide via the unique index if applied unconditionally to a clone), `_validate_auth_config_for_save` (199-214, raises 422 "Invalid auth_config"), `_is_auth_config_id_conflict` (80-100, maps the unique-index violation to 409).
- `_to_response` (756-792): strips `client_secret` from `auth_config` and sets `has_client_secret` for `oauth2` type before returning; this is what makes "masked" possible without a dedicated secret-handling field anywhere in the UI.
- `src/codemie/service/mcp/clone_naming.py`: `compute_base(source_name)` truncates for the `Copy N of <base>` width; `allocate_name(session, user_id, base)` is a pure read (`select(...).where(...).like(...)`) that returns the next free `N` — no write, so it is already safe to call from a read-only endpoint. Name uniqueness/allocation is scoped per `user_id`, not globally.
- `src/codemie/rest_api/routers/mcp_config.py:88-113`: `POST /mcp-configs/{config_id}/clone`, no body param, `dependencies=[Depends(authenticate), Depends(admin_access_only)]`, calls `MCPConfigService.clone(config_id, user)`.
- `src/codemie/rest_api/models/mcp_config.py`: `MCPServerConfigData` (97-156, includes `auth_config: dict[str, Any] | None`, raw dict — typed OAuth2/SAML models live only in enterprise), `MCPConfig` table model (159-246, `is_public`/`is_system` SQL fields, unique partial index `ix_mcp_configs_auth_config_id`), `MCPConfigCreateRequest` (252-273, no `is_system` field — client can't set it), `MCPConfigUpdateRequest` (276-296), `MCPConfigResponse` (299-319).

Frontend (`codemie-ui` repo):
- `MCPServerActions.tsx` (147 lines): row menu with Clone item (line 90-95) wired to `showCloneConfirmation` → `isCloneConfirmationVisible` state → `ConfirmationModal` (132-141) with header `Clone "{name}"?`, confirm text `Clone Server`, calling `confirmClone` → `onClone(server)` (62-72). No form/modal is shown pre-submission today.
- `MCPServerModal/index.tsx` (305 lines): shared Add/Edit modal. Header is `props.server ? 'Edit MCP Server' : 'Add MCP Server'` (line 92) — a third `mode`/title concept does not exist yet. Submit button text is `isSubmitting ? 'Saving...' : props.server ? 'Update Server' : 'Create Server Configuration'` (61-64) — no "Clone Server" case. The whole MCP `config` object (including `auth_config`) is edited as **one raw JSON textarea** (`serverConfig`, lines 223-240), not as structured fields — there is no dedicated `client_secret` input anywhere in this modal.
- `MCPServerModal/useMCPServerModal.ts` (127 lines): `useForm` with `yupResolver(mcpServerSchema)`; `useEffect` (65-71) does `reset(getFormValuesFromServer(server))` when `visible && server`, else `reset(getDefaultFormValues())` — this is the "same pattern" the task names for Clone prefill.
- `MCPServerModal/modalHelpers.ts`: `getFormValuesFromServer` (31-40) does `JSON.stringify(server.config, null, 2)` for the `serverConfig` field — since the backend's `_to_response` already stripped `client_secret` and only exposes `has_client_secret`, that boolean (not the secret) is what ends up in the editable JSON; this is the entirety of today's "masking" behavior, there is no separate masked-placeholder widget.
- `MCPServerActions.clone.test.tsx`: covers the current confirmation-popup flow (open → confirm → `onClone` called once, disabled during flight, closes on resolve). Will need rewriting once the confirmation step is removed.
- `useMCPManagement.ts` (hook used by `MCPManagementPage.tsx`): `handleCloneServer` (101-117) guards double-dispatch via `cloningRef`/`cloningIds` (per-row), calls `mcpStore.cloneConfig(server.id)`, toasts on error. `handleModalSubmit` (73-93) branches `editingServer ? updateConfig : createConfig`; a third "clone" branch does not exist yet.
- `store/mcp.ts`: `createConfig` (`POST v1/mcp-configs`, 204-225), `cloneConfig` (`POST v1/mcp-configs/{id}/clone`, no body, 239-265), `updateConfig` (`PUT v1/mcp-configs/{id}`, 283-308). No store method calls a name-suggestion endpoint today.
- `MCPManagementPage.tsx`: `canClone = isAdmin || isMaintainer` (line 74) — this is the UI-side admin/maintainer gate the new endpoints must match.

### Architecture and Layers Affected
- Backend: REST router (`rest_api/routers/mcp_config.py`), API models (`rest_api/models/mcp_config.py`), service layer (`service/mcp_config_service.py`, `service/mcp/clone_naming.py`). No new DB table/migration — the unique partial index already exists.
- Frontend: feature components (`MCPServerActions.tsx`, `MCPServerModal/index.tsx`, `useMCPServerModal.ts`, `modalHelpers.ts`), page-level hook (`useMCPManagement.ts`), state store (`store/mcp.ts`), validation (`mcpServerValidation.ts`).

### Integration Points
- Backend clone persistence goes through `Session(MCPConfig.get_engine())` directly today (not `.save()`); `create` uses `mcp_config.save()`. Any new `clone_from_form` reusing `create`'s helpers will need to decide which persistence path to use while still regenerating the id and catching `IntegrityError` → 409 the same way both existing paths do.
- `EncryptionFactory().get_current_encryption_service()` (class attribute `MCPConfigService.encryption_service`) is the only encryption boundary; no external HTTP/SDK call in this path.
- `codemie.enterprise.mcp_auth.dependencies` supplies `validate_auth_config_on_save`, `is_mcp_auth_enabled`, `has_any_credentials_for_auth_config`, `invalidate_credentials_for_auth_config`, `_is_missing_required_value` — this is the enterprise package boundary; its SAML/other-auth-type field definitions were not inspectable (see Section 8).
- Frontend talks to the backend only through `store/mcp.ts`'s thin `api.post/put` wrappers; no other service layer in between.

### Patterns and Conventions
- Backend: classmethod-based service (`MCPConfigService`), typed `ExtendedHTTPException` for 404/409/422/503, module-level private helper functions above the class for config-shape logic, `IntegrityError` → `_is_auth_config_id_conflict` → 409 pattern repeated in `create`, `update`, `clone`.
- Frontend: one shared modal component driven by `props.server` presence to switch between Add/Edit; `react-hook-form` + `yup` for validation; Valtio store (`mcpStore`) for list/pagination state with optimistic `unshift`/`findIndex` updates; per-row async-state tracking via a `Set` + a `useRef` guard against double submits (seen in both delete-via-confirmation and clone-via-`cloningIds`).

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/` was confirmed in the 2026-10-07 prior-task research to have no mention of clone-specific contract details; this remains the base guidance (API router patterns, service layer patterns, security patterns guides apply generically).
- `docs/superpowers/tasks/2026-10-05-clone-mcp-capability/` and `docs/superpowers/tasks/2026-10-07-clone-mcp-full-copy-remove-stripping/` are prior-task artifacts (spec.md, plan.md, technical-analysis.md, test logs) for the two backend changes that produced today's `clone` method; both are historical reference, not current contract.
- A root-level untracked file `clone-mcp-form-flow-task-description.md` (same content as this task's `task_context`, with the same acceptance criteria) already exists in the repo working tree — it is the task_context verbatim, not an independent source.

### Architectural Decisions
- The 2026-10-07 change deliberately made `clone` bypass `create` and copy everything verbatim with no re-validation/re-encryption, specifically because reusing `_prepare_auth_config_for_persistence` on an already-encrypted secret would double-encrypt it. This task's requirement to reuse that helper is only safe because it will pass `existing_encrypted_secret` explicitly (the `update`-path shape), not call it with no arguments the way `create` does.
- `is_system`/`is_public` in today's `clone`: `is_public` is copied (`_CLONE_COPIED_FIELDS`), but `is_system` is **hardcoded `False`**, not copied — this contradicts the task's framing that today's clone forces values "to True" like create; in fact clone's current `is_system` behavior is forced-False, and the task's acceptance criterion ("a cloned server that was system/public stays system/public") requires changing this hardcoded value to a copy, not removing a forced-True.

### Derived Conventions
- No `.ai-run/guides/` entry for MCP clone specifically; conventions are derived straight from `create`/`update`/existing `clone` as shown above.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/test_mcp_config_service_clone.py` (213 lines, pytest + `unittest.mock`, `Session`/`allocate_name`/`validate_auth_config_on_save`/`is_mcp_auth_enabled` all patched, `MCPConfig.find_by_id` patched per-call): covers name allocation, flag/field copying, verbatim secret/header/env/args/url copy with new `auth_config.id`, deep-copy independence, system-source cloneability, 404/409/other-IntegrityError handling, and that logs contain ids but never secret values. This whole file's assumptions (`MCPConfigService.clone("src-1", user)` with 2 args, no body/form, hardcoded `is_system=False`) will not match a `clone_from_form(source_id, request, user)` signature — expect a full rewrite.
- `tests/codemie/service/mcp/test_clone_naming.py`: unaffected, stays as-is (per 2026-10-07 analysis).
- `tests/codemie/rest_api/routers/test_mcp_config.py`: covers the current `/clone` route including 403 for non-admin — will need rewriting for a request body and the new suggested-name route (not located as an existing file; a new test module is likely needed for it).
- Frontend: `MCPServerActions.clone.test.tsx` (confirmation-popup lifecycle) and `useMCPManagement.test.ts` (hook behavior, not read in full here) both encode the current no-form clone flow and need rewriting. No existing frontend test exercises `MCPServerModal` in a "Clone" mode (title/submit-label branch does not exist).

### Testing Framework and Patterns
- Backend: `pytest`, heavy `unittest.mock.patch`/`MagicMock` for `Session`, no real DB in unit tests.
- Frontend: `vitest` + `@testing-library/react`, icon modules mocked via `vi.mock('@/assets/icons/....svg?react', ...)`, `ConfirmationModal` mocked to a bare `<dialog>` for behavioral assertions.

### Coverage Gaps
- No test currently exercises `_prepare_auth_config_for_persistence` being called with a **cross-record** `existing_encrypted_secret` (source record's secret applied to a brand-new row) — today it is only exercised same-record in `update`. This is new ground for the clone rewrite.
- No test exists for a `GET .../suggested-clone-name` endpoint (it doesn't exist yet).
- No frontend test exists for `MCPServerModal` rendering in a third ("Clone") mode, or for name-suggestion fetch triggering on menu click.

---

## 5. Configuration and Environment

### Environment Variables
- `MCP_AUTH_ENABLED` (checked via `is_mcp_auth_enabled()`) gates the `inactive_auth_config` response warning and whether `validate_auth_config_on_save` enterprise validation actually runs; not clone-specific, applies equally to create/update/the new clone-from-form path.

### Configuration Files
- None specific to this feature. No new migration needed — `ix_mcp_configs_auth_config_id` unique partial index already exists and already handles the "clone must regenerate auth_config.id" requirement via the existing 409-on-conflict path.

### Feature Flags and Deployment Concerns
- `useMcpEnabled()` hook / `isMcpFeatureEnabled` gates whether the whole MCP management page renders at all (`MCPManagementPage.tsx`); unrelated to clone specifically but is the outer flag this feature lives behind.
- This is a cross-repo change (`codemie` backend + `codemie-ui` frontend, both touched by this task); sequencing the two deployments matters if the old no-body `POST /clone` is removed before the UI stops calling it, or vice versa for the new endpoints.

---

## 6. Risk Indicators

- Speculative: reusing `_prepare_auth_config_for_persistence` with `incoming_auth_config=None` (the way `create` calls it) on a clone would re-encrypt an already-stored secret per the confidential-OAuth2 branch at line 245-247 — the new `clone_from_form` method must call it the `update`-style way (passing both `existing_encrypted_secret` and `incoming_auth_config`), not the `create`-style way, or secrets will double-encrypt and become unusable.
- Speculative: reusing `_ensure_auth_config_id` unmodified is insufficient for a clone, because it only fills a *missing* id (line 160-164) and would otherwise keep the source's `auth_config.id`, guaranteeing a 409 on the unique index for any source that has an `auth_config`. The existing `clone` method's unconditional-regeneration logic (lines 368-370) will need to be preserved explicitly in the new method, separate from `_ensure_auth_config_id`.
- The task's own framing ("is_system/is_public copied from source, not forced True") does not match today's code precisely: `is_public` is already copied today; `is_system` is today hardcoded to `False` (not forced `True` as framed) — the real change needed is making `is_system` copy from source instead of being hardcoded, which is a materially different starting point than described.
- `MCPConfigCreateRequest` has no `is_system` field — if `clone_from_form`'s implementation shape is "an optional override on create's request/service method" (shape (a) in the task), a new field or a separate request model is unavoidable; this is a design decision for spec/plan, not resolved by this research.
- The frontend `MCPServerModal` edits the entire `config` (including `auth_config`) as one raw JSON textarea with no structured `client_secret` field — "masking" today is incidental (the backend simply never sends the real secret, only `has_client_secret`). A Clone-mode prefill that round-trips this JSON textarea must preserve that same incidental masking (i.e., the prefilled JSON must still omit `client_secret`/show only `has_client_secret`) without introducing a new UI control; this constrains how "stays masked, same as Edit" can be implemented without broader form rework.
- No `GET /v1/mcp-configs/{id}/suggested-clone-name` endpoint exists yet; `allocate_name`'s per-user scoping and `MAX_NAME_LENGTH`/`COPY_NUMBER_MAX_WIDTH` truncation logic in `clone_naming.py` must be reused verbatim (not reimplemented) to keep the suggestion consistent with whatever naming conflict check runs at submit time.
- Both the old no-body clone endpoint/service method and significant test files (`test_mcp_config_service_clone.py`, `MCPServerActions.clone.test.tsx`) are deletion/rewrite targets; expect a large test-file churn relative to the amount of new logic.
- **Confirmed verification gap**: the `codemie-enterprise` package (SAML/other MCP auth-type field definitions, e.g. `idp_x509cert`) is not installed or checked out anywhere reachable from this environment (`pip show`/`import codemie_enterprise` fails, no sibling checkout found under the user's Workspace directories). The task's own verification gap about whether a non-oauth2 auth-type field needs the same mask-and-carry-over treatment as `client_secret` could not be resolved by this research and remains open — see Section 8.

---

## 7. Summary for Complexity Assessment

This is a coordinated two-repo change. Backend touches three files in `codemie` (`rest_api/routers/mcp_config.py`, `service/mcp_config_service.py`, possibly `rest_api/models/mcp_config.py` for a new clone-request model) plus a full rewrite of `tests/codemie/service/test_mcp_config_service_clone.py` and the router's clone tests, and a new test module for the suggested-name endpoint. Frontend touches `MCPServerActions.tsx` (remove confirmation wiring), `MCPServerModal/index.tsx` + `useMCPServerModal.ts` + `modalHelpers.ts` (add a third Clone mode, trigger name-suggestion fetch), `useMCPManagement.ts` and `store/mcp.ts` (new clone-from-form submit path, new name-suggestion fetch), plus rewriting `MCPServerActions.clone.test.tsx`. Layers touched: backend router, service, model; frontend component, hook, store, validation.

Technical novelty is moderate, not high: the naming (`clone_naming.py`), the mask-and-carry-over mechanism (`_prepare_auth_config_for_persistence` with `existing_encrypted_secret`), and the JSON-textarea prefill pattern (`useMCPServerModal.ts`) all already exist and are being recombined, not invented. The main source of complexity is correctness under combination — avoiding double-encryption, avoiding the unconditional-id-regeneration regression, and getting `is_system` to copy from source where it is currently hardcoded false. Test coverage posture is weak for the new combination (cross-record secret carry-over, a third modal mode) even though the underlying primitives are each separately tested today.

The dominant risk is the unresolved external dependency: `codemie-enterprise`'s SAML/other auth-type field handling could not be inspected locally (package not installed, no sibling checkout found), so the acceptance criterion asking to confirm or implement non-oauth2 mask-and-carry-over cannot be closed by filesystem research alone and must be escalated or scoped out explicitly in the spec.

---

## 8. External References

- `~/Documents/Workspace/codemie-dev/codemie-enterprise` and any other sibling checkout under `~/Documents/Workspace/` and `~/Documents/Workspace/codemie-dev/`: **unresolved**. No directory named `*enterprise*` exists under `codemie-dev/`, and `codemie-dev/`'s only siblings are `codemie`, `codemie-onboarding`, `codemie-sdk`, `codemie-ui`. `python3 -c "import codemie_enterprise"` fails with `ModuleNotFoundError` (not installed in this environment's Python either), and no `codemie_enterprise*` path was found searching the filesystem. The task's own instruction to "look for the external codemie-enterprise package if checked out alongside this repo" and inspect its SAML/auth-type handling cannot be fulfilled here — this is the same gap the task_context itself names and asks to flag. `codemie`'s `pyproject.toml` lists `codemie-enterprise = {version = "2.3.44", source = "codemie-artifact-registry-gcp", optional = true}` as a private-registry dependency, confirming it is intentionally not vendored into this checkout.
- `clone-mcp-form-flow-task-description.md` (repo root, untracked): resolved. Its content is identical to the `task_context` given to this research (same scope, out-of-scope, and acceptance-criteria sections); it is the task_context's own source file, not an independent reference, so no additional facts were drawn from it beyond what task_context already states.
- `docs/superpowers/tasks/2026-10-07-clone-mcp-full-copy-remove-stripping/` and `docs/superpowers/tasks/2026-10-05-clone-mcp-capability/`: resolved, read as historical prior-task context (not named explicitly by task_context as a source of truth, but directly relevant); findings folded into Sections 2-3 above.
