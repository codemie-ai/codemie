# Technical Research

**Task**: idempotent put assistant datasource integration prompt_variables encryption sanitize_for_save drop_missing_context
**Generated**: 2026-10-08
**Research path**: codegraph

---

## 1. Original Context

task_context: EPMCDME-15310 — Idempotent write path for apply [idempotent-write]. Backend-only work in the `codemie` repo (Python/FastAPI). Story: as a platform engineer applying the same document repeatedly through a pipeline, re-applying an unchanged document against the same id must make no server-side change. Acceptance criteria: (1) unchanged re-PUT by id leaves the stored entity identical — no field added, removed or rewritten; (2) a credential field sent with the platform's stored mask leaves the stored credential untouched; (3) each supported datasource type re-submitted unchanged through its type-specific route is unchanged; (4) documented intentional server-side rewriting (e.g. invalid datasource context entry dropped, masked prompt variable restored) still happens exactly, not as an undocumented side effect; (5) negative: a POST with no id always creates a new entity even if an identical one exists; (6) no status-code, response-schema or validation-rule change on any existing PUT/POST — silent drop/restore stays silent. Scope: audit every server-side rewrite on the PUT path entity by entity — assistants (_restore_existing_encrypted_values, _encrypt_prompt_variables_in_request, _filter_invalid_datasources / AssistantService.drop_missing_context, MCPAccessControlService.sanitize_for_save, guardrail assignment sync), workflows, skills, categories, integrations (project_settings.py, user_settings.py, branching by credential_type; settings.py only has /settings/transfer), and the ~15 type-specific datasource POST/PUT routes under /v1/index/knowledge_base/* and /v1/application/{app_name}/index/*. Fix where a valid unchanged document is rewritten; document the intentional ones. A contract-baseline characterization test (status codes, response shapes, validation failures, silent rewrites; one case per datasource route) must be committed BEFORE any implementation change. New idempotency tests per router (AC1), extend test_assistant.py mask test (AC2), one test per datasource type in test_index.py (AC3), test_create_always_new.py (AC5). Out of scope: identity/name resolution, combined create-or-update endpoint, bulk update, export, diff/plan, Node IaC/SDK. Facts already observed (verify, do not trust): sanitize_for_save appears validate-only (returns list(servers)); prompt-variable encryption on save may be non-deterministic (salted?) so re-PUT of the same plaintext could change stored ciphertext — check each encryption backend; update path runs repository.update then _filter_invalid_datasources then assistant.update() again (possible double-write/updated_date churn); check updated_date/update_date, version creation (create_new_version/has_configuration_changes), and other server-managed fields that change on every PUT. Full story: /Users/Yauheni_Dzenisenka/Development/codemie-dev/maia-stories/sdk/2-iac-plan-apply/1-idempotent-write/story.md

---

## 2. Codebase Findings

Note: the codegraph budget for this run was 2 explore calls; the function bodies of the assistant.py rewrite helpers, the datasource routers and the integration routers were not returned and are NOT verified here. Only what is listed below was observed.

### Existing Implementations
- `src/codemie/service/mcp/access_control.py` — `MCPAccessControlService.sanitize_for_save` (line 141): calls `validate_on_save(servers)` then returns `list(servers)`. VERIFIED validate-only: it raises `ValidationException` on violation (restricted mode `mcpCustomServersDisabled`, duplicate `mcp_config_id`, missing/inactive/non-public catalog entry) and never mutates. `strip_inline_config` and `_strip_one` are no-ops ("inline overrides are kept"). `filter_for_runtime` does silent drops but only at runtime, not on save. Callers: `create_assistant`, `update_assistant`, `ask_virtual_assistant` (routers/assistant.py), `create_skill`, `update_skill` (service/skill_service.py). Codegraph reports no covering tests for `sanitize_for_save`.
- `src/codemie/rest_api/routers/assistant.py` — contains `_filter_invalid_datasources`, `_encrypt_sensitive_prompt_variables`, `_encrypt_prompt_variables_in_request`, `_restore_existing_encrypted_values` (they use `EncryptionFactory`/`get_current_encryption_service`, `encrypt`). Bodies not retrieved. `_get_masked_prompt_variables` (~line 2627) replaces sensitive non-empty `default_value` with `SENSITIVE_VALUE_MASK` (`codemie.core.constants`) for validation output.
- `src/codemie/rest_api/models/assistant.py:1311` — `update` (the assistant model update; story/task says it is called again after `_filter_invalid_datasources`). Not read.
- `src/codemie/service/encryption/` — `BaseEncryptionService` with backends: `PlainEncryptionService` (identity), `Base64EncryptionService` (deterministic), `GCPKMSEncryptionService` (KMS `client.encrypt`, base64 of ciphertext), `AWSKMSEncryptionService`, `AzureKMSEncryptionService`, `VaultEncryptionService`. Selected by `get_encryption_service` factory.
- Other: `AssistantService.drop_missing_context`, guardrail sync, workflow/skill/category routers, `project_settings.py`, `user_settings.py`, and the datasource routers (`routers/index.py`) were not surfaced.

### Architecture and Layers Affected
Router layer (`rest_api/routers/assistant.py`, `index.py`, `workflow.py`, `skill.py`, `category.py`, `project_settings.py`, `user_settings.py`), service layer (`service/mcp/access_control.py`, `service/skill_service.py`, `service/encryption/*`), model layer (`rest_api/models/assistant.py`, `core/models.py` with `Application`, `CreatedByUser`; SQL models extend `BaseModelWithSQLSupport` with inherited `date`/`update_date`).

### Integration Points
Encryption factory (multiple KMS/vault backends, external calls); `MCPConfig` catalog lookup (`MCPConfig.get_by_ids`); `customer_config` flag `mcpCustomServersDisabled`.

### Patterns and Conventions
Layered router -> service -> repository per `.ai-run/guides/architecture/layered-architecture.md`. Mask constant `SENSITIVE_VALUE_MASK`. Sonar cyclomatic complexity gate of 15 (CLAUDE.md).

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/` exists. Relevant (not read in this run): `api/rest-api-patterns.md`, `api/endpoint-conventions.md`, `architecture/service-layer-patterns.md`, `testing/testing-api-patterns.md` (the story mandates TestClient-boundary testing), `development/security-patterns.md`, `integration/mcp-integration.md`.

### Architectural Decisions
Story rescoped 2026-09-25: no identity resolution; client picks PUT vs POST. Story design claims `_restore_existing_encrypted_values` is idempotent by construction (unverified here).

### Derived Conventions
Sensitive values masked with a single constant mask; silent filtering preferred over validation errors (AC6).

---

## 4. Testing Landscape

### Existing Coverage
- Encryption: `tests/codemie/service/encryption/test_azure_encryption_service.py`, `test_gcp_encryption_service.py`, plus others for the factory.
- `tests/codemie/rest_api/routers/test_assistant.py` (existing mask test to extend, per story) and `test_index.py` (named by the task) not read.
- A found `test_idempotent_put_returns_same_result` relates to user profile upsert, not these entities; `test_attach_skill_idempotent` relates to skills attach. Neither is a template for stored-entity byte-identity.

### Testing Framework and Patterns
pytest with TestClient at router boundary (per guide). Not further verified.

### Coverage Gaps
`sanitize_for_save` has no covering tests per codegraph. No byte-identity re-PUT tests were found anywhere.

---

## 5. Configuration and Environment

### Environment Variables
KMS config used by GCP backend: `GOOGLE_KMS_PROJECT_ID`, `GOOGLE_KMS_REGION`, `GOOGLE_KMS_KEY_RING`, `GOOGLE_KMS_CRYPTO_KEY`. Other backends' vars not retrieved.

### Configuration Files
`codemie.configs.config`; `customer_config` component flag `mcpCustomServersDisabled`.

### Feature Flags and Deployment Concerns
`mcpCustomServersDisabled` switches validation behavior on save. Encryption backend choice is deployment-specific, so idempotency must hold per backend.

---

## 6. Risk Indicators

- Encryption determinism is deployment-dependent: Plain and Base64 are deterministic; GCP KMS symmetric encrypt is generally randomized (non-deterministic ciphertext), and AWS/Azure/Vault were not inspected. If unmasked plaintext is re-encrypted on each PUT, stored ciphertext can change (the task's hypothesis, plausible but not confirmed in the code seen).
- `sanitize_for_save` is confirmed validate-only; the story's "MCP entries are sanitised" premise is outdated for the rewrite audit. It can still reject (not rewrite) valid-before inputs if catalog state changes.
- No codegraph coverage for `sanitize_for_save` tests; the router bodies for `_restore_existing_encrypted_values`, `_filter_invalid_datasources`, `drop_missing_context` and the ~15 datasource routes were not read; downstream stages must read them.
- Speculative: the double write (`repository.update` then `_filter_invalid_datasources` then `assistant.update()`) may bump `update_date` on every PUT; needs checking against the `BaseModelWithSQLSupport` update_date handling.
- Shared utilities across assistants, skills (skill_service calls sanitize_for_save), workflows, datasources: cross-cutting blast radius.
- Contract baseline must be committed before changes (AC6); no pre-existing template.
- Sonar complexity limit 15 for any added code.

---

## 7. Summary for Complexity Assessment

The change touches the router and service layers across assistants, skills, workflows, categories, integrations and ~15 datasource routes, plus the encryption service layer. The file change surface is potentially wide (several routers plus tests per router), but this run verified only the MCP sanitizer (validate-only, so no fix likely needed there) and the encryption backend inventory. The remaining rewrite helpers were not read.

Technical novelty is moderate: no existing byte-identity or re-PUT tests were found, so all idempotency tests and the contract baseline are net-new. The encryption backends differ in determinism, which makes the prompt-variable path the main security-sensitive risk; mask-restore logic is credential handling.

Test posture is weak for the targeted paths (no sanitize_for_save tests found; router tests not inspected). Key risks are non-deterministic ciphertext, double-write/update_date churn, and unaudited datasource routes.

---

## 8. External References

Resolved: `/Users/Yauheni_Dzenisenka/Development/codemie-dev/maia-stories/sdk/2-iac-plan-apply/1-idempotent-write/story.md` (read in full). Facts the task needs:
- Size L (24/36), recommended flow sdlc-standard; Python backend only; Node IaC out of scope.
- Identity rescoping (2026-09-25): caller supplies id for update (PUT) or none for create (POST); no EntityResolver, no name resolution, no combined endpoint, no bulk-update.
- Story rewrite audit outcomes: `_restore_existing_encrypted_values` = "already idempotent by construction (masked input -> restore stored value; unmasked -> store new value); documented as intentional, not changed"; `_filter_invalid_datasources` = audited per datasource type, fix if a valid unchanged document is dropped; `sanitize_for_save` = audited, kept if it only touches invalid input.
- Integrations: `settings.py` has only `POST /v1/settings/transfer`; real surface is `project_settings.py` and `user_settings.py`, branching by `credential_type` (SCHEDULER, LITE_LLM, GIT, WEBHOOK, ...). Backend already skips credential values equal to the mask on write.
- Tests per story: `test_idempotent_write.py` per entity router (new); extend `tests/codemie/rest_api/routers/test_assistant.py` mask test (AC2); one idempotency test per datasource type in `tests/codemie/rest_api/routers/test_index.py` (AC3); `test_create_always_new.py` (AC5); contract baseline (AC6) committed first, passing on current code, one case per datasource route, stays green and unedited; only AC1-AC3 tests expected red initially. Follow `.ai-run/guides/testing/testing-api-patterns.md`.
