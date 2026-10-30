# Technical Research

**Task**: mcp server-config assistant-tool-config user-context
**Generated**: 2026-09-14T00:00:00Z
**Research path**: codegraph

---

## 1. Original Context

New configurable opt-out for MCP user_context propagation, requested by the task creator/stakeholder as a follow-up to EPMCDME-13260. EPMCDME-13260 makes the backend forward a non-sensitive user profile (user_context: id, username, name, email, roles, is_admin, is_maintainer, is_auditor, user_type, project_names, admin_project_names, knowledge_bases, picture) to MCP tools via MCP-Connect's _meta["codemie.ai/user-context"], mirroring what the DSP (Data Source Provider) path already does unconditionally today (provider_tool_factory.py, UserContext.from_user()). MCP-Connect (branch EPMCDME-14847) already accepts it, masks it in its own logs, forwards it opaquely to the downstream MCP tool server on tools/call, and the tool server uses it if it wants to.

New ask: give admins/assistant builders a way to turn this OFF, with two levels:
1. Server-level default: a field on the backend's MCP server registration/config (MCPServerConfig or wherever MCP server integrations are stored/edited in CodeMie), defaulting to True (send context, matching current DSP behavior).
2. Per-assistant override: when configuring an assistant's MCP tool, a JSON key like "allow_user_context": false in that assistant's MCP tool config disables sending user_context for that specific assistant, overriding the server-level default.
3. Enforcement is backend-side only, before building the MCP request payload (in the codepath that currently calls MCPExecutionContext.to_request_fields() / MCPConnectClient._build_request_payload) — when disabled, user_context is omitted from the request entirely.

Focus the research on: where MCP server integrations are registered/stored/edited in CodeMie (the config model, its persistence, its API/UI edit path) so we know where a server-level default field would live; where an assistant's per-MCP-tool JSON configuration is stored and how it's structured today (to see whether a new key like "allow_user_context" fits naturally, and any precedent for similar per-assistant tool overrides e.g. mcp_headers/env); and the exact current call path from "an assistant is about to invoke/list an MCP tool" through to MCPExecutionContext.to_request_fields()/MCPConnectClient._build_request_payload, so the digest can point at exactly where an enforcement check would need to read the config and skip populating user_context.

---

## 2. Codebase Findings

### Existing Implementations

**Server-level MCP config catalog (candidate home for a server-level default field):**
- `src/codemie/rest_api/models/mcp_config.py:97` — `MCPServerConfigData` (Pydantic `BaseModel`): the config block persisted for a shareable MCP server registration. Fields today: `command`, `url`, `args`, `headers: dict[str, str]`, `env: dict[str, Any]`, `type`, `auth_token`, `single_usage: bool`, `tools: list[str] | None`, `audience`, `auth_config: dict[str, Any] | None`, `allow_issuer_prefix_match: bool = False`.
- `src/codemie/rest_api/models/mcp_config.py:159` — `MCPConfig` (`SQLModel`, `table=True`, extends `BaseModelWithSQLSupport`): the DB row for `mcp_configs`. Stores `config: MCPServerConfigData | None` as a `PydanticType` JSONB column, plus `name`, `description`, `categories`, `required_env_vars`, `user_id`, `is_public`, `is_system`, `created_by`, `usage_count`, `is_active`.
- `src/codemie/rest_api/models/mcp_config.py:276` — `MCPConfigUpdateRequest`, `:299` `MCPConfigResponse` — API request/response models that carry `config: MCPServerConfigData | None` through create/update/read.
- `src/codemie/service/mcp_config_service.py` — `MCPConfigService` (`create`, `get_by_id`, `update`, `increment_usage`/`decrement_usage`/`adjust_usage`) — the service layer that persists/reads `MCPConfig` rows; this is the edit path referenced by the mcp-integration guide as the place to keep MCP config logic.

**Per-assistant MCP tool configuration (candidate home for the per-assistant JSON override):**
- `src/codemie/rest_api/models/assistant.py:174` — `MCPServerDetails` (Pydantic `BaseModel`): the per-assistant MCP server entry. Fields observed: `name`, `description`, a `config` field normalized from an empty dict to `None` (`_normalize_empty_config`), a `command` field with its own `validate_command` validator (mirrors `MCPServerConfigData.validate_command`), `settings: Optional[SettingsBase]` (marked "Must be renamed to environment_vars"), `integration_alias: Optional[str]`, and a `tools` list with the same "only these tools will be used" semantics as `MCPServerConfigData.tools`. Ships a `Config.json_schema_extra` example showing `config: {command, args, env}`.
- `src/codemie/rest_api/models/assistant.py:1251` — `AssistantConfiguration` (`SQLModel`, `table=True`): versioned assistant config snapshot; `mcp_servers: list[MCPServerDetails]` is stored as a `PydanticListType(MCPServerDetails)` JSONB column alongside `toolkits: list[ToolKitDetails]`.
- No existing per-tool boolean toggle (e.g. `allow_user_context`) was found on `MCPServerDetails` or `MCPServerConfigData`; the closest existing precedent for per-assistant tool-level override fields is `headers`/`env` on `MCPServerConfigData` (dict fields already threaded through to the MCP-Connect request body via `_merge_mcp_headers`/`_bridge_env` in `client.py`).
- Two distinct `ToolConfig` classes exist and are unrelated to MCP servers: `src/codemie/core/models.py:514` (`name`, `tool_creds`, `integration_id` — generic tool invocation config) and `src/codemie/rest_api/models/usage/assistant_user_mapping.py:37` (`name`, `integration_id` — persisted per-user/per-assistant tool-to-integration mapping). Neither currently represents MCP-server-specific JSON config; `MCPServerDetails` is the closer fit for a per-assistant MCP override key.

**Runtime call path — assistant invoking/listing an MCP tool through to the wire payload:**
1. `src/codemie/service/mcp/toolkit_service.py:185` — `MCPToolkitService.get_mcp_server_tools(mcp_servers, user_id, ..., assistant_id, ...)`: filters servers via `MCPAccessControlService.filter_for_runtime`, resolves `current_user = get_current_user()`, and builds one `MCPExecutionContext` **shared across all servers in the call**, setting `user_context=UserContext.from_user(current_user) if current_user else None` (line 243). This context is then passed into `_collect_server_results`.
2. `_collect_server_results` (same file) processes each `MCPServerDetails`/server config and produces `MCPTool` instances; tools that need context are wrapped as `ContextAwareMCPTool(original_tool, context)` (`src/codemie/service/mcp/toolkit.py:308`), binding the same `MCPExecutionContext` instance to every tool from every server in that call.
3. `ContextAwareMCPTool.execute()` (`toolkit.py:348`) → `MCPTool.execute_with_context()` (`toolkit.py:190`) → `MCPTool._aexecute_with_context()` (`toolkit.py:139`) → `self.mcp_client.invoke_tool(server_config=self.mcp_server_config, tool_name=..., tool_args=..., execution_context=execution_context)`.
4. `MCPConnectClient.invoke_tool()` (`src/codemie/service/mcp/client.py:203`) and `MCPConnectClient.list_tools()` (same file, `tools/list` path) both call `self._build_request_payload(method=..., server_config=server_config, execution_context=execution_context, params=...)`.
5. `MCPConnectClient._build_request_payload()` (`client.py:100`) constructs `MCPToolInvocationRequest(..., **(execution_context.to_request_fields() if execution_context else {}))`; `server_config` here is `MCPServerConfig` (runtime model in `src/codemie/service/mcp/models.py`), a different class from the persisted `MCPServerConfigData`.
6. `MCPExecutionContext.to_request_fields()` (`src/codemie/service/mcp/models.py:167`) is the exact point that serializes `user_context` (minus `extra_attributes`) into the dict unpacked into the request; it currently has no conditional — `user_context` is always included when set on the context object. `post_body = request.model_dump(exclude_none=True)` then drops it only if it is `None`.

**Important gap in the traced path**: `get_mcp_server_tools` builds **one shared `MCPExecutionContext`** (with one `user_context`) for the whole call, covering potentially multiple `MCPServerDetails` entries in one `mcp_servers` list. `MCPExecutionContext.user_context` is therefore not naturally per-server today — an enforcement check keyed on a per-server or per-assistant-tool flag would need either (a) a per-server context (or a per-server override of the shared context before it reaches `to_request_fields()`), or (b) the flag folded into `MCPServerConfig`/`MCPServerConfigData` itself and read inside `_build_request_payload` where `server_config` is already per-server. This repo does not currently show how `MCPServerDetails`/`MCPServerConfigData` gets translated into the runtime `MCPServerConfig` passed to `_build_request_payload`; that mapping step was not located in the calls surfaced by codegraph and should be traced explicitly before deciding the enforcement point.

### Architecture and Layers Affected

- **Persistence / config layer**: `MCPConfig` + `MCPServerConfigData` (`src/codemie/rest_api/models/mcp_config.py`), backed by `mcp_configs` Postgres table via `BaseModelWithSQLSupport`/`PydanticType`.
- **Assistant configuration layer**: `MCPServerDetails` + `AssistantConfiguration.mcp_servers` (`src/codemie/rest_api/models/assistant.py`), backed by `assistant_configurations` table via `PydanticListType`.
- **Service layer**: `MCPConfigService` (CRUD for server-level config), `MCPToolkitService` (tool discovery/execution orchestration, builds `MCPExecutionContext`), `MCPServerTester`/`MCPToolsInfoService` (test/introspection paths that also call `get_mcp_server_tools`).
- **Runtime/client layer**: `src/codemie/service/mcp/toolkit.py` (`MCPTool`, `ContextAwareMCPTool`, `MCPToolkit`), `src/codemie/service/mcp/client.py` (`MCPConnectClient`), `src/codemie/service/mcp/models.py` (`MCPExecutionContext`, `MCPServerConfig`, `MCPToolInvocationRequest`).
- **Security/user layer**: `src/codemie/rest_api/security/user.py` (`User`, `UserContext`, `UserContext.from_user()`).
- **API/router layer**: `src/codemie/rest_api/routers/assistant.py` (assistant CRUD including `MCPServerDetails`), an `mcp_config` router (referenced by the mcp-integration guide, registered in `src/codemie/rest_api/main.py`) for server-level `MCPConfig` CRUD.

### Integration Points

- MCP-Connect (external service, branch EPMCDME-14847): receives the built payload over HTTP at `{base_url}/bridge`; `_build_request_payload` is the last backend hop before the wire.
- DSP (Data Source Provider) path: `src/codemie/service/provider/provider_tool_factory.py` also calls `UserContext.from_user()` (confirmed via codegraph caller list) and forwards `user_context` unconditionally — this is the sibling path originally out of scope for the MCP-only research pass; it is now fully covered in section 9 below following the 2026-09-14 scope expansion.
- `MCPAccessControlService.filter_for_runtime` (`src/codemie/service/mcp/access_control.py`, referenced but not opened) filters `mcp_servers` before context building — a plausible sibling location for a server/assistant-level policy check, though not confirmed by source in this pass.

### Patterns and Conventions

- Server-level MCP config fields follow a "raw dict + typed enterprise subclass" pattern for `auth_config` (`MCPServerConfigData.auth_config: dict[str, Any] | None`, with a comment that typed OAuth2/SAML models live in the enterprise package) — a precedent for adding an optional field with a safe backend-community default.
- `MCPServerConfigData.validate_command` and the field_validator pattern (`@field_validator`, allow-list against `_ALLOWED_MCP_COMMANDS`) is the standard for constrained fields on this model; a boolean opt-out field would not need this pattern.
- `MCPExecutionContext.to_request_fields()` already uses `model_dump(exclude={...})` to selectively drop fields (e.g. `auth_headers`, `conversation_id`, `oauth2_*`, `user_context.extra_attributes`) before serialization — the same mechanism (excluding `user_context` entirely, or setting it to `None` before calling `to_request_fields()`) is the established way this class hides fields from MCP-Connect.
- Both `MCPServerConfigData` (mcp_config.py) and `MCPServerDetails` (assistant.py) independently define an overlapping `command`/`validate_command` and `tools` field with duplicated docstrings — evidence that assistant-level and server-catalog-level config are maintained as two separate schemas rather than one shared base, relevant to where a new field would need to be added twice (or once, with resolution logic elsewhere).

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/integration/mcp-integration.md` — Found and read in full. States: "Keep MCP configuration and authentication behavior behind existing service and router modules" (avoid adding MCP auth logic to unrelated routers; load/configure through existing MCP service boundaries). Confirms MCP config router registration at `src/codemie/rest_api/main.py:695` and MCP auth router at `:713`. Also documents `MCP_SERVER_INIT_TIMEOUT` / `MCP_CONNECT_INIT_TIMEOUT` / `MCP_CLIENT_TIMEOUT` env vars (unrelated to user_context but the only MCP-specific env vars documented).
- No guide file specifically covers `user_context` propagation, `MCPExecutionContext`, or per-assistant MCP tool overrides — this area's conventions are derived from code (see below), not from `.ai-run/guides/`.
- No guide file specifically covers the DSP provider-config model (`Provider`/`ProviderBase`/`ProviderToolkit`) either — DSP conventions in section 9 are likewise derived from code.

### Architectural Decisions

- Inline code comments in `src/codemie/service/mcp/models.py:167-182` (docstring on `to_request_fields`) record the EPMCDME-13260/EPMCDME-14847 decision trail directly: "`user_context` IS serialised and forwarded to MCP-Connect on both `tools/list` and `tools/call` requests (EPMCDME-13260)... This depends on an MCP-Connect release that accepts the field (EPMCDME-14847)." This is the load-bearing comment the new opt-out will need to update once enforcement is added.
- `src/codemie/rest_api/security/user.py:152-172` docstring on `UserContext`: "Non-sensitive user profile forwarded to downstream tool invocations (DSP and MCP)... Never includes credentials or internal identifiers." This docstring already names both consumers (DSP and MCP), confirming `UserContext` is shared, not duplicated, across the two paths.

### Derived Conventions

- New optional boolean fields on Pydantic config models in this codebase default via `Field(default=..., description=...)`, matching `single_usage: bool = Field(default=False, ...)` and `allow_issuer_prefix_match: bool = Field(default=False, ...)` on `MCPServerConfigData`.
- Config resolution precedence (server-level default overridden by a more specific scope) has a precedent in `AssistantUserMappingRepository.promote_to_assistant_scope` (workflow-scoped row overrides/falls back to assistant-scoped row) — a scoping pattern, though for a different feature (tool credential mapping, not MCP config).

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/mcp/test_models.py`, `tests/codemie/service/mcp/test_models_1_2_auth_config.py` — cover `MCPExecutionContext.to_request_fields()` directly: `test_to_request_fields_includes_user_context_when_set`, `test_to_request_fields_excludes_extra_attributes_from_user_context`, `test_auth_headers_excluded_from_to_request_fields`, etc.
- `tests/codemie/service/mcp/test_execution_context_integration.py` — covers `ContextAwareMCPTool` and context propagation end-to-end: `test_get_mcp_server_tools_propagates_user_context`, `test_get_mcp_server_tools_user_context_none_when_no_user`, `test_context_propagates_through_tool_chain`.
- `tests/codemie/service/mcp/test_toolkit_mcp_tool.py`, `tests/codemie/service/mcp/test_toolkit_mcp_toolkit_init.py` — cover `MCPTool`/`MCPToolkit` construction and execution paths (mocked `MCPConnectClient`).
- `tests/codemie/service/mcp/test_client.py` — covers `MCPConnectClient.invoke_tool` (referenced by codegraph, not opened in full).
- `tests/codemie/service/test_mcp_config_service_auth_validation.py`, `..._auth_crud.py`, `..._auth_conflict.py`, `..._auth_secret_storage.py` — cover `MCPConfigService` create/update validation, entirely for `auth_config`; no coverage of a hypothetical `allow_user_context`-style field.
- `tests/codemie/rest_api/models/test_assistant_model.py` — referenced as covering `MCPServerDetails` (not opened in full).
- `tests/codemie/repository/assistants/test_assistant_user_mapping_repository.py` — covers the (unrelated) `ToolConfig`/`AssistantUserMappingSQL` mapping repository.
- `tests/codemie/configs/test_mcp_commands_config.py`, `tests/codemie/configs/test_managed_mcp_config.py` — cover unrelated MCP config loaders (allowed commands, managed/client-neutral MCP catalog).
- `tests/codemie/service/provider/test_provider_tool_factory.py` — covers `ProviderToolFactory.build`/`invoke`/`provider_config` on the DSP path (see section 9.5 for detail).
- `tests/codemie/service/provider/test_provider_service.py` — covers `ProviderService.create`/`update`/`get`/`delete`/`index` CRUD on `Provider` (see section 9.5).
- `tests/codemie/rest_api/security/test_user_context.py` — covers `UserContext` itself (`from_user`, `masked_for_logging`, round-trip), shared by both MCP and DSP paths.

### Testing Framework and Patterns

- `pytest` with `unittest.TestCase`-style classes in some MCP tests (`setUp`/`tearDown`, `MagicMock(spec=...)`) alongside plain `pytest` function-style tests elsewhere (`test_models_1_2_auth_config.py`). Mocks are built with `MagicMock`/`AsyncMock`, and `@patch("codemie.service.mcp_config_service.MCPConfig")` is the standard way to isolate `MCPConfigService` from the DB layer.

### Coverage Gaps

- No test exercises a server-level "send/don't send user_context" default on `MCPServerConfigData`/`MCPConfig` — the field does not exist yet.
- No test exercises a per-assistant `allow_user_context` override on `MCPServerDetails` — the field does not exist yet.
- No test exercises the interaction/precedence between a server-level default and a per-assistant override for any existing field pair on this model family (the closest analog, `headers`/`env` merge, is exercised via `_merge_mcp_headers`/`_bridge_env` but those are additive merges, not enable/disable toggles, so the precedence pattern for a boolean override is itself untested).
- No test currently asserts that `_build_request_payload`/`to_request_fields()` can vary its `user_context` output per-server within a single `get_mcp_server_tools` call — consistent with the finding above that today's `MCPExecutionContext` is built once per call, not once per server.
- No test exercises an `allow_user_context`-style toggle on the DSP path (`Provider`/`ProviderToolFactory.invoke`) either — the field does not exist yet there. Since DSP has only one config level (see 9.2), this gap is simpler than the MCP one: no precedence logic to test, only a single conditional in `invoke()`.

---

## 5. Configuration and Environment

### Environment Variables

- `MCP_SERVER_INIT_TIMEOUT`, `MCP_CONNECT_INIT_TIMEOUT`, `MCP_CLIENT_TIMEOUT` — documented in `.ai-run/guides/integration/mcp-integration.md`; govern MCP server startup/invocation timeouts, unrelated to user_context but the only MCP-specific env vars found in this pass.
- No environment variable currently governs `user_context` propagation on either the MCP or the DSP path; the existing EPMCDME-13260 behavior is unconditional code, not env-gated.

### Configuration Files

- `mcp-commands-config.yaml` (loaded by `MCPCommandsConfig`, `src/codemie/configs/mcp_commands_config.py`) — allow-lists MCP server launch commands/paths; unrelated to user_context.
- `managed-mcp-servers.yaml` (loaded by `load_managed_mcp_servers`, `src/codemie/configs/managed_mcp_config.py`) — client-neutral managed MCP server catalog (ConfigMap-supplied, not committed); has no per-entry user_context field today (`ManagedMcpServer` fields: `name`, `transport`, `url`, `auth`, `description`, `clients`, `oauth`).
- MCP server registrations that a user creates (the `MCPConfig`/`MCPServerConfigData` catalog) and assistant-embedded MCP servers (`MCPServerDetails` on `AssistantConfiguration`) are both DB-persisted JSONB, not file-based config.
- DSP provider registrations (the `Provider`/`ProviderBase` row, `provided_toolkits: List[ProviderToolkit]`) are likewise DB-persisted JSONB on the `providers` table, not file-based config — see section 9.1.

### Feature Flags and Deployment Concerns

- No feature flag or toggle currently exists for `user_context` propagation on either path. `MCP-Connect` (EPMCDME-14847) has already deployed acceptance of the field; the deploy-sequencing note referenced in this branch's own commit history (`31e331fae EPMCDME-13260: Add MR deploy-sequencing note`) suggests deploy-order between CodeMie and MCP-Connect is an established concern for this feature area worth carrying into the opt-out's rollout plan. The DSP path has no external counterpart service requiring similar deploy sequencing (the DSP-side provider server either respects `user_context` or ignores it; no bridge component is in the loop).

---

## 6. Risk Indicators

- Speculative: the server-level default field most naturally extends `MCPServerConfigData` (`src/codemie/rest_api/models/mcp_config.py:97`) with a new `allow_user_context: bool = False` field, mirroring the existing `single_usage`/`allow_issuer_prefix_match` boolean-field pattern — but this is a design choice for the spec/plan stage, not a confirmed requirement.
- Speculative: the per-assistant override most naturally extends `MCPServerDetails` (`src/codemie/rest_api/models/assistant.py:174`), either as a top-level field or nested inside its `config` block — again a design choice, not confirmed here.
- Speculative: on the DSP side, the new `allow_user_context: bool = False` field most naturally extends `ProviderBase` (`src/codemie/rest_api/models/provider.py:255`), read inside `ProviderToolFactory.invoke()` via `self.provider_config.allow_user_context` — a design choice for spec/plan, not a confirmed requirement.
- Confirmed gap: `MCPExecutionContext` is built **once per `get_mcp_server_tools` call**, not once per server (`toolkit_service.py:234-244`), while `user_context` enforcement is meant to be per-server/per-assistant-tool. The design will need to decide whether the check happens earlier (filtering/adjusting per-server before the shared context is used) or later (inside `_build_request_payload`, which does have per-server `server_config` in scope).
- Confirmed gap: the mapping from `MCPServerDetails`/`MCPServerConfigData` (config schemas) to `MCPServerConfig` (the runtime model actually passed into `_build_request_payload`, defined in `src/codemie/service/mcp/models.py`) was not located in this research pass — codegraph surfaced the endpoints but not the translation step in `toolkit_service._collect_server_results`. This should be traced explicitly before finalizing the enforcement point, since `_build_request_payload` only has access to whatever fields survive that translation.
- Naming collision risk: two unrelated classes are both named `ToolConfig` (`src/codemie/core/models.py:514` and `src/codemie/rest_api/models/usage/assistant_user_mapping.py:37`) — neither is the per-assistant MCP JSON config the task describes; care is needed not to confuse either with `MCPServerDetails`.
- No existing test coverage for any enable/disable-style boolean override with precedence between a server-level and assistant-level scope in the MCP model family; the closest analog (`headers`/`env` merge) is an additive merge, not a boolean override, so a new precedence-resolution code path would be genuinely novel and untested territory on the MCP side.
- The MCP-Connect side (branch EPMCDME-14847) is out of this repo and was not indexed by codegraph — its behavior is taken as given from the task description only, not independently verified in this pass.
- `src/codemie/service/mcp/access_control.py` (`MCPAccessControlService.filter_for_runtime`) is called in the `get_mcp_server_tools` path and could be a natural policy checkpoint, but its source was not opened in this research pass — a gap worth closing before design.
- Confirmed (DSP): unlike MCP, the DSP path has no per-assistant or per-datasource override concept at all today — `ProviderToolFactory` is constructed from a single `Provider` row with no assistant identity threaded through (section 9.2). This makes the DSP toggle strictly simpler than the MCP one (one level, one conditional in `invoke()`), but also means the two toggles will have structurally different resolution logic (DSP: flat field read; MCP: two-level precedence) — the spec should not assume a single shared helper covers both without adaptation.
- Confirmed (DSP): no existing boolean gate on `Provider`/`ProviderBase`/`ProviderToolkit`/`ProviderConfiguration` controls what is sent to a DSP provider at invocation time (section 9.4); the new field has no direct DSP-side precedent to copy and instead should mirror the MCP-side `MCPServerConfigData` boolean-field style for consistency across the two toggles.

---

## 7. Summary for Complexity Assessment

This task touches four architectural layers on the MCP side: the persistence/config layer (`MCPServerConfigData`/`MCPConfig` in `mcp_config.py` — candidate for the server-level default), the assistant-configuration layer (`MCPServerDetails`/`AssistantConfiguration` in `assistant.py` — candidate for the per-assistant override), the MCP runtime/client layer (`MCPToolkitService.get_mcp_server_tools` → `ContextAwareMCPTool` → `MCPConnectClient.invoke_tool`/`_build_request_payload` → `MCPExecutionContext.to_request_fields()` — the enforcement point), and the security/user layer (`UserContext`/`User`, unchanged). On the DSP side (added 2026-09-14), the same security/user layer is shared, and a single persistence/runtime layer is touched instead of two: `Provider`/`ProviderBase` (`src/codemie/rest_api/models/provider.py`) as the persisted config, and `ProviderToolFactory.invoke()` (`src/codemie/service/provider/provider_tool_factory.py:131`) as the single enforcement point, since `self.provider_config` is already in scope there with no additional plumbing.

The main technical novelty on the MCP side is not the new fields themselves (both candidate models already carry similarly-shaped boolean/dict fields, so schema extension is low-risk and well-precedented) but the **scope mismatch** between where `MCPExecutionContext` is currently built (once per `get_mcp_server_tools` call, covering potentially several servers) and where enforcement needs to vary (per server / per assistant-tool) — a confirmed gap requiring design attention. The DSP side has no such mismatch: it is confirmed single-level (no per-assistant or per-datasource override construct exists at all), so its enforcement is a single conditional read of `self.provider_config.allow_user_context` inside an already-well-understood method, with no precedence logic to design or test. This makes the DSP half of the scope expansion materially lower-complexity than the MCP half — the risk concentrates in the MCP two-level design and the still-untraced `MCPServerDetails`/`MCPServerConfigData` → `MCPServerConfig` mapping, not in DSP.

Test coverage on the MCP seam (`to_request_fields`, `ContextAwareMCPTool` context propagation, `MCPConfigService` validation) is solid; DSP coverage on the equivalent seam (`ProviderToolFactory.invoke`, `ProviderService` CRUD) is also present but, like MCP, has zero coverage for any user-context toggle specifically, since the field does not exist yet on either side. Neither path has a precedent for a boolean gate governing what is forwarded to the downstream provider/tool server, so both the MCP and DSP additions are novel code, though the DSP addition is the simpler of the two to design, implement, and test.

---

## 8. External References

None named by the task. `task_context` references ticket IDs (EPMCDME-13260, EPMCDME-14847, EPMCDME-14984) and named code symbols/files within this repository (all covered in Section 2 and Section 9 via codegraph), but does not name any file or URL outside this repository as a source of truth.

---

## 9. DSP Provider Config (addendum — scope expansion 2026-09-14)

Scope: EPMCDME-14984 was expanded to add an `allow_user_context: bool = False` opt-out to the DSP (Data Source Provider) path as well as MCP. This section covers the DSP side only (points 1-4 of the expansion request); the MCP findings above (sections 1-8) are untouched and remain the source of truth for the MCP half of the task.

### 9.1 Persisted config model (DSP equivalent of MCPServerConfigData)

- `Provider` (`src/codemie/rest_api/models/provider.py:262`) — `SQLModel`, `table=True`, `__tablename__ = "providers"`; extends `BaseModelWithSQLSupport` and `ProviderBase`. This is the DB-persisted row — the DSP equivalent of `MCPConfig`.
- `ProviderBase` (`provider.py:255`) — the Pydantic/SQLModel base carrying the persisted fields: `name`, `service_location_url`, `configuration: ProviderConfiguration` (currently just `auth_type: AuthType`, only `BEARER` today), `provided_toolkits: List[ProviderToolkit]` (stored as a `PydanticListType` JSONB column via `SQLField(sa_column=Column(PydanticListType(ProviderToolkit)))`). This is the DSP equivalent of `MCPServerConfigData`, and the natural home for a new `allow_user_context: bool = False` field so it persists on the `providers` table and round-trips through create/update requests.
- `ProviderToolkit` (`provider.py:124`) — one integration's toolkit definition within a provider: `toolkit_id`, `name`, `description`, `toolkit_config: ToolkitConfig`, `provided_tools: List[Tool]`, `toolkit_metadata: Optional[ProviderToolkitMetadata]`. `Tool` (nested class) carries `name`, `description`, `args_schema`, `tool_metadata: ProviderToolMetadata`, `tool_result_type`, `sync_invocation_supported`, `async_invocation_supported`.
- CRUD / API edit path: `ProviderService` (`src/codemie/service/provider/provider_service.py`) — `create`, `update`, `get`, `delete`, `index`, all keyed by `provider_id`; backed by `CreateProviderRequest`/`UpdateProviderRequest` (in `provider.py`, not opened in full this pass but referenced directly by `ProviderService.create`/`update`) and routed via `src/codemie/rest_api/routers/provider.py` (`create_provider`, `update_provider` at `PUT /providers/{provider_id}`, `list_providers`, `get_provider`).
- `ProviderService.update()` (`provider_service.py:55-66`) applies changes via `for field, value in request.dict().items(): if value: setattr(provider, field, value)` — note this only overwrites truthy values, a detail relevant to how a `False` `allow_user_context` update would need to be handled (a bare `if value:` check would silently skip setting the field back to `False` on update — worth flagging for the design/spec stage as a potential existing-code interaction, not something to fix in this research pass).

### 9.2 Per-assistant / per-datasource override — confirmed: none exists

- `ProviderToolkitsFactory` (`src/codemie/service/provider/provider_toolkits_factory.py`) is constructed from a single `Provider` row (`ProviderToolkitsFactory(provider)`, `self.provider = provider`) — there is no assistant identity threaded into this factory or its output toolkits.
- `ProviderToolkitsFactory.build()` (line 69) iterates `self.provider.provided_toolkits` and, for each toolkit, generates `get_tools`/`get_datasource_tools` closures (`_generate_get_tools` line 115, `_generate_get_datasource_tools` line 133) that close over `provider = self.provider` (the single provider-level row) and construct one `ProviderToolFactory(provider, toolkit_config, tool_config[, datasource=...])` per tool — always the same `provider_config` for every tool under that provider.
- `ProviderToolFactory.__init__` (`provider_tool_factory.py:59-70`) takes `provider_config: ProviderBase`, `toolkit_config: ProviderToolkit`, `tool_config: ProviderToolkit.Tool`, `provider_client`, and optionally `datasource: Optional[ProviderIndexInfo]`. None of these parameters carry an assistant id or an assistant-scoped config blob analogous to `MCPServerDetails`.
- The `datasource: ProviderIndexInfo` parameter participates only in `_resolve_configuration_params()` (`provider_tool_factory.py:201-214`), which decrypts datasource-backed configuration params (via `ProviderDatasourceSchemaService`/`decrypt_datasource_provider_fields`) for the tool call — it is not a context/gating mechanism and carries no user-context-related override.
- Confirmed: DSP is single-level configuration. One `Provider` row governs every tool and every datasource under it; there is no per-assistant or per-datasource JSON override point comparable to MCP's `MCPServerDetails` (which lives on `AssistantConfiguration.mcp_servers`). A new `allow_user_context` toggle on the DSP side therefore needs **only one resolution level** — a flat read of the field on `provider_config` — not the two-level precedence design the MCP side requires.

### 9.3 Runtime path: persisted config → `ProviderToolFactory.invoke()`

Traced concretely, in order:

1. `Provider.get_all()` / `Provider.get_by_id(provider_id)` (`provider.py`, via `BaseModelWithSQLSupport`) loads the persisted row(s) from the `providers` table.
2. `ProviderToolkitsFactory.get_toolkits()` (`provider_toolkits_factory.py:44-59`) — iterates `Provider.get_all()` and calls `ProviderToolkitsFactory(provider).build()` per provider; `get_toolkits_for_provider(provider_id)` (line 61-64) does the single-provider variant via `Provider.get_by_id(provider_id)`.
3. `ProviderToolkitsFactory.__init__(self, provider: ProviderBase)` (line 66-67) stores `self.provider = provider`.
4. `ProviderToolkitsFactory.build()` (line 69-82) creates one dynamic `ProviderToolkitBase` subclass per `toolkit_config` in `self.provider.provided_toolkits`, wiring `get_tools = self._generate_get_tools(toolkit_config)` and `get_datasource_tools = self._generate_get_datasource_tools(toolkit_config)`.
5. `_generate_get_tools`/`_generate_get_datasource_tools` (lines 115-154) close over `provider = self.provider` and, for each `tool_config`, instantiate `ProviderToolFactory(provider, toolkit_config, tool_config[, datasource=datasource])`.
6. `ProviderToolFactory.__init__` (`provider_tool_factory.py:59-70`) sets `self.provider_config = provider_config` — **this is the object/constructor that holds the persisted config in scope for everything downstream**, including `invoke()`.
7. `ProviderToolFactory.build()` (line 72-105) dynamically creates a `ProviderToolBase` (a `CodeMieTool` subclass, the actual LangChain-visible agent tool) whose generated `execute()` method (`_generate_execute`, line 114-129) closes over `context = self` (the `ProviderToolFactory` instance) and calls `context.invoke(user=..., project_id=..., request_uuid=..., params=kwargs, headers=..., datasource=context.datasource)`. The hedging fast path (`HedgingToolService`, referenced in the blast radius of `ProviderToolFactory`/`invoke`) also calls `invoke()` directly on a `ProviderToolFactory` instance, so both call sites reach the same method with the same `self.provider_config` in scope.
8. `ProviderToolFactory.invoke()` (`provider_tool_factory.py:131-199`) is therefore **the exact enforcement point**: `self.provider_config` (the persisted `Provider`/`ProviderBase` row) is already `self`-scoped there, with no additional plumbing required. Concretely: line 156 reads `self.provider_config.service_location_url`; line 161 reads `self.provider_config.configuration`; line 175 builds `user_context=UserContext.from_user(user).model_dump(exclude_none=True)`; line 177 logs it masked (`UserContext.from_user(user).masked_for_logging()`). A new check — e.g. `user_context = UserContext.from_user(user).model_dump(exclude_none=True) if self.provider_config.allow_user_context else None` (or omit the kwarg / pass `{}`) — would read a field that, once added to `ProviderBase`, is already present on the same `self.provider_config` object these two lines already dereference.

### 9.4 Existing gating patterns for consistency

- `ProviderConfiguration.auth_type: AuthType` (`provider.py:34-38`) — an enum, not a boolean; currently only one value (`BEARER`) is defined. Not a precedent for a boolean toggle, but confirms the `configuration` sub-object is where auth-adjacent behavior switches have historically been added.
- `ProviderToolkitMetadata.managed_fields: Optional[Dict[str, str]]` (`provider.py:121`) — governs which fields are "managed" (presumably for UI/admin editing purposes), not request content sent to the provider at invocation time. Not directly analogous.
- `provider_datasource_base_service.py`: `_filter_masked_fields`/`_filter_codemie_fields` (referenced in blast radius, not opened in full this pass) — filter which datasource config fields are exposed/persisted for datasource CRUD responses; a masking pattern, but scoped to datasource field visibility in API responses, not to what is sent to the provider at tool-invocation time.
- **Confirmed: no existing boolean toggle on `Provider`/`ProviderBase`/`ProviderToolkit`/`ProviderConfiguration` gates what is sent to a DSP provider at invocation time.** The DSP model family has no direct in-family precedent for an enable/disable field. For consistency, the recommendation (design-stage, not asserted here as fact) is to follow the *MCP-side* precedent instead — `MCPServerConfigData.single_usage: bool` / `allow_issuer_prefix_match: bool = Field(default=False, ...)` — so the two new `allow_user_context` fields (MCP and DSP) share the same declaration style even though they live in unrelated model families.

### 9.5 Testing landscape (DSP-specific detail)

- `tests/codemie/service/provider/test_provider_tool_factory.py` — exercises `ProviderToolFactory.build()`/`invoke()`, including construction with a `provider_config` fixture (`mock_provider`/`mock_provider_toolkit` fixtures referenced in the codegraph `instantiates` list) and `test_build_sanitizes_datasource_tool_name`. This is the file a new `allow_user_context` behavior test on the DSP side would extend.
- `tests/codemie/service/provider/test_provider_service.py` — exercises `ProviderService.create`/`update`/`get`/`delete`/`index` (`test_create`, `test_create_name_non_unique`, `test_update`, `test_index`, `test_get`, `test_get_not_found`), the CRUD surface a new `allow_user_context` field would need to round-trip through on create/update.
- `tests/codemie/rest_api/models/test_provider_model.py`, `tests/codemie/rest_api/models/test_provider_model_text_type.py` — cover `ProviderToolkitConfigParameter`/model-level validation for `provider.py`; relevant if the new field is validated at the model level.
- `tests/codemie/rest_api/security/test_user_context.py` — covers `UserContext` itself (shared by MCP and DSP), including `from_user`, `masked_for_logging`, round-trip equality; unaffected by either toggle since `UserContext` itself does not change, only whether it is attached.
- **Confirmed gap**: no test currently exercises an `allow_user_context`-style toggle on the DSP path — the field does not exist yet. Unlike the MCP-side gap (section 4), there is no missing *precedence* test to add here, since DSP has only one configuration level (section 9.2) — the new DSP test surface is a single conditional (`allow_user_context=True` sends `user_context`, `allow_user_context=False` omits it) in `test_provider_tool_factory.py`, plus a CRUD round-trip assertion in `test_provider_service.py`.
