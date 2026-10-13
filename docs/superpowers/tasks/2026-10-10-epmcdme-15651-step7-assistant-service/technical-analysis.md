# Technical Research

**Task**: assistant inline-credentials prompt-variable encryption service
**Generated**: 2026-10-10
**Research path**: codegraph

---

## 1. Original Context

Implement delivery step 7, package `assistant`, of Jira story EPMCDME-15651 ("Split oversized REST API router modules into smaller per-concern modules"). Steps 0-6 are done on branch EPMCDME-15651_split-router-modules: the five big router modules are now packages (routers/assistant/, index/, analytics/, projects/, conversation/) and request/response classes live in rest_api/models. Ticket step 7: "Move domain helpers into services, one merge request per package." For `assistant` the ticket says: "Move the inline-credential detection and prompt-variable encryption helpers into `service/assistant/`." Today these live in src/codemie/rest_api/routers/assistant/_inline_credentials.py (~208 lines) and as the `_encrypt_*` / `_restore_existing_encrypted_values` helpers in src/codemie/rest_api/routers/assistant/management.py (verify the exact helper set; also check `_shared.py`, `_ask.py`, `_sub_assistants.py` and `routers/utils.py` for further domain (non-HTTP) helpers that belong with them, but keep scope to what the ticket names unless a helper is inseparable from it).
Unlike steps 1-6 this is NOT a pure file move: handlers call the new service and mock.patch targets in tests must move. Constraints (from the ticket and the user-approved criteria): no API behavior change (the OpenAPI snapshot and route-precedence guards in tests/codemie/rest_api/router_guards/ must stay byte-identical); modules under service/ and rest_api/handlers/ must NOT import from rest_api/routers/ (except the existing routers/utils.py helpers); no router imports underscore-prefixed private helpers from another router module; ruff max-complexity 16; security-relevant code moves, so the eventual MR must ask a reviewer to post `/sanity`. Follow Clean Code/SOLID/DRY/KISS and .ai-run/guides (architecture/layered-architecture.md, service-layer-patterns.md, project-structure.md, security-patterns.md).
Ticket body and user-resolved criteria: docs/superpowers/tasks/2026-10-08-epmcdme-15651-split-router-modules/{ticket,spec}.md. Handoff notes: local/router-split/HANDOFF.md (sections for step 2 describe the assistant package and its patch-target map).
Research must report, with path:line evidence: (1) the exact set of helpers to move and who calls them (routers, services, handlers, tests, sibling repos /home/taras_spashchenko/EPAM/cm/codemie-enterprise etc.); (2) existing service/assistant/ contents and naming conventions (how services are exposed: classes vs module functions, singletons); (3) every test file and mock.patch literal that targets the helpers' current module paths, and tests that would go dark if mis-aimed; (4) whether any helper depends on router-only things (HTTPException, Request, Depends, router constants) that would drag HTTP concerns into service/ and how existing services handle exceptions (typed ExtendedHTTPException? see .ai-run/guides/development/error-handling.md); (5) circular-import risks when routers.assistant imports service.assistant; (6) the security-relevant behavior that must be preserved byte-for-byte (encryption, masking, restore semantics) and existing test coverage of it; (7) whether acceptance criterion 5 (no service/ or handlers/ import of routers/ except routers/utils.py) already holds repo-wide today.

---

## 2. Codebase Findings

Tools: Read, Glob, codegraph. No grep or shell, nothing run. Counts come from reading; tree-wide absence is unproven unless stated. Paths are under `src/codemie/` unless prefixed `tests/`, `docs/` or a repo name.

### Existing Implementations

Helper set: 10 functions in 3 router modules, no class, no module constant. The ticket scope names 9 (6 + 3); the 10th is the only other consumer of a moved helper.

| Helper | Defined | Called from |
|---|---|---|
| `_validate_assistant_inline_integrations` | `rest_api/routers/assistant/_inline_credentials.py:19` | `marketplace.py:126` (assistant), `:137` (each sub-assistant); imported `marketplace.py:36-38` |
| `_get_masked_prompt_variables` | `_inline_credentials.py:44` | `_inline_credentials.py:39`; `catalog.py:441` (imported `catalog.py:37-39`) |
| `_check_toolkit_credentials :79`, `_check_object_credential_values :109`, `_check_mcp_server_credentials :140`, `_build_validation_result :196` | `_inline_credentials.py` | same file only (`:28`, `:89`, `:97`, `:149`, `:31`, `:34`) |
| `_encrypt_sensitive_prompt_variables` (on `Assistant`) | `rest_api/routers/assistant/management.py:386` | `management.py:121` in `_build_assistant_from_create_request`, called at `:226` by `create_assistant` |
| `_encrypt_prompt_variables_in_request` | `management.py:407` | `management.py:277` in `update_assistant` |
| `_restore_existing_encrypted_values` | `management.py:428` | `management.py:282` in `update_assistant` |
| `_mask_sensitive_prompt_variables` (10th) | `rest_api/routers/assistant/catalog.py:429` | `catalog.py:231` in `_build_assistant_detail_response` (by-id route `:256`, by-slug route `:270`) |

- `_inline_credentials.py` (208 lines) imports only `Assistant`, `InlineCredential` and `SettingType` (`:15-16`); function-local `SENSITIVE_VALUE_MASK` import at `:55`. `management.py` helpers use function-local imports of `SENSITIVE_VALUE_MASK` and `EncryptionFactory` (`:394-395`, `:415-416`, `:438`).
- No other caller found (codegraph call edges plus reading; not grep). The ticket lists only `_get_assistant_by_id_or_raise` and `_check_user_can_access_assistant` as cross-router imports; both are public in `routers/utils.py:103,118`. Import blocks of `routers/assistant/{execution,generation,versions,toolkits,health}.py` were read and import none of the helpers (function-local imports inside `execution.py` bodies not read).
- Outside the ticket scope: `_shared.py` (192 lines) holds `_get_assistant_by_slug_or_raise :29`, `_validate_remote_entities_and_raise :55`, `_track_assistant_management_metric :76`, `_raise_assistant_operation_error :145`, `_index_marketplace_assistant :161`; `_ask.py` (413 lines) is execution plumbing (`Request`, `BackgroundTasks`, handler calls); `_sub_assistants.py` (230 lines) is publish orchestration; `routers/utils.py` has no credential or prompt-variable code; the other `management.py` helpers (`_update_source_clone_count :60`, `_filter_invalid_datasources :373`, `_track_mcp_usage_* :452-481`) are unrelated.
- Decrypt half is elsewhere: `service/assistant_service.py:933` `AssistantService._decrypt_assistant_prompt_variables` (function-local `EncryptionFactory :941`; per-variable try/except that logs and keeps the ciphertext `:953-955`), called from `get_system_prompt :268-270`.
- Related but separate: `service/assistant/assistant_prompt_variable_mapping_service.py` (user-level overrides; own encrypt/decrypt/mask methods, `MASKED_VALUE = "*" * 10 :36`, `EncryptionFactory` built in `__init__ :41`, module singleton `:227`); `service/inline_credentials_collector.py` (workflow variant with different rules: no USER-pinned check, adds `mcp_integration_alias`; used by `service/workflow_config/workflow_marketplace_service.py:30`).
- `src/external/deployment_scripts/preconfigured_assistants.py:236` builds `Assistant(...)` directly and copies no `prompt_variables`; not a caller.

### Architecture and Layers Affected

REST router layer (`routers/assistant/{_inline_credentials,management,catalog,marketplace}.py`), service layer (`service/assistant/`, 16 modules plus `__init__`), models (`rest_api/models/assistant.py`: `PromptVariable :275-284`, `InlineCredential :461-476`), encryption services (`service/encryption/`), tests under `tests/codemie/rest_api/routers/` and `tests/codemie/service/assistant/`.

### Integration Points

- Router wiring: `routers/assistant/__init__.py:28-41` (`APIRouter(tags=["Assistant"], prefix="/v1", dependencies=[])`, 8 `include_router`). `management.py` already imports `service.assistant.assistant_repository` and `category_service` (`:49-50`), so `service/assistant/__init__.py` (`:15` imports `virtual_assistant_service`) is already on the router import path. `rest_api/models/assistant.py:35` imports `core.constants` at module level (which imports `LLMService` and builds `ModelTypes`, `core/constants.py:17-18,84`) and `:55` imports `models.settings`.
- Call timing: in `update_assistant` the encrypt and restore calls (`management.py:276-282`) precede the `try` at `:284`; in create, `_build_assistant_from_create_request` (`:226`) runs outside the `try` of `_save_new_assistant` (`:143`). Encrypt errors therefore propagate unwrapped.
- `EncryptionFactory` (`service/encryption/encryption_factory.py:36-69`) builds a new service from `config.ENCRYPTION_TYPE` (`:63`) on every call; cloud constructors do network work (`azure_encryption_service.py:36` `get_key`, `vault_encryption_service.py:63` `is_authenticated`). The helpers resolve it only after their empty-list return.
- Cross-repo contract: `core/constants.py:61` `SENSITIVE_VALUE_MASK = "********"`, mirrored in `codemie-ui/src/constants/index.ts:154`; the UI echoes the mask on update and the backend restores on equality.
- Sibling repos: `codemie-enterprise` documents zero coupling to core (`src/codemie_enterprise/plugin/config.py` docstring "no imports from codemie.*"); a codegraph query there for the helper names found nothing relevant. `codemie-mcp-connect-service` is not indexed; `codemie-ui` has no Python import.
- AC 5 state: the three offenders named in the ticket are gone: `service/budget/budget_service.py:51-56` and `project_budget_service.py:60` import only under `TYPE_CHECKING` from `rest_api.models.*`; `rest_api/handlers/assistant_handlers.py:26-27` imports `ToolCallResumeRequest` under `TYPE_CHECKING` from `models.assistant`, and its only router import is `routers.utils.run_assistant_in_thread_pool` (`:58`, the allowed exception). `hedged_handler.py` (lines 1-60 read) imports no router. Step 2 `spec.md:46` states AC 5 for the whole of `service/` and `handlers/`; HANDOFF `:133`, `:182`, `:270`, `:355` record empty package-specific AC 5 regexes at steps 3-6. A fresh repo-wide scan was not possible here.

### Patterns and Conventions

- `service/assistant/` files: `assistant_*_service.py`, `category_service.py`, `credential_validator.py`, `assistant_integration_validator.py`, `sub_assistant_validator.py`, `datasource_cleanup.py`, `virtual_assistant_service.py`, `assistant_repository.py`, `assistant_engine_builder.py`. `__init__.py:15-20` re-exports only `VirtualAssistantService` and `VIRTUAL_ASSISTANT_PREFIX`.
- Three exposure styles: stateless class with `@staticmethod`/`@classmethod` (`AssistantIntegrationValidator :39`, `CredentialValidator :41`); class plus module singleton (`assistant_prompt_variable_mapping_service :227`, `category_service`, `assistant_user_interaction_service`, `assistant_service` at `assistant/assistant_service.py:147`); module functions (`datasource_cleanup.py:37`).
- Name trap: two classes named `AssistantService`: `service/assistant_service.py` (runtime, imported by `management.py:51`) and `service/assistant/assistant_service.py:24` (template cache).
- Exceptions: `ExtendedHTTPException` (`core/exceptions.py:59`) is a plain `Exception` subclass, not FastAPI's; services already raise it (`service/assistant/category_service.py:29`, `service/budget/budget_service.py:33`, `service/conversation_service.py:36`). The 10 function bodies reference no `HTTPException`, `Request`, `Depends`, `status`, `logger` or router constant, and raise nothing; `encrypt()` errors from providers propagate.
- Typing: `_inline_credentials.py` uses bare `list`, untyped params and no `from __future__ import annotations`; `.ai-run/guides/standards/code-quality.md:19` asks for `X | None`, parameterized generics and the future import in new modules.

---

## 3. Documentation Findings

### Guides and Architecture Docs

Read: `layered-architecture.md` (HTTP in routers, orchestration in services), `service-layer-patterns.md` (explicit user/IDs, feature-scoped services), `project-structure.md`, `error-handling.md` (typed exceptions, sanitized failures), `security-patterns.md` (no prompt-variable or encryption section), `code-quality.md:36-46` (new concern, new module, one noun), `quality-gates.md:99-136` (`## Test harness` block, `/sanity`), `security/README.md:18-35` (`/sanity` wording), `testing-service-patterns.md`. No guide cites the helper files (greps unavailable; unverified).

### Architectural Decisions

`ticket.md:38` (move into `service/assistant/`), `:54` (step 7, one MR per package), `:67` (AC 5), `:69` (logic moves in separate MRs), `:70` (AC 8). Step 2 `spec.md:56` non-goal "Moving helpers into `service/` (step 7)"; its `:65` fallback `_prompt_variables.py` was not needed (HANDOFF `:83`).

### Derived Conventions

Patch the module whose code resolves the name; `<Name>.<attr>` keeps its tail (HANDOFF `:66-76`); function-local imports in moved bodies were kept verbatim in steps 2-6.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/rest_api/routers/test_inline_credentials.py` (206 lines): imports 5 names from `routers.assistant._inline_credentials` (`:19-25`); tests at `:66` object values, `:107`/`:120` USER-pinned vs PROJECT MCP, `:129` toolkits, `:164` result builder, `:180` full validation (asserts `is_valid` and count only; its mock server has no `settings`).
- `tests/codemie/rest_api/routers/test_assistant_marketplace.py` (763 lines): 4 patches of `codemie.rest_api.routers.assistant.marketplace._validate_assistant_inline_integrations` at `:129`, `:189`, `:264`, `:319`; its other `marketplace.*` patches stay.
- `tests/codemie/rest_api/routers/test_assistant.py` (1357 lines): `:1068` patches `catalog._mask_sensitive_prompt_variables` inside `_enrichment_patches :1060`, used by one test (`:1089`). `TestCreateAssistantSlug :645`, `TestAssistantMcpGovernance :800`, `TestCreateAssistantCloneTracking :900` call `create_assistant`/`update_assistant` without prompt variables, so encrypt/restore run only their early returns.
- `test_assistant_reactions.py` (96 lines) and `test_assistant_filter_invalid_datasources.py` (41 lines) patch or import nothing in scope. HANDOFF patch map (`:65-76`) lists no `_encrypt_*`, `_restore_*` or mask patch.
- Unread (map says unaffected): `test_assistant_users`, `_sort_params`, `_plugin_tools`, `test_toolkit_settings_config`, `test_system_prompt_validation`, `test_tool_call_resume`, `test_assistant_mapping`, `tests/unit/routers/test_assistant_file_attachment.py`.
- Neighbours: `tests/codemie/service/assistant/test_assistant_prompt_variable_mapping_service.py`; `tests/codemie/service/encryption/` (factory, GCP, Vault, Azure).

### Testing Framework and Patterns

pytest, `unittest.mock.patch`, `httpx.AsyncClient(ASGITransport(app))` with `app.dependency_overrides[authenticate]`, handlers also called as plain functions. Service tests live in `tests/codemie/service/assistant/` (21 test files plus `__init__.py`). Guards: `poetry run pytest tests/codemie/rest_api/router_guards -n 0 --show-capture=no -q` (baselines `router_guards/baselines/openapi.json`, `route_precedence.json`).

### Coverage Gaps

No test calls `_encrypt_sensitive_prompt_variables`, `_encrypt_prompt_variables_in_request`, `_restore_existing_encrypted_values` or `_get_masked_prompt_variables` (codegraph reports no covering test for the restore and mask functions; none found by reading). No test creates or updates an assistant with a sensitive variable. Coverage of `AssistantService._decrypt_assistant_prompt_variables` not located. Inline-credential types `mcp_environment_vars`, `mcp_auth_token`, `mcp_inline_config_env` are not asserted individually.

---

## 5. Configuration and Environment

### Environment Variables

`ENCRYPTION_TYPE` (`plain`, `gcp`, `aws`, `azure`, `base64`, `vault`; unset or invalid falls back to plain, `encryption_factory.py:61-69`). Provider settings read by constructors: `AZURE_KEY_VAULT_URL`, `AZURE_KEY_NAME`, `GOOGLE_KMS_PROJECT_ID/REGION/KEY_RING/CRYPTO_KEY`, `AWS_KMS_KEY_ID`, `AWS_KMS_REGION`, `VAULT_URL`, `VAULT_TOKEN`, `VAULT_NAMESPACE`, `VAULT_TRANSIT_KEY_NAME`, `VAULT_TRANSIT_MOUNT_POINT`.

### Configuration Files

`pyproject.toml`: ruff line length 120 (`:213`), select `E,F,B,N,C4,G,T20,RSE,SIM,C,W,PERF,ISC` (`:223`), ignores incl. `G004`, `B904` (`:225-238`), `max-complexity = 16` (`:248`); per-file `E501` ignores for `*template*`, `*prompt*`, `*_vars*`, `*_tool*` names (`:258-261`). Makefile targets `ruff`, `license-check`, `test`, `test-harness`, `verify`.

### Feature Flags and Deployment Concerns

No feature flag gates these code paths. No pipeline config: the MR needs `/sanity` and a `## Test harness` block (`quality-gates.md:111`).

---

## 6. Risk Indicators

- No direct test for encrypt, restore, mask; `test_assistant.py:1068` patches the masker out, so regressions stay green.
- Silent pass: by reading, `test_assistant_marketplace.py:151` passes with an inert `_validate_assistant_inline_integrations` patch (4 literals); `MagicMock` assistants validate clean.
- Order and errors: encrypt then restore (`management.py:276-282`), both before the `try` at `:284`; errors propagate unwrapped.
- Encryption service is built after the empty-list return; cloud constructors do network calls.
- Two masks: `"********"` (backend, UI) vs `"*" * 10` (mapping service).
- Pre-existing, preserve: restore copies the stored value without checking sensitivity (`management.py:446-449`); an unmatched mask is stored literally.
- Duplicate bodies `management.py:386-404`/`:407-425`; decrypt stays at `assistant_service.py:933`; two `AssistantService` classes.
- Speculative: step 2-6 AST move proofs do not apply; typing guide conflicts with verbatim bodies.
- Unverified (no grep): repo-wide AC 5, other importers, guide citations.

---

## 7. Summary for Complexity Assessment

Three router modules lose 10 functions (about 290 lines) to new `service/assistant/` code; two router test files and new service tests change. Callers are `create_assistant`, `update_assistant`, both detail routes and `validate_assistant_for_marketplace`; no service, handler or sibling repo calls them, and the bodies reference no HTTP types.

First step that is not move-only: wiring and patch targets change, and the byte-identical AST proof of steps 2-6 cannot be reused. Security-relevant code moves (`/sanity`, `## Test harness`). Detection has unit tests; encryption, restore and masking have none.

---

## 8. External References

Resolved and read: `docs/superpowers/tasks/2026-10-08-epmcdme-15651-split-router-modules/ticket.md` (`:38` helpers move into `service/assistant/`; `:54` step 7; `:67` AC 5; `:69` logic moves in separate MRs; `:70` `/sanity`), `spec.md:42-51` (criteria 1-8), `local/router-split/HANDOFF.md` (`:60` step 7 not move-only, one run per package; `:65-76` patch map). Step 2 `spec.md:46` holds the AC 5 wording.
