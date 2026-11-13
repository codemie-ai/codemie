# Spec: EPMCDME-16489 - MCP link predicate, links-only save validation, disable-and-report restore

Source of truth for requirements: `ticket.md` (AC 1-13). This spec records scope, behavior and decisions; it does not repeat the ticket.

## Goal

Define "MCP link" once in `MCPAccessControlService` and make every save, restore and rollback path follow it, so a leftover disabled/custom entry never blocks a save (open or restricted mode), a catalog MCP can be used in several workflow nodes, and restores succeed by disabling (and reporting) unavailable links. No tables, counts or UI.

## Behavior

- **Predicate**: one module-level function in `src/codemie/service/mcp/access_control.py`: link = `enabled` (missing = true) AND `mcp_config_id` AND NOT `use_custom_config`. Exported, with the per-node duplicate check below, for reuse by EPMCDME-16490 and 16640. No new copy of the rule is added. Existing copies, left in place for 16490: `_restricted_violations`, `filter_for_runtime`, `resolve_catalog_config`, `management.py:582-603` and `toolkit_service.py:1581`.
- **Save** (`validate_on_save`, :153): the catalog check (exists, active, public) and the duplicate check (:161-169) run only over links; disabled and custom entries are never counted. Refusal messages stay the existing generic ones. Re-enabling an entry with a dead config still fails (AC 3). The test `tests/unit/service/mcp/test_access_control.py:131` asserts the old behavior and is rewritten.
- **Restricted mode**: `_validate_restricted_mode` (:101) and `validate_on_restore` (:174) run only over enabled entries (`enabled` missing = true); disabled entries are exempt on save and rollback, re-enabling is checked. Enabled custom servers and enabled catalog entries with a custom configuration are refused as today.
- **Duplicates**: counted over links only. Assistants and skills check across their list. Workflows check per node (each assistant's `mcp_servers`, each tool's single `mcp_server`). `collect_workflow_mcp_servers` (:207) is flat today, so a node-scoped collector returns each server with its address (`assistants[i].mcp_servers[j]` or `tools[k].mcp_server`). Router (`routers/workflow.py:521`) and `WorkflowService.validate_for_update` (:260) use it.
- **Restore helper** next to `validate_on_restore`: takes servers (with addresses for workflows), returns `(new_servers, report)`. Inputs are never mutated; copies are returned. For each link whose config is missing, inactive or not public, the copy has `enabled=false` and the report gets `{name, mcp_config_id, reason, address?}` (`missing|inactive|not_public`; address only for workflows). Non-links and available links are untouched. One `get_by_ids` per call.
- **Assistant rollback** (`assistant_version_service.py:267,295`): the helper runs on `target_config.mcp_servers`; its copy goes into the new configuration and the assistant. The loaded target version is never written (today the same list object is passed). Tests assert the stored version is unchanged. No usage-count call is added (see Non-goals).
- **Workflow rollback** (`workflow_version_service.py:43-108`): the target YAML is held as local text; the helper identifies dead links by address; the text is edited in place (see Editor); only then assigned to `workflow.yaml_config` and parsed once with `parse_execution_config()`, which re-derives `assistants`/`tools`; then `_validate_restored_workflow`. An editor failure happens before any mutation. `yaml_config_history` gets the usual new entry only. A config delete never rewrites workflow YAML (AC 9).
- **Post-edit hook (design constraint)** - **superseded by plan AR-14**: the hook is the empty seam `after_workflow_links_changed(workflow)` in `access_control.py`, called after persistence (rollback after `update`, preconfigured workflows after `save`, always), not a sync inside `disable_dead_links_in_yaml`, which stays pure. Create, update and the generator do not call the seam; EPMCDME-16640 covers those paths. Original wording: EPMCDME-16640 re-syncs `workflow_mcp_servers` "at the end of the restore helper". The workflow restore path therefore ends in one clearly named function, `disable_dead_links_in_yaml(yaml_text) -> (new_text, report)`, called by the rollback and by preconfigured workflows, whose return point is the single place after the edit where 16640 adds its sync. No table work here.
- **Responses**: both rollback endpoints (`routers/assistant/versions.py:161`, `routers/workflow.py:983-999`) return a model extending `BaseResponse` with additive `disabled_mcp_servers: list[...]` (empty when none; workflow items include the node address). OpenAPI snapshot baseline regenerated.
- **Preconfigured**: assistants (`preconfigured_assistants.py:155,233`) apply the helper before `sanitize_for_save`; their existing `adjust_usage` calls (:193, :267) are left as is. Workflows (`preconfigured_workflows.py:59-85`) have no MCP validation today; the helper plus editor is applied to the template text (new behavior, no raise). Disabled servers are logged at warning with the template name and count. A lookup is a failure, and the helper is skipped for that template (template saved as written, error logged with its name), when `get_by_ids` raises or when a non-empty id set returns no rows at all (`base.py:407-410` returns only existing rows, so an unpopulated catalog otherwise looks like removed configs); partial misses still disable and log. Preconfigured workflows are create-once (`preconfigured_workflows.py:68-70`), so a template saved disabled is never healed: this skip rule is what prevents that. For assistants a DB outage still fails startup as today (`sanitize_for_save` runs the same query). Assistants use the same strict rule (`restore_mcp_servers(..., strict_lookup=True)`), so on a fresh environment an assistant template whose links are all missing is left as written and can still be refused by the normal save validation: this narrows AC 7 for that case, because "not seeded yet" cannot be told apart from "deleted" without a separate catalogue signal.
- **Gaps**: `_apply_sub_assistant_settings` (`_sub_assistants.py:136-137`) runs `sanitize_for_save` on the `mcp_servers` it writes; `workflow_generator_service.py:109-116` runs the router's validation before `create_workflow`. Marketplace moderation is unchanged (verified by test).
- **Runtime**: unchanged. Disabled tool nodes already raise `MCP server '<name>' is disabled` (`tool_node.py:131-132`); the agent-node skip is asserted in the ticket (assistant path reaches `toolkit_service.py:299`); planning confirms workflow agent nodes reach it and adds a test.

## Workflow YAML in-place editor

New small module in the service layer (PyYAML only, `yaml.compose` nodes with `start_mark`/`end_mark`, precedent `workflows/validation/line_lookup.py:67`; no router or deployment_scripts imports). It takes addresses from the helper report and holds no link predicate and no lookup by `mcp_config_id`. Every reported address is edited, so one dead id in several nodes is fully disabled, and a custom-config or already disabled entry with the same id is never touched. At each address:
- `enabled: true` scalar replaced by `false`;
- missing `enabled`: a new `enabled: false` line at the mapping's indentation;
- flow-style mapping: key inserted inline;
- anchored mapping via `<<: *mcp`: if the alias sets `enabled` or `mcp_config_id`, the alias is edited; otherwise the anchor definition is edited and the report lists every node reached through it.
Everything else is byte-identical. Confirmation re-parses the edited text and compares the full set of (address, enabled) before and after: every reported address must be disabled and no unreported address may change. If an unreported address would change (an alias differing only by `use_custom_config` or its own `enabled` shares the anchor), the alias-override path is used; if it cannot be, or the editor cannot place an edit, the rollback fails with a clear error rather than writing a partial result.

## Acceptance criteria

As in `ticket.md` AC 1-13: predicate single-sourced; availability and duplicates checked only for links; per-node workflow duplicates; shared helper with reason and address; both rollbacks and preconfigured paths use it and report; byte-preserving YAML edit with history untouched and derived columns re-derived; both gap paths validate; tests as listed in AC 12, plus:
- same dead id in two nodes and a custom-config entry with that id (only the links disabled and reported);
- anchored case where a shared anchor would flip an unreported node (alias-override or failure, nothing unreported changes);
- restricted-mode disabled-entry exemption (disabled custom entry saves, same entry enabled is refused);
- disabled plus enabled entries with the same id save, two enabled links are refused (assistant, skill, workflow node);
- assistant stored version unchanged after rollback;
- preconfigured template with a non-empty id set returning no rows: helper skipped, error logged.
AC 13 is a manual dev confirmation outside the code change; it also checks production workflows for anchored MCP entries.

## Non-goals

- No MCP link tables, sync, filters or delete guard (EPMCDME-16490, 16640); only the hook point above.
- No usage-count bookkeeping: rollback and disable do not call `adjust_usage` for assistants or workflows. The legacy `usage_count` stays stale after rollback/disable until 16490 deletes it (16490 steps-to-reproduce item 3 lists this drift as fixed there; 16640 removes the API field). The preconfigured assistants `adjust_usage` calls stay as is.
- No UI for `disabled_mcp_servers`. No ticket owns it yet; until one does, both rollback endpoints append the disabled server names to `message`.
- No replacement of the other live copies of the link rule.
- No workflow YAML rewrite on config delete.
- No change to the generic catalog refusal message on save.
- No change to marketplace moderation, skill import/export, clone or templates.
- No new YAML dependency (no ruamel), no `yaml.safe_dump` round trip.
- No UI work (no ticket named as owner of displaying `disabled_mcp_servers`).
- No change to `strip_inline_config`, runtime resolution, or restricted-mode rules for enabled entries.

## Decisions

- Disabled entries exempt from restricted-mode checks on save and rollback over checking every entry — user, answers AR-3.
- Rollback and disable leave the legacy `usage_count` untouched over adjusting it by link diff — user, answers AR-7 (16490 drops `usage_count` and all `adjust_usage` bookkeeping).
- Duplicate check counts links only over every entry with an id: a disabled leftover must not block a save.
- Exposing `reason` (missing/inactive/not_public) in restore reports over an opaque message: the ticket requires it; save refusals stay generic.
- Editor takes helper-reported node addresses over locating entries by `mcp_config_id`: the id is not unique within a workflow once cross-node duplicates are allowed.
- Editor confirms that only reported addresses changed over confirming reported ones are disabled: shared anchors can flip other nodes.
- Restore helper returns copies and a report over mutating in place: the stored version must stay unchanged.
- Node-scoped collector for workflow duplicate checks over per-call grouping in the router: router and service both need it.
- Helper plus editor applied to preconfigured workflows (log only) over leaving them unvalidated: AC 7 requires one rule and the YAML is the source of truth.
- All-missing non-empty id set treated as lookup failure (helper skipped, error logged) over disabling every link: an unpopulated catalog must not freeze shipped templates disabled (AR-11).
- Workflow restore ends in one named function with a single return point over inline steps: gives 16640 its re-sync hook without table work here.
- Other live copies of the link rule left for 16490 over editing them here: they serve counts.
- "Import-like restores" taken as the two rollbacks plus preconfigured assistants/workflows over inventing entry points: grep of `src` shows no other restore-style callers.
- Anchor edits modify the anchor definition unless the alias overrides the field, over expanding the alias: ticket-specified.
- Editor failure aborts the rollback over silently writing unedited YAML.

## Risks / assumptions

- YAML editor edge cases (flow, anchors, merge keys, CRLF, multi-document, tabs) have no repo precedent; covered by dedicated tests.
- Disabling via an anchor disables every alias sharing it (reported per node).
- Legacy `usage_count` is stale after rollback/disable until 16490 deletes it.
- Fresh environments with an unpopulated catalog: preconfigured templates are saved as written (error logged); planning checks seed order (`main.py:544-553` has no catalog seeding).
- Agent-node skip of disabled servers on the workflow path is assumed, to be confirmed in planning.
- `MCPConfig.get_by_ids` returns only existing rows, so a partial result is indistinguishable from removed configs.
- Branch `EPMCDME-15629_mcp-assets-count` was not inspected; reuse of its predicate is for planning to check.
