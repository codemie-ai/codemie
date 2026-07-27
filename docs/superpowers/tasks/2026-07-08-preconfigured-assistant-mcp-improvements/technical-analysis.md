# Technical Research

**Task**: preconfigured_assistant mcp integration plugin validation creation update
**Generated**: 2026-07-08T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Improvements for creation and update flows of preconfigured_assistant with MCP server integrations. The current workflow for creating and updating preconfigured_assistant entities integrated with MCP servers presents potential limitations and lacks robust validation/configuration management. As the MCP ecosystem expands (multiple MCPs per project, distinct plugin labels, varying DATABASE_URIs), we require improvements that ensure: Consistent, scalable, and reliable integration between assistants and MCP servers. Validation of unique MCP plugin labels and assignment logic during creation/update. Visible and enforceable assignment of correct plugin endpoints in the assistant configuration UI. Error handling that proactively detects and surfaces misconfigurations (e.g., duplicate plugin IDs, missing endpoints). Automated testing/validation for >2 MCP configurations to prevent regression and ensure all assistant creations/updates properly register and link their MCP servers. Acceptance criteria: Users can seamlessly create/update assistants linked to multiple MCP servers with distinct labels. System enforces uniqueness and validity of all MCP plugin labels and endpoints during creation/update. No duplicate plugin IDs or omissions occur; configuration is accurately reflected in assistant details. Errors are correctly surfaced to users with clear, specific messages (not generic failure). Automated tests are in place for multi-MCP scenarios (>2) verifying expected flows. No regressions in assistant-MCP integration logic for existing 1-MCP or 2-MCP use cases.

---

## 2. Codebase Findings

### Existing Implementations

**Deployment Script (primary entry point for preconfigured assistant lifecycle)**
- `src/external/deployment_scripts/preconfigured_assistants.py` — Core file. Implements `create_preconfigured_assistant()`, `update_assistant_content()`, `manage_preconfigured_assistants()`, `delete_disabled_assistant()`, `get_all_contexts()`. `mcp_servers` is passed verbatim from YAML template into the DB record (line 216 for create, line 149 for update) with no validation whatsoever. `MCPAccessControlService` is never imported or called here.
- `src/external/deployment_scripts/preconfigured_workflows.py` — Imports `get_preconfigured_assistant_id_by_slug` from `preconfigured_assistants`. Creates integration credentials (including `amna-codemie-aws-integration`) referenced by MCP server `integration_alias` fields via `SettingsService.create_project_credentials_if_missing()`. Workflow init runs after assistant init.

**App Startup Orchestration**
- `src/codemie/rest_api/main.py` — Calls `manage_preconfigured_assistants()` inside `_initialize_preconfigured_content()` at startup (line 336).

**Data Models**
- `src/codemie/rest_api/models/assistant.py` — Defines `MCPServerDetails` (Pydantic model, line 152): fields `name`, `description`, `enabled`, `mcp_config_id`, `config` (inline `MCPServerConfig`), `mcp_connect_url`, `tools`, `integration_alias`, `settings`, `resolve_dynamic_values_in_arguments`, `tools_tokens_size_limit`, `mcp_connect_auth_token`, `command`, `arguments`. Has `@field_validator("command")` enforcing `_ALLOWED_MCP_COMMANDS`/`_ALLOWED_MCP_PATHS`. `AssistantBase` stores `mcp_servers: list[MCPServerDetails]` as JSONB. `validate_fields()` contains `_check_slug_uniqueness()`, `_check_categories()`, `_validate_assistant_ids()`, `_validate_prompt_variables()` — but NO MCP server name uniqueness check.
- `src/codemie/rest_api/models/mcp_config.py` — `MCPConfig` SQLModel table `mcp_configs`. Fields: `id`, `name`, `config` (`MCPServerConfigData`), `is_public`, `is_system`, `is_active`, `usage_count`. The catalog of shared/system MCP configurations.

**Access Control and Validation**
- `src/codemie/service/mcp/access_control.py` — `MCPAccessControlService`. `validate_on_save()`: in restricted mode validates all servers have `mcp_config_id`; checks duplicate `mcp_config_id` values (line 109–111); validates catalog entries are active and public. `sanitize_for_save()` wraps `validate_on_save()` and returns the list. `filter_for_runtime()` silently drops disqualified servers in restricted mode. `resolve_catalog_config()` fetches connection config from catalog at runtime. **This service is called in the REST router but entirely bypassed in the deployment script path.**
- `src/codemie/service/mcp/models.py` — `MCPServerConfig` (runtime Pydantic model with XOR validator: `command` xor `url`). `MCPExecutionContext`.

**Service Layer**
- `src/codemie/service/mcp/toolkit_service.py` — `MCPToolkitService` with TTL cache. Uses `"MCP:{server.name}"` as the integration-mapping key for per-user credential resolution (lines 70–71). Duplicate `name` values within one assistant's `mcp_servers` list cause ambiguous credential lookup at runtime.
- `src/codemie/service/mcp_config_service.py` — `MCPConfigService` CRUD for `MCPConfig` catalog. `create()` checks duplicate name per `user_id`. `adjust_usage()` does bulk `SELECT FOR UPDATE` on `usage_count`. `delete()` blocks deletion if `usage_count > 0`.
- `src/codemie/service/assistant/assistant_service.py` — Loads YAML templates from `config/templates/assistant/` at startup, keyed by slug. Returns cached `Assistant` instances via `get_assistant_template_by_slug()`.
- `src/codemie/service/tools/toolkit_service.py` — `ToolkitService._merge_skill_mcp_servers()` deduplicates by `server.name` when merging skill-contributed MCP servers at runtime.
- `src/codemie/service/skills/skill_contributions.py` — `SkillContributionsResolver` also deduplicates by `server.name` when collecting MCP servers from skills.

**REST API Router**
- `src/codemie/rest_api/routers/assistant.py` — `create_assistant()` (POST `/v1/assistants`, line 671) and `update_assistant()` (PUT `/v1/assistants/{id}`, line 782) both call `MCPAccessControlService.sanitize_for_save(request.mcp_servers)`. Also call `_track_mcp_usage_on_create/delete/changes()` to update `usage_count` in the catalog. These guardrails are **absent** from the deployment script path.

**Customer Configuration**
- `src/codemie/configs/customer_config.py` — `PreconfiguredAssistant` has `id`, `settings: AssistantSetting` (`enabled`, `index_name`), and `project`. No MCP server configuration is injectable per preconfigured assistant from `customer-config.yaml`.

**YAML Templates**
- `config/templates/assistant/aws_waf_template.yaml` — The **only** template in the repository that currently declares `mcp_servers`. Contains two inline-config entries:
  - `aws-knowledge-mcp-server` (`command: uvx`, args point to AWS knowledge endpoint)
  - `awslabs.aws-api-mcp-server` (`command: uvx`, carries `integration_alias: amna-codemie-aws-integration`)
  Neither uses `mcp_config_id`. This is the only real-world multi-MCP preconfigured assistant template.
- 25 other templates under `config/templates/assistant/` and `config/templates/assistant/admin/` have no `mcp_servers`.

**Database Migrations**
- `src/external/alembic/versions/b8e7f4d19c3a_populate_system_mcp_configs.py` — Populates ~18 system MCP configurations into `mcp_configs`.
- `src/external/alembic/versions/d1e2f3a4b5c6_...` — Adds `mcp_servers` JSONB to `skills` table.

### Architecture and Layers Affected

| Layer | Components |
|---|---|
| **Deployment Script** | `src/external/deployment_scripts/preconfigured_assistants.py` — the primary layer to modify |
| **Configuration Load** | `src/codemie/service/assistant/assistant_service.py` — loads YAML templates at startup |
| **Model / Validation** | `src/codemie/rest_api/models/assistant.py` (`AssistantBase.validate_fields()`, `MCPServerDetails`) — where name-uniqueness check belongs |
| **Access Control Service** | `src/codemie/service/mcp/access_control.py` (`MCPAccessControlService`) — existing save-time gate, must be wired into deployment script |
| **Runtime Resolution** | `src/codemie/service/mcp/toolkit_service.py` (`MCPToolkitService`) — where duplicate name collision surfaces at runtime |
| **REST API Router** | `src/codemie/rest_api/routers/assistant.py` — reference implementation for the correct validation + usage-tracking call sequence |
| **Catalog Service** | `src/codemie/service/mcp_config_service.py` (`MCPConfigService.adjust_usage()`) — usage tracking not called from deployment script |
| **Configuration** | `config/templates/assistant/aws_waf_template.yaml` — the live multi-MCP template; subject to any new validation at template-load time |

### Integration Points

**Internal:**
- `preconfigured_assistants.py` → `assistant_service.get_assistant_template_by_slug()` (template source)
- `preconfigured_assistants.py` → `Assistant.get_by_fields()` / `Assistant.save()` (direct DB access, bypasses router logic)
- `preconfigured_assistants.py` → `customer_config.get_all_configured_assistant_slugs()` / `is_assistant_enabled()` (config gate)
- `preconfigured_assistants.py` does NOT call → `MCPAccessControlService.sanitize_for_save()` (absent, this is the primary gap)
- `preconfigured_assistants.py` does NOT call → `MCPConfigService.adjust_usage()` (absent, usage counter gap)
- `preconfigured_workflows.py` → reads `preconfigured_assistant_ids` dict from `preconfigured_assistants.py` (cross-script module-level state)
- `MCPToolkitService` uses `"MCP:{server.name}"` as per-user integration credential lookup key — making `name` a functional unique identifier per assistant

**External:**
- No external HTTP calls in the deployment script path itself.
- Runtime: `MCPToolkitService` calls MCP-Connect sidecar (HTTP via `httpx`) for tool listing and invocation.
- Runtime: `integration_alias` on `MCPServerDetails` is resolved to a `Settings` record in PostgreSQL at invocation time.

### Patterns and Conventions

**Duplicate-key validation pattern (reference: `AssistantBase._validate_prompt_variables`):**
```python
keys = [var.key for var in self.prompt_variables]
duplicate_keys = {key for key in keys if keys.count(key) > 1}
if duplicate_keys:
    return f'Duplicate prompt variable keys detected: {", ".join(duplicate_keys)}'
```
This exact pattern applies to `[s.name for s in self.mcp_servers]`. A new `_validate_mcp_server_names()` method fits directly into the existing `validate_fields()` aggregation point on `AssistantBase`.

**`validate_fields()` extension point:**
`AssistantBase.validate_fields()` returns an error string and is called by the router before saving. Adding an MCP server name uniqueness check here covers both the router path and any future caller of `validate_fields()`.

**Access control call pattern (reference: `assistant.py` router lines 695, 808):**
```python
request.mcp_servers = MCPAccessControlService.sanitize_for_save(request.mcp_servers)
```
The same call inserted in `create_preconfigured_assistant()` (after template fetch, before `Assistant()` constructor) and in `update_assistant_content()` (before `fields_to_check` is built) would close the validation gap.

**`is_react` deprecation:** `AssistantBase.is_react` is explicitly deprecated (`# DO NOT USE IT`). The deployment script still passes it (line 205). New code should not introduce dependencies on this field.

---

## 3. Documentation Findings

### Guides and Architecture Docs

**`C:\Users\NargizMamedova\Projects\codemie-fork\.ai-run\guides\integration\mcp-integration.md`**
Directly relevant. Core directive:
> Keep MCP configuration and authentication behavior behind existing service and router modules. Avoid adding MCP auth logic to unrelated routers. Use MCP config/auth routers and services. Avoid treating MCP tools as static app code — load/configure through existing MCP service boundaries.

This mandates that the fix for the deployment script must route validation through `MCPAccessControlService` (the existing MCP service boundary) rather than duplicating validation logic inline in the deployment script.

**`C:\Users\NargizMamedova\Projects\codemie-fork\.ai-run\guides\architecture\layered-architecture.md`**
Prescribes: routers → services → repositories. Forbids DB calls from routers. The deployment script calling `Assistant.save()` directly is an accepted exception for deployment-time initialization, but validation must still flow through the service layer.

**`C:\Users\NargizMamedova\Projects\codemie-fork\.ai-run\guides\architecture\service-layer-patterns.md`**
Names `assistant_service.py` as a feature-scoped service. Does not name `MCPAccessControlService` explicitly, but the pattern applies.

**`C:\Users\NargizMamedova\Projects\codemie-fork\.ai-run\guides\testing\testing-patterns.md`** and **`testing-service-patterns.md`**
Relevant for writing new tests: pytest-based, class-grouped (`class TestX:`), `unittest.mock` with `MagicMock(spec=...)`, `@patch` stacking.

### Architectural Decisions

**Decision 1 — Save-time validation via `MCPAccessControlService`.**
`sanitize_for_save()` is called on every user-driven create/update in the REST router. The deployment script is the single path that bypasses it. Closing this gap is architecturally consistent with the mcp-integration.md directive.

**Decision 2 — `usage_count` is decoupled from save logic.**
`_track_mcp_usage_on_create/delete/changes()` are router-only. This counter governs whether `MCPConfigService.delete()` is blocked. Deployment-script-created assistants using catalog refs (`mcp_config_id`) do not increment `usage_count`, making the counter inaccurate for system configs referenced by preconfigured assistants.

**Decision 3 — `mcp_servers` is versioned with assistant configuration.**
`AssistantConfiguration` stores `mcp_servers` in JSONB snapshots. The deployment script calls `assistant.save()` directly without calling `AssistantVersionService`; preconfigured assistants have no version history currently. This is existing technical debt, not new for this task.

**Decision 4 — `name` is a functional key at runtime.**
`MCPToolkitService` uses `"MCP:{server.name}"` for per-user integration credential mapping. Duplicate names → ambiguous credential resolution at tool invocation time. This makes name uniqueness enforcement a correctness requirement, not just a UX nicety.

**Decision 5 — Inline config wins over catalog at runtime.**
`MCPAccessControlService.resolve_catalog_config()`: "inline wins wholesale over the catalog." YAML templates currently use only inline config (no `mcp_config_id`). The `aws_waf_template.yaml` template is entirely inline.

### Derived Conventions

- Validation methods on `AssistantBase` return `Optional[str]` (error string or `None`) and are aggregated in `validate_fields()`. New MCP-specific checks follow this same signature.
- Error messages use the pattern: `"Duplicate <entity> <field> detected: {values}"` — matching `_validate_prompt_variables`.
- Template YAML files use snake_case field names matching `MCPServerDetails` Pydantic field names.
- `integration_alias` on a template MCP server entry requires a corresponding `Settings` record created by `preconfigured_workflows.py`. The order dependency (workflows after assistants at startup) is intentional and must be preserved.

---

## 4. Testing Landscape

### Existing Coverage

**`tests/external/deployment_scripts/test_preconfigured_assistants.py`**
- Covers: create (new assistant), create (existing — delegates to update), `update_assistant_content` for `conversation_starters`, `icon_url`, and `context` changes, no-change scenario (returns False), `manage_preconfigured_assistants` with enabled/disabled assistants, `delete_disabled_assistant` (found and not found), `get_all_contexts` merge/deduplication logic.
- `mock_assistant.mcp_servers = []` and `mock_assistant_template.mcp_servers = []` in both fixtures (lines 51, 69) — **every test uses an empty MCP server list**.
- `mcp_servers` is in `fields_to_check` (source line 149) but the branch where it differs is never exercised by any test.

**`tests/unit/service/mcp/test_access_control.py`**
- Thorough coverage of `MCPAccessControlService`: open mode, restricted mode, duplicate `mcp_config_id`, inactive/non-public catalog entries, `filter_for_runtime`, `resolve_catalog_config`. Uses `_open_mode()` and `_restricted_mode()` context manager helpers. Tests with mixed server lists (3 servers).

**`tests/codemie/rest_api/models/test_assistant_model.py`** — `TestMCPServerDetails`
- Field defaults, serialization, command validation for `MCPServerDetails`.

**`tests/codemie/workflows/test_workflow_mcp_servers.py`**
- `test_virtual_assistant_service_create_with_multiple_mcp_servers` — 3 MCP servers on a `WorkflowAssistant`. Only multi-MCP test in the codebase, but for workflows, not preconfigured assistants.

**`tests/codemie/service/assistant/test_assistant_version_service.py`**
- `create_initial_version` with `mcp_servers=[MCPServerDetails(...)]`. Tests the version service MCP field handling but not the deployment script path.

### Testing Framework and Patterns

- **Framework**: pytest with `pythonpath = src`, `testpaths = tests`, `--import-mode=importlib`.
- **Mocking**: `unittest.mock` (`patch`, `MagicMock`, `MagicMock(spec=...)`, `call`, `ANY`). `@patch` decorator stacking on test functions.
- **Fixtures**: function-scoped fixtures; `mock_database_engine` autouse session-scoped fixture in `tests/conftest.py` patches `PostgresClient.get_engine`.
- **Pattern**: class-based grouping (`class TestX:`) used in service tests; bare functions used in deployment script tests.
- **MCP access control tests** use `_open_mode()` / `_restricted_mode()` context managers that `patch("codemie.configs.customer_config.customer_config.is_component_enabled", ...)`.

### Coverage Gaps

1. **`update_assistant_content` with `mcp_servers` that differs between template and existing assistant** — the `mcp_servers` field comparison and update branch has zero test coverage.
2. **`create_preconfigured_assistant` propagating non-empty `mcp_servers` from template** — `mcp_servers=assistant_template.mcp_servers` argument in the `Assistant()` constructor (line 216) is not asserted in any test.
3. **Multi-MCP preconfigured assistant creation (>2 servers)** — required by acceptance criteria; no such test exists in `test_preconfigured_assistants.py`.
4. **Single-MCP and two-MCP preconfigured assistant creation/update** — even 1-MCP baseline regression tests are absent.
5. **`MCPAccessControlService.sanitize_for_save()` called from the deployment script** — if this call is added, tests must verify it is invoked and that `ValidationException` surfaces correctly on violations.
6. **Deployment-script behavior when `mcpCustomServersDisabled` is enabled** — currently the deployment script bypasses restricted-mode validation entirely; no test documents or asserts this.
7. **`MCPConfigService.adjust_usage()` not called from deployment script** — no test documents the absence; if the gap is filled, new tests needed.

---

## 5. Configuration and Environment

### Environment Variables

| Variable | Default | Relevance |
|---|---|---|
| `MCP_CONNECT_ENABLED` | `True` | Master switch for MCP tool loading; if False, MCP tools skipped entirely |
| `MCP_CONNECT_URL` | `http://localhost:3000` | MCP-Connect sidecar endpoint |
| `MCP_TOOLKIT_SERVICE_CACHE_TTL` / `_CACHE_SIZE` | set in config | TTL cache for MCP toolkit instances |
| `CUSTOMER_CONFIG_DIR` | `config/customer` | Path for `customer-config.yaml` (injected via Kubernetes ConfigMap per deployment) |
| `ASSISTANT_TEMPLATES_DIR` | `config/templates/assistant` | Path for YAML template files |
| `NATS_PLUGIN_KEY_CHECK_ENABLED` | `False` | Plugin/NATS enterprise subsystem (parallel to MCP, not directly affected) |
| `PG_URL` | — | App database connection string (masked in safe logs) |

Note: "DATABASE_URI" as used in the task context is not a top-level env var. It maps to: `PG_URL` (app Postgres), or `DATABASE_URL` passed as an env entry inside `MCPServerConfigData.env` to the `mcp-server-postgres` process at MCP tool-call time. These are distinct.

### Configuration Files

| File | Role |
|---|---|
| `config/customer/customer-config.yaml` | Per-deployment: which preconfigured assistants are enabled, `mcpCustomServersDisabled` feature flag, `mcpConnect` component flag |
| `config/customer/managed-mcp-servers.example.yaml` | Schema example for per-deployment `managed-mcp-servers.yaml` (not committed; injected via ConfigMap) |
| `config/mcp/mcp-commands-config.yaml` | Allowlist of permitted MCP server binary names and paths (loaded fail-closed at startup via `MCPCommandsConfig`) |
| `config/templates/assistant/aws_waf_template.yaml` | The only preconfigured assistant template with `mcp_servers` defined (2 inline servers, 1 with `integration_alias`) |

### Feature Flags and Deployment Concerns

**`mcpCustomServersDisabled` (in `customer-config.yaml` `components`):**
When enabled, `MCPAccessControlService.validate_on_save()` rejects any MCP server without `mcp_config_id`. The deployment script currently bypasses this. A deployment with this flag enabled and a template containing inline `mcp_servers` (like `aws_waf_template.yaml`) will save the assistant without restriction at startup, then silently drop the servers at runtime via `filter_for_runtime()`. If `sanitize_for_save()` is added to the deployment script, this behavior changes to fail-fast at startup — which is the correct behavior per the mcp-integration.md directive.

**`integration_alias` order dependency:**
`preconfigured_workflows.py` creates the `amna-codemie-aws-integration` Settings record after assistants are initialized. MCP server runtime resolution of `integration_alias` occurs at tool invocation time, so the order dependency does not cause a race condition at startup. However there is no validation that a template-declared `integration_alias` has a corresponding Settings record; if `preconfigured_workflows.py` is skipped or fails, the AWS WAF assistant's second MCP server silently loses credentials.

**Deployment context for multi-MCP templates:**
The `config/` directory is COPY'd into the Docker image and overridden in Kubernetes via ConfigMap mounts. Template YAML files (`ASSISTANT_TEMPLATES_DIR`) are baked into the image; `customer-config.yaml` is injected per deployment. Adding new templates with `mcp_servers` requires no config changes — only a new YAML file in `config/templates/assistant/`.

---

## 6. Risk Indicators

- **Primary gap — deployment script bypasses all MCP save-time validation**: `create_preconfigured_assistant()` and `update_assistant_content()` in `src/external/deployment_scripts/preconfigured_assistants.py` do not call `MCPAccessControlService.sanitize_for_save()`. This is the root cause of the task. Restricted-mode enforcement, catalog-entry validity checks, and duplicate `mcp_config_id` detection are all absent from the deployment path.

- **No MCP server `name` uniqueness check anywhere at save time**: `AssistantBase.validate_fields()` checks slug, categories, sub-assistant IDs, and prompt variable keys, but not `MCPServerDetails.name` uniqueness within `mcp_servers`. The runtime consequence is ambiguous credential resolution in `MCPToolkitService` (uses `"MCP:{server.name}"` as mapping key). This affects both the REST API path and the deployment script path.

- **Zero test coverage for preconfigured assistants with non-empty `mcp_servers`**: All fixtures in `tests/external/deployment_scripts/test_preconfigured_assistants.py` set `mcp_servers = []`. The acceptance criteria requires tests for >2 MCP server scenarios. The `aws_waf_template.yaml` two-server template is the only real-world case and it is entirely untested through the deployment script path.

- **`MCPConfigService.adjust_usage()` not called from deployment script**: Catalog-referenced MCP servers in templates would not increment `usage_count` on creation, and would not decrement it on deletion. This can cause incorrect blocking of `MCPConfig` deletion for system configs.

- **`integration_alias` existence not validated at creation time**: `create_preconfigured_assistant()` does not verify that a declared `integration_alias` has a corresponding Settings record. Silent credential resolution failure at first tool invocation. The `aws_waf_template.yaml` `amna-codemie-aws-integration` alias relies on `preconfigured_workflows.py` running first.

- **`AssistantVersionService` not called from deployment script**: Preconfigured assistants have no version snapshots. Any `mcp_servers` change via deployment update is not captured in `assistant_configurations`. This is existing technical debt; the task should not worsen it but may want to note the gap.

- **`validate_fields()` not called from deployment script**: The router calls `validate_fields()` before saving user-driven assistants. The deployment script calls `Assistant.save()` directly, bypassing all model-level structural validation (slug uniqueness, categories, prompt variable keys, and the new MCP name uniqueness check once added).

- **The only multi-MCP template (`aws_waf_template.yaml`) uses only inline configs**: No preconfigured assistant template currently uses `mcp_config_id` catalog references. Restricted-mode (`mcpCustomServersDisabled`) deployments would silently drop both servers from `aws_waf_template.yaml` at runtime via `filter_for_runtime()`. If `sanitize_for_save()` is added to the deployment script, a restricted-mode deployment would fail fast at startup instead — a behavior change that must be handled.

- **`settings` field on `MCPServerDetails` carries a pending rename**: Documented in-source as `# Must be renamed to environment_vars` (line 205). Any new code that touches this field should use it by its current name but not introduce new references that lock in the old name. Rename is out of scope for this task but should be noted.

- **No guide documents the deployment script's MCP validation gap**: The `mcp-integration.md` guide covers router/service patterns; it does not address the deployment script. New guidance or an inline code comment should document the required call sequence after the fix.

---

## 7. Summary for Complexity Assessment

This task involves a clearly localized set of changes with well-understood root causes. The deployment script `src/external/deployment_scripts/preconfigured_assistants.py` bypasses the existing `MCPAccessControlService` validation gate that the REST router correctly invokes on every user-driven create/update. The fix requires: (1) calling `MCPAccessControlService.sanitize_for_save()` from both `create_preconfigured_assistant()` and `update_assistant_content()`; (2) adding a `_validate_mcp_server_names()` method to `AssistantBase.validate_fields()` to enforce `name` uniqueness within `mcp_servers` (mirroring the existing `_validate_prompt_variables` pattern); and optionally (3) wiring `MCPConfigService.adjust_usage()` into the deployment script for catalog-referenced servers. The primary files to change are: `preconfigured_assistants.py` (deployment script), `assistant.py` (model validation), and their corresponding test files. The REST router and `MCPAccessControlService` itself do not need changes — the validation logic already exists and is correct. Estimated change surface: 3–5 files (deployment script, model, test for deployment script, optionally test for model validation).

The task follows well-established patterns already present in the codebase — no novel architecture is required. The `validate_fields()` extension point, the `_validate_prompt_variables` pattern, and the `MCPAccessControlService.sanitize_for_save()` call sequence are all documented by existing code. The most technically novel aspect is determining whether `sanitize_for_save()` should raise (fail the deployment) or log a warning when called from the deployment script under restricted mode with inline-config templates — this is a behavioral decision that affects the `aws_waf_template.yaml` case in restricted-mode deployments and should be explicitly decided before implementation.

Test coverage for this domain is the weakest part of the codebase in this area. All existing `test_preconfigured_assistants.py` tests use `mcp_servers = []`. The acceptance criteria requires tests for >2 MCP server scenarios, plus regression tests for 1-MCP and 2-MCP cases — all are entirely new. The test patterns in `test_access_control.py` (which already exercises 3-server mixed lists) and `test_workflow_mcp_servers.py` (which has a 3-server virtual assistant test) provide good reference fixtures. Risk is low-to-medium: the change is additive (adding validation calls and test cases), the integration points are well-understood, and the only non-trivial decision is the restricted-mode behavior in the deployment script.
