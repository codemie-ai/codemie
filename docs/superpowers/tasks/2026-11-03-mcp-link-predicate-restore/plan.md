# EPMCDME-16489 Implementation Plan

**Goal:** One MCP link predicate, links-only save validation, disable-and-report restore with in-place workflow YAML edit, two validation gaps closed. Source: `spec.md` (+ `ticket.md` AC 1-13), `technical-analysis.md`.

**Conventions:** Commit per task using the repository's existing convention. Tests: pytest under `tests/unit/service/mcp/` (helpers `_server()`/`_catalog_entry()`), run with `--skip-enterprise` in docker/WSL. No whole-suite gates here.

**Decisions folded in from architecture review:**
- AR-14: `disable_dead_links_in_yaml` stays pure. 16640 syncs after `workflow.update` (rollback) and after `save` (preconfigured) through an empty seam `after_workflow_links_changed(workflow)` in `access_control.py`, called by both after persistence.
- AR-15 (**superseded by the spec rule**): preconfigured workflows and assistants call the helper with `strict_lookup=True`, where only an empty catalogue result (or a failed read) is a lookup failure: template saved as written, error logged with template name. A partial miss disables the missing links. Rollback uses the full rule. Original wording, now void: any missing id = lookup failure. Seed order checked: `main.py:544-553` runs assistants, skills, workflows, katas; no src code seeds the MCP catalog, so the catalog is not guaranteed populated and the strict rule is required.
- AR-16 (user decision): AC 2 "never fail" covers the catalog-availability check only (and duplicates); enabled custom entries in restricted mode stay refused as today. Test included in Task 1.
- Two files are named `toolkit_service.py`: `service/mcp/toolkit_service.py` (disabled skip at :299; runtime, unchanged) and `service/tools/toolkit_service.py` (`:1581` copy of the link rule, left for 16490). Neither is edited.

---

### Task 1: Predicate, shared reason, links-only save, restricted-mode enabled filter
**Files:** `src/codemie/service/mcp/access_control.py:71,101,125,153,234`; `tests/unit/service/mcp/test_access_control.py` (rewrite test at :131)
- Add exported `is_enabled(server)`, `is_link(server)` (enabled AND `mcp_config_id` AND NOT `use_custom_config`) and `unavailable_reason(entry|None) -> Literal["missing","inactive","not_public"] | None`. Reuse `unavailable_reason` in `_validate_catalog_entries` (log text derived from it) and `filter_for_runtime`; messages unchanged.
- Split `validate_on_save` into `check_duplicates`, `check_restricted`, `check_catalog`; duplicates and catalog run over links only. Put the enabled filter inside `_restricted_violations` (also covers `validate_on_restore`).
- Comment on `is_link` that an enabled custom-config entry carrying an id is not a link here while `management.py:582-603` counts it; 16490 decides.
Test-first: yes — disabled entry with dead id saves in open and restricted mode; disabled plus enabled entry with same id saves; two enabled links same id refused (assistant list); re-enabled dead entry refused; restricted mode: disabled custom entry saves, same entry enabled refused, enabled custom entry with dead id still refused (AR-16); predicate unit cases (missing `enabled`, custom-config with id, no id).

### Task 2: Node-scoped collector and workflow validation entry point
**Files:** `access_control.py:207-221`; `src/codemie/service/workflow_service.py:249-262`; `src/codemie/rest_api/routers/workflow.py:520-521`; tests in `tests/unit/service/mcp/test_access_control.py`
- Add typed `NodeAddress` (frozen dataclass; `render()` -> `assistants[i].mcp_servers[j]` / `tools[k].mcp_server`) and `collect_workflow_mcp_nodes(workflow) -> list[tuple[NodeAddress, MCPServerDetails]]`; keep `collect_workflow_mcp_servers` as a flat view of it.
- Add `validate_workflow_mcp(workflow)`: duplicates per node over links, restricted and catalog once over the flat list with one `get_by_ids`. `validate_and_strip_workflow_mcp_servers` and the router loop (both candidates) call it instead of `validate_on_save(flat)`.
Test-first: yes — same catalog id in two assistant nodes and in a tool node saves; same id twice within one node refused; restricted/catalog checks still run over all nodes.

### Task 3: Restore helper (copies plus report)
**Files:** `access_control.py` (next to `validate_on_restore`); `src/codemie/rest_api/models/mcp_restore.py` (new: `DisabledMcpServer{name, mcp_config_id, reason, address: str|None}`, `DisabledMcpResponse(BaseResponse){disabled_mcp_servers: list[DisabledMcpServer] = []}`)
- `restore_mcp_servers(servers, addresses=None, strict_lookup=False) -> (new_servers, report)`: one `get_by_ids`; for each link with a `unavailable_reason`, the copy (`model_copy`) has `enabled=False` and a report item; inputs never mutated; non-links untouched. `strict_lookup=True` raises `CatalogLookupError` when any link id has no row or `get_by_ids` raises.
Test-first: yes — dead link disabled in copy and reported with each reason; input list objects unchanged; custom-config and already disabled entries with same id untouched; one `get_by_ids` call; strict mode raises on a partial miss.

### Task 4: Assistant rollback uses the helper and reports
**Files:** `assistant_version_service.py:267,295,311`; `routers/assistant/versions.py:161-169`; `tests/codemie/service/assistant/test_assistant_version_service.py`
- After `validate_on_restore`, run the helper on `target_config.mcp_servers`; put the copy into `new_config` and pass `target_config.model_copy(update={"mcp_servers": copy})` to `update_assistant_config_fields`. The loaded target is never written. No `adjust_usage` call. Return the report with the result; router responds with `DisabledMcpResponse`.
Test-first: yes — rollback to a version with a deleted catalog config succeeds, new config and assistant have the entry disabled, response lists it, stored target version `mcp_servers` unchanged, no usage-count call.

### Task 5: Workflow YAML in-place editor
**Files:** `src/codemie/service/mcp/workflow_yaml_editor.py` (new; imports `access_control`, not the reverse; PyYAML `yaml.compose` marks, precedent `workflows/validation/line_lookup.py:67`); `tests/unit/service/mcp/test_workflow_yaml_editor.py`
- `disable_dead_links_in_yaml(yaml_text, *, strict_lookup=False) -> (new_text, report)`, pure. Reads the text through a scratch `WorkflowConfig(yaml_config=text).parse_execution_config()` (not the live workflow; verify no import cycle), collects addresses via Task 2, calls the Task 3 helper, then edits every reported address: `enabled: true` -> `false`; missing key -> new line at mapping indentation; flow mapping -> inline insert; `<<: *mcp` alias -> edit the alias if it sets `enabled`/`mcp_config_id`, else the anchor (report lists every node reached).
- Confirm by re-parse: every reported address disabled and no unreported (address, enabled) pair changed; else try alias-override; else raise (nothing written).
Test-first: yes — byte-identical output outside edits for block, flow, comments, CRLF, tabs-free multi-document; same dead id in two nodes both disabled; custom-config entry with that id untouched; shared anchor that would flip an unreported node -> override or error; no-dead-links returns text unchanged.

### Task 6: Workflow rollback uses the editor and reports
**Files:** `workflow_version_service.py:83-104`; `routers/workflow.py:977-999`; `access_control.py` (empty seam `after_workflow_links_changed`); OpenAPI baseline via `tests/codemie/rest_api/router_guards/regenerate_baselines.py`; tests `tests/codemie/service/test_workflow_version_service.py`, `tests/codemie/rest_api/routers/test_workflow_versions.py`
- Run `disable_dead_links_in_yaml(target.yaml_config)` before any mutation (editor error aborts, `yaml_config` untouched), assign the edited text, then the existing `parse_execution_config` / `_validate_restored_workflow` flow (derived `assistants`/`tools` come from edited YAML). `yaml_config_history` gets the usual single new entry. Call `after_workflow_links_changed(workflow)` after `workflow.update`. Return the report; router response model becomes `DisabledMcpResponse` (items carry node address). Config delete is untouched (AC 9).
Test-first: yes — rollback of a revision referencing a deleted config succeeds with edited YAML stored, derived columns re-derived, history entry only the prior config, report has addresses; editor failure leaves workflow untouched; OpenAPI snapshot test passes after baseline regeneration.

### Task 7: Preconfigured assistants and workflows
**Files:** `src/external/deployment_scripts/preconfigured_assistants.py:155,233`; `preconfigured_workflows.py:59-85`; tests under `tests/` for deployment scripts
- Assistants: apply the helper (`strict_lookup=False`) before `sanitize_for_save`; on an all-missing non-empty id set skip the helper so `sanitize_for_save` raises as today. Leave `adjust_usage` calls (:193, :267).
- Workflows: apply `disable_dead_links_in_yaml(template.yaml_config, strict_lookup=True)` after `patch_template_with_real_assistant_ids` and before `WorkflowConfig(**template.model_dump())`, re-deriving `assistants`/`tools` from the edited text; log disabled count with template name at warning; on `CatalogLookupError` save as written and log error with template name. Call `after_workflow_links_changed` after `save`.
Test-first: yes — template with inactive config saved disabled and warning logged; template with one missing id among several saved as written and error logged; empty `get_by_ids` result for a non-empty id set skips helper (workflow saved as written; assistant still raises); `get_by_ids` raising skips the helper.

### Task 8: Close validation gaps
**Files:** `routers/assistant/_sub_assistants.py:136-137`; `service/workflow_generator_service.py:109-116`; tests next to existing sub-assistant and generator tests
- `_apply_sub_assistant_settings` runs `MCPAccessControlService.sanitize_for_save` on the `mcp_servers` it writes. Generator calls `validate_workflow_mcp` on the generated workflow before `create_workflow`. Moderation code untouched.
Test-first: yes — sub-assistant publish with a dead enabled catalog link or duplicate links is refused, with a disabled leftover it passes, marketplace moderation reads unchanged; generator persist with an unavailable catalog MCP raises, valid one persists.

### Task 9: Runtime guard test (no source change expected)
**Files:** tests only, `tests/.../test_mcp_toolkit_service.py` (or the existing file for `service/mcp/toolkit_service.py`)
- Confirm the workflow agent-node path (`utils.py:541` `build_agent_for_workflow` -> `ToolkitService.get_tools` -> `get_mcp_server_tools`) reaches the skip at `mcp/toolkit_service.py:299`; if the path does not reach it, record that in the digest rather than change runtime.
Test-first: no — characterization test: a disabled MCP entry in a workflow assistant node produces no tools and no error; disabled tool node still raises `MCP server '<name>' is disabled`.

---

## Negative-constraint pass
- No link tables, sync, filters, delete guard: only the empty seam (Tasks 6, 7) honors this.
- No usage-count bookkeeping: Tasks 4, 6 add no `adjust_usage`; Task 7 leaves existing calls.
- No replacement of other link-rule copies: Tasks 1-8 touch none of `management.py:582-603`, `tools/toolkit_service.py:1581`, `preconfigured_assistants.py:181,182,265`, `resolve_catalog_config`.
- No YAML rewrite on config delete: not touched (Task 6 note).
- Generic catalog refusal message unchanged: Task 1 keeps messages, only reasons in log.
- No marketplace moderation, import/export, clone, template changes: Task 8 test asserts moderation unchanged.
- No ruamel, no `safe_dump` round trip: Task 5 uses `yaml.compose` and text edits.
- No UI work; `strip_inline_config`, runtime resolution and enabled-entry restricted rules unchanged: Tasks 1, 9.
- Editor failure aborts rollback rather than writing unedited YAML: Tasks 5, 6.
