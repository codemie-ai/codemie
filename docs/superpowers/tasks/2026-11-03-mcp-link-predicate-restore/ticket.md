# EPMCDME-16489 — Subtask A: [MCP] One link predicate, links-only save validation, restore with "disable and report", close validation gaps

Parent: EPMCDME-15629 (Story: [MCP Management] MCP servers as rows with a catalog FK: one mechanism for MCP usage by Assistants, Skills and Workflows). Related: unmerged branch `EPMCDME-15629_mcp-assets-count`, MR codemie!4439 (notes 2488108, 2489792, 2500152, 2500360, 2502118). Downstream: EPMCDME-16490, EPMCDME-16640.

## Description

Define what counts as an MCP link in one place and make every save, restore and rollback path follow it. No tables are created here; this work stands on its own and fixes bugs that exist today.

**Link predicate:** an `mcp_servers` entry is a link when it is `enabled` (missing means true) AND has `mcp_config_id` AND is NOT `use_custom_config`. One function, used by `validate_on_save` now and by the table sync, counts, filters and delete guard later.

**Problems fixed:**
1. `MCPAccessControlService.validate_on_save` (`src/codemie/service/mcp/access_control.py:152`) checks the catalog for every entry that has an `mcp_config_id`, even disabled or custom-config ones. After a config is deleted, a leftover id makes the next save of an assistant, skill or workflow (and a workflow rollback) fail with "MCP server not available in the catalogue".
2. The duplicate check (`access_control.py:160-168`) runs over the whole workflow: `collect_workflow_mcp_servers` flattens all nodes, so using one catalog MCP in two nodes is refused. 12 production workflows do this (23 duplicate entries); they probably cannot be saved or rolled back today (to confirm on dev).
3. Restore paths bring stored data back without checking the catalog: `validate_on_restore` (`access_control.py:174`) only checks restricted mode. Failure shows on the following save.
4. Two paths write MCP data with no catalog check: marketplace publish of sub-assistants (line 136 of `_sub_assistants.py`) and the workflow generator with `persist=True` (`workflow_generator_service.py:111` -> `create_workflow`, bypassing router check at `routers/workflow.py:521`).

**Restore rule ("disable and report", same for all asset types):** when an operation brings stored data into an asset and that data has an enabled link to a catalog config that is missing, inactive or not public, the link is set to `enabled: false`, the operation succeeds, and the response lists the disabled servers so the UI can show them. A disabled tool node fails at runtime with the existing clear error (`MCP server 'X' is disabled`, `tool_node.py:130`) and an agent node skips the server. The entry stays visible in the editor.

**Workflow YAML:** comments and formatting must be preserved. No `yaml.safe_dump` round trip and no new dependency. The helper uses `yaml.compose()` nodes (`start_mark` / `end_mark`) to find the entry by `mcp_config_id` and edits only that spot: replace the `enabled: true` scalar; insert `enabled: false` at the mapping's indentation when `enabled` is missing; handle flow style (inline mapping in curly braces with `name` and `mcp_config_id` keys) and anchors (edit the anchor definition for `<<: *mcp`). `yaml_config_history` is left untouched, because each rollback applies the rule to the restored text again.

## Preconditions
* EPMCDME-15629 plan agreed (MR codemie!4439 notes above).
* No dependency on other sub-tasks.

## Steps to Reproduce (current behaviour)
1. Workflow using one catalog MCP in two nodes: save refused with "The same MCP server from the catalogue was added more than once".
2. Leave a disabled MCP entry with a catalog id in an assistant, delete/deactivate that config, save the assistant: fails with "MCP server not available in the catalogue".
3. Roll back an assistant or workflow to a version with an enabled link to a deleted config: id restored (assistant) or rollback fails (workflow); next assistant save fails.
4. Publish an assistant with sub-assistants to the marketplace with an `mcp_servers` payload referencing an unavailable config: nothing checks it.
5. Generate a workflow with `persist=True` referencing an unavailable config: nothing checks it.

## Expected Result
* A leftover disabled or custom-config entry never blocks a save, rollback or import.
* One catalog MCP can be used in several workflow nodes; a duplicate inside one node is still refused (assistants and skills keep the check across their list).
* Restore and rollback succeed and report the servers they disabled; workflow YAML keeps comments and formatting.
* Both gap paths run the normal save validation.

## Affected Areas
* Backend (codemie): `MCPAccessControlService` (`validate_on_save`, `sanitize_for_save`, `validate_on_restore`, `validate_and_strip_workflow_mcp_servers`, `collect_workflow_mcp_servers`), `WorkflowService.validate_for_update`, `AssistantVersionService.rollback_to_version` (`assistant_version_service.py:267`), `workflow_version_service.py:108`, `preconfigured_assistants.py:155,233`, `preconfigured_workflows.py:59-85`, the sub-assistants helper (line 136), `workflow_generator_service.py:111`, rollback response models.
* Checked, not affected: marketplace moderation (approve/reject/queue) only reads `mcp_servers`; skill import/export and preconfigured skills carry no MCP links; skills have no versions; clone and workflow templates go through normal create; runtime-only paths already skip a missing config.
* API contract: rollback responses (assistant and workflow) gain additive field `disabled_mcp_servers`. OpenAPI baseline.

## Acceptance Criteria

**Link predicate**
1. One function defines the link rule (`enabled` AND `mcp_config_id` AND NOT `use_custom_config`, missing `enabled` = true). `validate_on_save` uses it; no other copy of the rule is added.

**Save validation**
2. `validate_on_save` checks catalog availability (exists, active, public) only for entries that are links. Disabled and custom-config entries with any id never fail a save, rollback or import, for assistants, skills and workflows.
3. Re-enabling an entry whose config is missing still fails with the existing "not available in the catalogue" error.
4. Duplicate check for workflows runs per node. Same catalog MCP in two nodes allowed; twice inside one node still refused. Assistants and skills keep the duplicate check across their list.

**Restore and rollback: disable and report**
5. One shared helper in `MCPAccessControlService` next to `validate_on_restore`: for an enabled link to a missing, inactive or non-public config it sets `enabled: false` and returns the list of disabled servers (`name`, `mcp_config_id`, `reason`: `missing`, `inactive` or `not_public`). Used by every path below.
6. Assistant version rollback, workflow rollback and import-like restores call the helper, succeed, and return the disabled servers in the response. Additive field `disabled_mcp_servers` (empty list when nothing disabled), documented in the OpenAPI baseline.
7. Preconfigured assistants and workflows apply the same helper and log the disabled servers instead of raising at startup.
8. Workflow YAML is edited in place by position: existing `enabled: true` replaced, missing `enabled` gets a new `enabled: false` line at right indentation, flow-style and anchored mappings handled. Comments and formatting elsewhere byte-for-byte unchanged. `yaml_config_history` not modified. Derived `assistants` / `tools` columns re-derived from the edited YAML.
9. A config delete does not rewrite any workflow YAML.

**Gaps**
10. Marketplace publish of sub-assistants runs the normal `validate_on_save` on the `mcp_servers` it writes.
11. The workflow generator with `persist=True` runs the same validation as the router before saving.

**Tests**
12. Tests cover: predicate (enabled / missing enabled / custom / no id); leftover ids not blocking saves; per-node and in-node duplicates; each restore path (assistant, workflow, preconfigured assistants and workflows) including reported list; YAML editor (block and flow style, anchors, missing key, comments preserved, history untouched); both gap paths. Edge: marketplace moderation approve/reject leaves MCP data unchanged.
13. Confirm on dev that a workflow with one catalog MCP in two nodes was refused before the change and is accepted after it.
