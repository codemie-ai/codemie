# Technical Research

**Task**: mcp access-control rollback workflow-yaml
**Generated**: 2026-11-03
**Research path**: codegraph

---

## 1. Original Context

Implement EPMCDME-16489 (Subtask A of EPMCDME-15629): one MCP link predicate, links-only save validation, "disable and report" restore with in-place workflow YAML editing, close validation gaps (marketplace sub-assistants publish, workflow generator persist=True). The full verbatim ticket body (description, problems, acceptance criteria 1-13) is in C:/Epam/codemie-dev/codemie/docs/superpowers/tasks/2026-11-03-mcp-link-predicate-restore/ticket.md — read it first and treat it as the task text. Extra context: the unmerged git branch `EPMCDME-15629_mcp-assets-count` (use `git log main..EPMCDME-15629_mcp-assets-count` / `git diff main...EPMCDME-15629_mcp-assets-count`, read-only) holds prior exploratory work for the parent story, and MR codemie!4439 discussed the plan; note what in that branch is reusable for this subtask (e.g. predicate, access_control changes) versus out of scope (tables, counts — other subtasks).

(Ticket body: `ticket.md` in the run dir, not repeated here.)

---

## 2. Codebase Findings

### Existing Implementations

Purpose A: decide which `mcp_servers` entries count as catalog links and validate them on save.
- `src/codemie/service/mcp/access_control.py:153` `validate_on_save` — restricted-mode check, then `catalog_refs = [s for s in mcp_servers if s.mcp_config_id]` (:161; ignores `enabled` and `use_custom_config`), duplicate check over the whole list (:165-169), then `_build_catalog_map` (:86) + `_validate_catalog_entries` (:125; missing/inactive/not public -> one generic "MCP server not available in the catalogue" message, exact reason only in the log).
- No link-predicate function exists. Related inline rules: `_restricted_violations` (:71, `mcp_config_id and (use_custom_config or inline config)`), `filter_for_runtime` (:234, restricted mode only), `resolve_catalog_config` (:259, `use_custom_config` -> inline, else catalog).
- Model: `MCPServerDetails` `src/codemie/rest_api/models/assistant.py:174` — `enabled: bool = True` (:191), `mcp_config_id` (:192), `use_custom_config: bool = False` (:198).
- Callers of `sanitize_for_save` (-> `validate_on_save`): assistant create/update `routers/assistant/management.py:263,397`; `routers/assistant/execution.py:98`; skills `service/skill_service.py:573,725`; preconfigured assistants `src/external/deployment_scripts/preconfigured_assistants.py:155,233`. Workflows: `WorkflowService.validate_for_update` (`service/workflow_service.py:249`, via `validate_and_strip_workflow_mcp_servers` :260) and router `routers/workflow.py:521` (`_persist_and_finalize_new_workflow`, validates raw-request and yaml-parsed copies).
- `collect_workflow_mcp_servers` (`access_control.py:207`) flattens `workflow.assistants[*].mcp_servers` plus `workflow.tools[*].mcp_server` into one list; callers: `routers/workflow.py:521`, `access_control.py:220`. Per-node grouping is not available from its return value.

Purpose B: bring stored MCP data back (rollback/restore).
- `MCPAccessControlService.validate_on_restore` `access_control.py:175` — restricted mode only; no catalog check. Sole caller: `AssistantVersionService.rollback_to_version` `service/assistant/assistant_version_service.py:267`. It copies `target_config.mcp_servers` unchanged into the new `AssistantConfiguration` (:295) and, via `update_assistant_config_fields` (:311), into the assistant.
- Assistant rollback endpoint `routers/assistant/versions.py:161`; returns `BaseResponse(message=...)` (:169).
- Workflow rollback `service/workflow/workflow_version_service.py:43`: swaps `workflow.yaml_config` to `target.yaml_config`, `parse_execution_config()`, `_validate_restored_workflow` -> `WorkflowService().validate_for_update` (:108; so a rollback fails today on a stale/duplicate MCP), then `workflow.update(yaml_config_history=[new_entry, *history])` (:104); on error restores original yaml. Router `routers/workflow.py:983-999` returns `BaseResponse`.
- Preconfigured: assistants via `sanitize_for_save` (raises at startup today). `preconfigured_workflows.py:59-85` `create_preconfigured_workflow` builds `WorkflowConfig(**template.model_dump())` and `save(refresh=True)` with no MCP validation at all (no `MCPAccessControlService` reference in file).
- "Import-like restores": not enumerated further in this run (see Risks).

Purpose C: edit workflow YAML text in place.
- None found. `yaml.compose` is used once, read-only, in `src/codemie/workflows/validation/line_lookup.py:67` (builds path->line map using `start_mark.line`). No ruamel in src or pyproject. Derived `assistants`/`tools` come from `WorkflowConfig.parse_execution_config()`.

Purpose D: disabled-link runtime behaviour.
- `src/codemie/workflows/nodes/tool_node.py:131-132` raises `MCP server '<name>' is disabled`. Agent-node skip of disabled server not located in this run.

Purpose E: gaps.
- Marketplace sub-assistants: `routers/assistant/_sub_assistants.py:136-137` `_apply_sub_assistant_settings` assigns `settings.mcp_servers` with no validation (sibling `categories` is validated at :141; `_reject_unreviewed_sub_assistant_changes` :82 only guards APPROVED/PENDING_REVIEW).
- Workflow generator: `service/workflow_generator_service.py:109-116` — `WorkflowExecutor.validate_workflow` then `WorkflowService().create_workflow(...)`; no `MCPAccessControlService` call. Router equivalent: `routers/workflow.py:499-528`.

Other touched code: `workflow_service.py:216` `create_workflow` (plain save), `WorkflowRollbackRequest` `rest_api/models/workflow_version.py:31`.

### Decisions Already in the Code
- Catalog refusal message is deliberately generic; exact reason only logged (`access_control.py:143`), so "reason" (`missing/inactive/not_public`) in the new report is a new exposure channel (alternative: current opaque message).
- `strip_inline_config` / `_strip_one` are no-ops; inline overrides are kept (`access_control.py:190-197`).
- Convention: all save paths funnel through `sanitize_for_save` / `validate_on_save`; workflow router validates both raw and yaml-parsed copies (`routers/workflow.py:512-521`).
- Both rollback endpoints return `BaseResponse` (message only), not a rich model.
- Existing test `tests/unit/service/mcp/test_access_control.py:131` `test_disabled_server_with_invalid_catalog_still_raises` asserts the behaviour AC 2 changes.
- OpenAPI contract guarded by `tests/codemie/rest_api/router_guards/test_openapi_snapshot.py` (+ `regenerate_baselines.py`).

### Task Statements Checked Against the Code
- "`validate_on_save` checks every entry with `mcp_config_id`, even disabled/custom" — holds (`access_control.py:161`).
- "Duplicate check runs over the whole workflow" — holds (`:165-169` over flattened list from `collect_workflow_mcp_servers`).
- "`validate_on_restore` only checks restricted mode" — holds (`:175-188`).
- "Sub-assistant publish line 136 writes MCP data unchecked" — holds (`_sub_assistants.py:136-137`).
- "Generator `workflow_generator_service.py:111` -> `create_workflow` bypasses router check at `routers/workflow.py:521`" — holds (create call is :116 in current source; no check).
- "`tool_node.py:130` error message" — holds (line 131-132 now).
- "`preconfigured_workflows.py:59-85`" in scope — it has no MCP validation today (nothing to "stop raising").
- "preconfigured_assistants.py:155,233" — holds (`sanitize_for_save`).
- "12 production workflows / 23 duplicates; refused on dev" — unverified (production data, dev environment; needs a dev run, AC 13).
- "No comments/format loss: no new dependency" — keeps in place `yaml.compose` precedent (`line_lookup.py:67`); PyYAML only.
- "Marketplace moderation only reads `mcp_servers`" — not re-verified this run.
- Branch `EPMCDME-15629_mcp-assets-count` / MR 4439 — unverified: this agent has no shell/git tool, so the branch diff was not read.

### Consumers of Changed Contracts
- `validate_on_save`: `sanitize_for_save` (management.py:263,397; execution.py:98; skill_service.py:573,725; preconfigured_assistants.py:155,233), `validate_and_strip_workflow_mcp_servers` (workflow_service.py:260), `routers/workflow.py:521`; tests `tests/unit/service/mcp/test_access_control.py`, `tests/codemie/rest_api/routers/test_contract_baseline.py`.
- `validate_on_restore`: only `assistant_version_service.py:267`.
- `collect_workflow_mcp_servers`: `routers/workflow.py:521`, `access_control.py:220`; no direct tests found.
- `validate_and_strip_workflow_mcp_servers`: `workflow_service.py:260` only; no tests found by graph.
- `WorkflowVersionService.rollback_to_version`: `routers/workflow.py:992`; tests `tests/codemie/service/test_workflow_version_service.py`, `tests/codemie/rest_api/routers/test_workflow_versions.py`.
- `AssistantVersionService.rollback_to_version`: `routers/assistant/versions.py:161`; tests `tests/codemie/service/assistant/test_assistant_version_service.py`.
- `MCPServerDetails.enabled`: `tool_node.py:131`; other readers of `.enabled`/`use_custom_config` (toolkit_service, agent_node, mcp_config_service delete/usage counts) not enumerated.

### Architecture and Layers Affected
Service (`service/mcp/access_control.py`, `workflow_service.py`, `workflow/workflow_version_service.py`, `assistant/assistant_version_service.py`, `workflow_generator_service.py`), REST routers (`routers/workflow.py`, `routers/assistant/versions.py`, `_sub_assistants.py`), response models (`rest_api/models/workflow_version.py`, `BaseResponse`), deployment scripts (`src/external/deployment_scripts/`), OpenAPI baseline.

### Integration Points
`MCPConfig.get_by_ids` (catalog lookup, `mcp_config.py`), `WorkflowConfig.parse_execution_config` (derived columns), `WorkflowExecutor.validate_workflow`, `customer_config.is_component_enabled("mcpCustomServersDisabled")`.

### Patterns and Conventions
Static/class-method service (`MCPAccessControlService`), `ValidationException` for refusals, loguru-style `logger.warning`, tests under `tests/unit/service/mcp/` with `_server()`/`_catalog_entry()` helpers.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/` exists (integration/mcp-integration.md, architecture/service-layer-patterns.md, api/endpoint-conventions.md, testing/*); not read in depth this run.

### Architectural Decisions
Parent plan in MR !4439 (unread). Inline comments in `access_control.py:143` (generic message).

### Derived Conventions
See Section 2.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/unit/service/mcp/test_access_control.py` — save validation open/restricted modes, restore.
- `tests/codemie/service/test_workflow_version_service.py`, `tests/codemie/rest_api/routers/test_workflow_versions.py`, `tests/codemie/service/assistant/test_assistant_version_service.py` — rollbacks.
- `tests/codemie/rest_api/router_guards/test_openapi_snapshot.py` — OpenAPI baseline.
- `tests/codemie/rest_api/routers/test_contract_baseline.py` — touches `sanitize_for_save`.

### Testing Framework and Patterns
pytest (repo policy: `--skip-enterprise`, run in docker/WSL per memory).

### Coverage Gaps
`collect_workflow_mcp_servers`, `validate_and_strip_workflow_mcp_servers`, sub-assistant settings path, generator persist path, preconfigured workflows: no tests found by graph.

---

## 5. Configuration and Environment

### Environment Variables
None found specific to the feature.

### Configuration Files
`customer_config` component flag `mcpCustomServersDisabled` (restricted mode).

### Feature Flags and Deployment Concerns
Preconfigured assistants/workflows run at deployment startup; OpenAPI snapshot baseline regenerated via `regenerate_baselines.py`.

---

## 6. Risk Indicators

- Existing test at `test_access_control.py:131` asserts the current (to-be-changed) behaviour.
- `collect_workflow_mcp_servers` returns a flat list; per-node duplicate check needs per-node data (Speculative: new grouping or a second collector).
- No existing text-position YAML editor; only a read-only `yaml.compose` use (`line_lookup.py:67`). Flow style, anchors/merge keys (`<<: *mcp`) are untested in repo.
- Rollback flow validates after `parse_execution_config`; derived `assistants`/`tools` columns must come from edited YAML (Speculative: re-parse after edit).
- Both rollback endpoints return `BaseResponse`; `disabled_mcp_servers` needs a response-model change plus OpenAPI snapshot update.
- `preconfigured_workflows.py` currently has no MCP validation; AC 7 wording ("instead of raising") does not match it.
- Branch `EPMCDME-15629_mcp-assets-count` and MR !4439 not inspected (no git access in this agent); reusable/out-of-scope split must be established by caller.
- "Import-like restores" and agent_node disabled-skip behaviour not enumerated.
- Thin tests around workflow validation helpers and generator persist.

---

## 7. Summary for Complexity Assessment

Layers: service (access control, workflow/assistant version services, workflow service, generator), REST routers and response models (additive field on two rollback responses, OpenAPI snapshot), deployment scripts, tests. Roughly 10-12 source files across five save/restore entry points plus two unvalidated write paths, all converging on `MCPAccessControlService`, which already centralises validation and has a clear seam for a predicate and a restore helper.

Novelty: the in-place YAML editor (block, flow, anchors, missing key, byte-exact preservation) has no precedent in the repo beyond a read-only `yaml.compose` use; this is the main technical risk. Per-node duplicate checking needs node-scoped collection that the current flat collector does not provide.

Test posture: access-control save validation is well covered; version rollbacks have tests; the workflow helpers, sub-assistant path, generator persist, and preconfigured workflows have none found. One existing test encodes behaviour that changes. Open items: branch/MR content, dev confirmation of the 12 production workflows (AC 13).

---

## 8. External References

- `ticket.md` (run dir) — resolved; read in full; facts used throughout.
- Branch `EPMCDME-15629_mcp-assets-count` and MR codemie!4439 — unresolved: no shell/git tool available to this agent; not read.

## Facts Added by Review

Researched for the names the spec relies on, after the analysis above was written. Read like the facts layer; where a line here contradicts one above, this one was read later.

- `MCPAccessControlService._validate_restricted_mode` — `src/codemie/service/mcp/access_control.py:101` (static). Calls `_restricted_violations(servers)` (:71) on the whole list it is given; no `enabled` filtering. `custom` = entries without `mcp_config_id`; `overridden` = entries with `mcp_config_id` and (`use_custom_config` or `_has_inline_config`, i.e. any non-`_CATALOG_REF_ALLOWED_FIELDS` field set, :32-46, :78). Raises `ValidationException` (custom first, then overridden) with a message naming up to 5 cleaned labels (`_labels`, `_clean_name`).
- Only consumer: `validate_on_save` at `access_control.py:158-159`, run only when `customer_config.is_component_enabled("mcpCustomServersDisabled")`. No other call site in `src` or `tests` (grep).
- Sibling: `validate_on_restore` (`access_control.py:174`) does not call `_validate_restricted_mode`; it calls `_restricted_violations` directly (:181), combines `custom + overridden`, raises "Can't restore this version…". Also no `enabled` filter. Docstring: catalog availability is not re-checked.
- `MCPServerDetails` (`src/codemie/rest_api/models/assistant.py:191-198`): `enabled: bool = True`, `mcp_config_id: Optional[str]`, `use_custom_config: bool = False`.
- Other callers of `validate_on_save` reach it via `sanitize_for_save` (:200; used at `src/external/deployment_scripts/preconfigured_assistants.py:155,233`) and `validate_and_strip_workflow_mcp_servers` (:218, which feeds `collect_workflow_mcp_servers` (:207) flattening all assistants and tools into one list).
- `MCPConfigService.adjust_usage(increments: set[str], decrements: set[str])` — `src/codemie/service/mcp_config_service.py:830` (classmethod). Takes all ids, opens a `Session(MCPConfig.get_engine())`, `SELECT ... WHERE id IN (...) FOR UPDATE`, +1 per increment id, `max(0, n-1)` per decrement id, ids not found are skipped silently, one `commit()`. Writes `MCPConfig.usage_count` (`src/codemie/rest_api/models/mcp_config.py:253`, int default 0). Older single-id siblings `increment_usage` / `decrement_usage` (:816) exist.
- `src/codemie/service/mcp/toolkit_service.py` — `MCPToolkitService`. `get_mcp_server_tools` calls `MCPAccessControlService.filter_for_runtime` (:229), then `_collect_server_results` (:281); at **:299-301** `if not mcp_server.enabled: logger.debug("Skipping disabled MCP server"); continue`. This is the line the spec cites for the "agent node skips the server" claim. Consumers of `get_mcp_server_tools`: `src/codemie/service/tools/toolkit_service.py:1058`, `src/codemie/workflows/nodes/tool_node.py:136`, `src/codemie/workflows/validation/resources.py:376`, `src/codemie/workflows/config_resources_validation.py:181`, `src/codemie/service/mcp/mcp_tester.py:104`, `src/codemie/service/tools/mcp_tools_info_service.py:58`. Other importers: `assistant_user_mapping_service.py:26` (`MCP_TOOL_CONFIG_PREFIX`), `enterprise/mcp_auth/_initiate.py:154`, `enterprise/mcp_auth/dependencies.py:632,702`.
- `src/codemie/service/tools/toolkit_service.py` — `ToolkitService` (:117). On the MCP path: `_merge_skill_mcp_servers` (:443) returns the assistant's `mcp_servers` plus skill-contributed ones de-duplicated by name (no `enabled` or `mcp_config_id` handling); the traditional collection flow (:1054-1071) passes that list to `MCPToolkitService.get_mcp_server_tools` when `config.MCP_CONNECT_ENABLED`. Its other content (feature-flag toolkits at :255-316, etc.) does not touch MCP links. Consumers of `ToolkitService.get_tools`: `src/codemie/service/assistant_service.py:591,874`, `src/codemie/service/tools/tool_service.py:182`. `get_toolkit_methods`: `workflows/nodes/tool_node.py:207`, `tool_execution_service.py:246`.
- Workflow agent nodes: `src/codemie/workflows/utils/utils.py:541` calls `AssistantService.build_agent_for_workflow`; no direct `ToolkitService.get_tools` or `get_mcp_server_tools` call exists under `src/codemie/workflows` outside the three listed above. Whether `build_agent_for_workflow` reaches `assistant_service.py:591/874` was not traced here (unverified).
- Tool node: `tool_node.py:129-132` raises `ValueError("MCP server '<name>' is disabled")` when `not mcp_server.enabled`, before `get_mcp_server_tools`.
- Catalog-availability failures are logged with the exact reason and surfaced as one generic message (`access_control.py:143`).
- Spec: "assistant path reaches `toolkit_service.py:299`" — holds for `src/codemie/service/mcp/toolkit_service.py:299` via `ToolkitService` (tools) :1058 -> `get_mcp_server_tools`. Workflow agent-node path: unverified (see above).
- Spec: "Other live copies of the link rule left for 16490 … editing management.py/toolkit_service.py" — copies found: `management.py:582,593,602,603`, `preconfigured_assistants.py:181,182,265`, `mcp/toolkit_service.py:299` (enabled only), `access_control.py:161`, `:73-74` (`_restricted_violations`).
