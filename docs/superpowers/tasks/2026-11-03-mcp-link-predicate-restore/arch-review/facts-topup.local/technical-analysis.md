# Technical Research (top-up)

**Task**: toolkit_service _validate_restricted_mode adjust_usage
**Generated**: 2026-11-03
**Research path**: codegraph

---

## 1. Original Context

EPMCDME-16489 — one MCP link predicate, links-only save validation, disable-and-report restore with in-place workflow YAML editing, close validation gaps (full text in `C:/Epam/codemie-dev/codemie/docs/superpowers/tasks/2026-11-03-mcp-link-predicate-restore/ticket.md`). Top-up research - record facts only, for the names the spec relies on that the analysis does not cover: toolkit_service.py, _validate_restricted_mode, adjust_usage. For each: where it is defined, what it does today on the path this task changes, and who consumes it.

---

## 2. Codebase Findings

### Existing Implementations

**Purpose: restricted-mode check on save/restore**
- `MCPAccessControlService._validate_restricted_mode` — `src/codemie/service/mcp/access_control.py:101` (static). Calls `_restricted_violations(servers)` (:71) on the whole list it is given; no `enabled` filtering. `custom` = entries without `mcp_config_id`; `overridden` = entries with `mcp_config_id` and (`use_custom_config` or `_has_inline_config`, i.e. any non-`_CATALOG_REF_ALLOWED_FIELDS` field set, :32-46, :78). Raises `ValidationException` (custom first, then overridden) with a message naming up to 5 cleaned labels (`_labels`, `_clean_name`).
- Only consumer: `validate_on_save` at `access_control.py:158-159`, run only when `customer_config.is_component_enabled("mcpCustomServersDisabled")`. No other call site in `src` or `tests` (grep).
- Sibling: `validate_on_restore` (`access_control.py:174`) does not call `_validate_restricted_mode`; it calls `_restricted_violations` directly (:181), combines `custom + overridden`, raises "Can't restore this version…". Also no `enabled` filter. Docstring: catalog availability is not re-checked.
- `validate_on_save` (:152-172) order today: empty -> return; restricted mode (if flag); `catalog_refs = [s for s in mcp_servers if s.mcp_config_id]` (:161, no `enabled` / `use_custom_config` filter); duplicate-id check over that list (:165-169, message "The same MCP server from the catalogue was added more than once. Keep only one."); `_build_catalog_map` (:86) and `_validate_catalog_entries` (:125; reasons not found / inactive / not public are logged, one user message "MCP server not available in the catalogue").
- `MCPServerDetails` (`src/codemie/rest_api/models/assistant.py:191-198`): `enabled: bool = True`, `mcp_config_id: Optional[str]`, `use_custom_config: bool = False`.
- Other callers of `validate_on_save` reach it via `sanitize_for_save` (:200; used at `src/external/deployment_scripts/preconfigured_assistants.py:155,233`) and `validate_and_strip_workflow_mcp_servers` (:218, which feeds `collect_workflow_mcp_servers` (:207) flattening all assistants and tools into one list).
- Runtime sibling: `filter_for_runtime` (:234) in restricted mode only, drops servers without id or with missing/inactive/non-public config; does not look at `enabled`.

**Purpose: MCP usage counters**
- `MCPConfigService.adjust_usage(increments: set[str], decrements: set[str])` — `src/codemie/service/mcp_config_service.py:830` (classmethod). Takes all ids, opens a `Session(MCPConfig.get_engine())`, `SELECT ... WHERE id IN (...) FOR UPDATE`, +1 per increment id, `max(0, n-1)` per decrement id, ids not found are skipped silently, one `commit()`. Writes `MCPConfig.usage_count` (`src/codemie/rest_api/models/mcp_config.py:253`, int default 0). Older single-id siblings `increment_usage` / `decrement_usage` (:816) exist.
- Consumers (all in `src`; 22 test matches, all in `tests/external/deployment_scripts/test_preconfigured_assistants.py`):
  - `src/codemie/rest_api/routers/assistant/management.py`: `_track_mcp_usage_on_create` (:578; ids of entries with `enabled` and `mcp_config_id`, called at :188), `_track_mcp_usage_on_delete` (:589; ids of all entries with `mcp_config_id`, ignoring `enabled`; called at :520), `_track_mcp_usage_changes` (:600; delta of enabled-id sets old vs new, called at :445). Each wraps the call in try/except and only logs a warning.
  - `src/external/deployment_scripts/preconfigured_assistants.py`: :193 (update: enabled-id delta, after `existing_assistant.save()`), :267 (create: enabled ids, increments only); both try/except with a warning.
- No call from rollback, workflow, skill or sub-assistant paths found. Each of the above copies computes its own "enabled and mcp_config_id" id set; none checks `use_custom_config`.

**Purpose: runtime use of MCP servers (the "toolkit_service.py" the spec names)**

There are two files with this name:
- `src/codemie/service/mcp/toolkit_service.py` — `MCPToolkitService`. `get_mcp_server_tools` calls `MCPAccessControlService.filter_for_runtime` (:229), then `_collect_server_results` (:281); at **:299-301** `if not mcp_server.enabled: logger.debug("Skipping disabled MCP server"); continue`. This is the line the spec cites for the "agent node skips the server" claim. Consumers of `get_mcp_server_tools`: `src/codemie/service/tools/toolkit_service.py:1058`, `src/codemie/workflows/nodes/tool_node.py:136`, `src/codemie/workflows/validation/resources.py:376`, `src/codemie/workflows/config_resources_validation.py:181`, `src/codemie/service/mcp/mcp_tester.py:104`, `src/codemie/service/tools/mcp_tools_info_service.py:58`. Other importers: `assistant_user_mapping_service.py:26` (`MCP_TOOL_CONFIG_PREFIX`), `enterprise/mcp_auth/_initiate.py:154`, `enterprise/mcp_auth/dependencies.py:632,702`.
- `src/codemie/service/tools/toolkit_service.py` — `ToolkitService` (:117). On the MCP path: `_merge_skill_mcp_servers` (:443) returns the assistant's `mcp_servers` plus skill-contributed ones de-duplicated by name (no `enabled` or `mcp_config_id` handling); the traditional collection flow (:1054-1071) passes that list to `MCPToolkitService.get_mcp_server_tools` when `config.MCP_CONNECT_ENABLED`. Its other content (feature-flag toolkits at :255-316, etc.) does not touch MCP links. Consumers of `ToolkitService.get_tools`: `src/codemie/service/assistant_service.py:591,874`, `src/codemie/service/tools/tool_service.py:182`. `get_toolkit_methods`: `workflows/nodes/tool_node.py:207`, `tool_execution_service.py:246`.
- Workflow agent nodes: `src/codemie/workflows/utils/utils.py:541` calls `AssistantService.build_agent_for_workflow`; no direct `ToolkitService.get_tools` or `get_mcp_server_tools` call exists under `src/codemie/workflows` outside the three listed above. Whether `build_agent_for_workflow` reaches `assistant_service.py:591/874` was not traced here (unverified).
- Tool node: `tool_node.py:129-132` raises `ValueError("MCP server '<name>' is disabled")` when `not mcp_server.enabled`, before `get_mcp_server_tools`.

### Decisions Already in the Code
- Catalog-availability failures are logged with the exact reason and surfaced as one generic message (`access_control.py:143`).
- Usage counts are adjusted by callers after the save, best-effort (try/except warning) in every call site; the counting rule is "enabled and has `mcp_config_id`" on create/update and "has `mcp_config_id`" on delete.
- Open-mode rollback is documented as unchanged: `validate_on_restore` checks restricted mode only.

### Task Statements Checked Against the Code
- Spec: "`_validate_restricted_mode` (:101, called at :158-159) and `validate_on_restore` (:174) run only over enabled entries" — contradicted for today's code: neither filters on `enabled` (this is the spec's proposed change; the line numbers are right).
- Spec: "rollback and disable do not call `adjust_usage`" — holds (no call outside management.py create/update/delete and preconfigured_assistants).
- Spec: "assistant path reaches `toolkit_service.py:299`" — holds for `src/codemie/service/mcp/toolkit_service.py:299` via `ToolkitService` (tools) :1058 -> `get_mcp_server_tools`. Workflow agent-node path: unverified (see above).
- Spec: "Other live copies of the link rule left for 16490 … editing management.py/toolkit_service.py" — copies found: `management.py:582,593,602,603`, `preconfigured_assistants.py:181,182,265`, `mcp/toolkit_service.py:299` (enabled only), `access_control.py:161`, `:73-74` (`_restricted_violations`).

### Consumers of Changed Contracts
- `validate_on_save` behaviour: `sanitize_for_save` (preconfigured assistants :155,:233), `validate_and_strip_workflow_mcp_servers`; `_validate_restricted_mode` itself has only `validate_on_save` as consumer. Other callers of `validate_on_save`/`sanitize_for_save` in routers/services were covered by the main analysis and not re-searched here.
- `adjust_usage` signature: 5 call sites listed above; unchanged by the spec.
- `MCPConfig.usage_count`: written by `increment_usage`, `decrement_usage`, `adjust_usage`; reads by consumers were not searched.

### Architecture and Layers Affected
Service layer (`service/mcp/access_control.py`, `service/mcp_config_service.py`, `service/tools/toolkit_service.py`, `service/mcp/toolkit_service.py`); REST router layer (`routers/assistant/management.py`); deployment scripts (`src/external/deployment_scripts`); workflow runtime (`workflows/nodes/tool_node.py`).

### Integration Points
`MCPAccessControlService` reads `MCPConfig` rows (`get_by_ids`) and `customer_config` flag `mcpCustomServersDisabled`; `MCPConfigService.adjust_usage` writes `MCPConfig` in PostgreSQL.

### Patterns and Conventions
Static/class methods on service classes; `ValidationException` for user-facing validation errors; best-effort side-effects wrapped in try/except with `logger.warning`.

---

## 3. Documentation Findings
Not re-searched in this top-up.

## 4. Testing Landscape
- `adjust_usage`: tested only through `tests/external/deployment_scripts/test_preconfigured_assistants.py` (22 matches). No test file contains `_validate_restricted_mode` by name. Tests for `management.py` usage tracking and for `MCPConfigService.adjust_usage` itself were not found by name search.

## 5. Configuration and Environment
- `mcpCustomServersDisabled` (customer_config component flag) gates `_validate_restricted_mode`, `validate_on_restore` and `filter_for_runtime`.
- `config.MCP_CONNECT_ENABLED` gates MCP tool loading in `ToolkitService` (:1055) and `tool_node.py:126`.

---

## 6. Risk Indicators
- Speculative: the main analysis cites "toolkit_service.py" ambiguously; two files exist (`service/tools/` and `service/mcp/`), and the :299 line is in the `mcp/` one.
- Speculative: workflow agent-node path to the :299 skip is not traced.
- Existing divergence between delete (ignores `enabled`) and create/update (requires `enabled`) in `management.py` counting.
- No direct unit test for `adjust_usage` or `_validate_restricted_mode` found by name.

---

## 7. Summary for Complexity Assessment
Top-up only. `_validate_restricted_mode` is a private static in `access_control.py:101`, called solely from `validate_on_save`, and it runs over the whole list with no `enabled` filter, as does `validate_on_restore`. `adjust_usage` is a locked bulk counter update in `mcp_config_service.py:830` called from five sites (management.py create/update/delete, preconfigured assistants create/update), never from rollback or workflow paths. "toolkit_service.py" resolves to two files; the runtime skip of disabled servers is `service/mcp/toolkit_service.py:299`.

---

## 8. External References
None named by the task beyond `ticket.md`, which was read.
