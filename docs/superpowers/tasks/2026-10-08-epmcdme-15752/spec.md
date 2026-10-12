# Spec: step scope and integration alias for script tool calls (EPMCDME-15752)

> **Superseded in part (2026-10-11).** The field is now called `tool_access` (it was `script_tool_access`). The alias model below was changed to match the existing assistant tool selection: `script_tool_access` is a list of `{name, integration_alias?}` (the `WorkflowAssistantTool` shape), one entry per tool, an alias pins the integration and no alias means the automatic lookup. The script no longer passes `integration_alias` (the runtime SDK, the call protocol, the authorizer and the policy select nothing by alias), and a tool cannot be listed with several aliases. Everything below about a per-call alias, several permitted aliases and `integration_aliases` is historical; the other parts (deny by default, owner mode, save-time checks, warnings, catalog and schema contract) still hold. Same for `plan.md` and the review artifacts in this folder.

Sub-task of EPMCDME-15568 (epic spec `docs/stories/2026-09-28-custom-tools/architecture-spec.md`: section 7.3 line 570, D19, D21, D28). Backend only (repository codemie). Size L. Revised after architecture review (`local/architecture-review/2026-10-08-epmcdme-15752-spec/`).

## Goal

The author of a workflow script step lists which tools its script may call and, per tool, which integration aliases it may use. The script may pass an optional `integration_alias` per call only as a selector among the listed aliases. A step without a policy calls no tool.

## Behavior

### Call protocol (additive, `v` stays 1, no new error codes)
- Payload `tool.call` gains optional `integration_alias` (non-empty string, else `bad_arguments`). `handler.py` `_PAYLOAD_FIELDS` / `_parse_payload` accept it; the "deferred" comment goes.
- The handler builds one `ToolCallSelector(name, integration_alias)` and passes it to `authorize_tool_call(context, selector)`, `ScriptScope.resolve(selector)` and `WorkflowToolResolver` / `resolve_workflow_tool`. Selectors and results are single values so a later field does not change every signature again.
- Scopes return one `ResolvedTool(tool, alias, runs_as_owner)` (no longer a bare tool); the authorizer still applies the exclusion function to `.tool`.
- SDK `call_tool(name, args=None, *, integration_alias=None, timeout=None)`; `call_tools` items already forward extra keys, add key-type validation. The SDK reference markdown and the script tool description document it.

### Policy (one value, one decision point)
- `ScriptStepPolicy` is a frozen pydantic `BaseModel` (not a dataclass; see plumbing) built from the step: per tool an allowed alias list, plus an explicit `owner_mode` of `none` (workflow not global), `creator` (global, with `owner_user_id` set) or `unknown_creator` (global, `created_by` missing). `owner_user_id` alone cannot tell the last two cases from `none`, so the mode is stored, not inferred. Its single method `select(name, alias) -> PolicyDecision` is the only place that applies the rules and is called by `ProjectScope.resolve` before any tool is built and outside the try/except. An absent policy means deny: `tool_unavailable` for every name.
- `callable_tools()` derives from the same policy and returns the listed tool names with their allowed aliases (not built instances), filtered by the exclusion prefilter; generator and test run use it.
- Rules in `select`:
  - tool not in policy: `tool_unavailable`.
  - listed with no aliases: running user's automatic lookup in the workflow's project; an alias in the call: `bad_arguments`.
  - listed with aliases: call alias must be listed else `bad_arguments` naming the permitted aliases; none passed with exactly one listed uses it; with several: `bad_arguments`.
- Owner rule, per tool, by `owner_mode`: `creator` means a listed alias runs under the creator's account (`runs_as_owner=True`); `unknown_creator` means a listed alias is `bad_arguments`, never a fallback to the running user; `none` means a listed alias resolves among the running user's integrations in the workflow's project, and if they have none, `bad_arguments` with a clear message.
- Assistant scope (`ToolListScope`, chat and workflow assistant step): a selector carrying an alias is `bad_arguments`, tool not run; without alias unchanged. `NoScope` still fails closed.

### Error mapping
In `ProjectScope.resolve` (`context.py` around lines 100-105) the first clause of the except chain is `except ToolCallRefused: raise`; only then the existing `ValueError` / `Exception` -> `None` swallowing (-> `tool_unavailable`). A dedicated exception for "no integration of that alias" is raised from the settings lookup path and mapped to `ToolCallRefused(CODE_BAD_ARGUMENTS)` by the resolver. It subclasses `ValueError`, because other `find_setting_for_tool` callers catch `ValueError`. Codes use the named constants (`test_error_code_contract.py`).

### Plumbing step -> scope and the alias/owner build path
- `VirtualAssistant` (`rest_api/models/assistant.py`, non-persisted) gets `script_step_policy`, set in `VirtualAssistantService.create_from_tool_config` from the `WorkflowTool` field and `workflow_config.is_global` / `created_by` (which fix `owner_mode`). It is set for every tool step, not only script steps (an empty policy when there is no map); harmless for ordinary tools, which never read it. One field carries the fact: `is_tool_step` becomes a derived property (`script_step_policy is not None`) and is no longer stored.
- Trap: today `VirtualAssistant` is kept as an explicit union member in the toolkit's assistant type (comment near `toolkit.py:75`), so no conversion drops the field. If `VirtualAssistant` is removed from that union or the field's type changes, the policy is lost, `is_tool_step` turns False and the script silently gets a different scope. So the policy type must stay pydantic-compatible (frozen `BaseModel`), and a test must show the policy reaches `ScriptToolRegistry` through `AgentWorkspaceToolkit`. `AgentWorkspaceToolkit` passes it to `ScriptToolRegistry`, whose `context()` builds `ProjectScope(policy)`.
- The real build path goes through `ToolsService.find_tool_by_invoke_request` (`tool_service.py:119`), which today calls `get_toolkit(tool_name, user, project)` and `get_core_tools(..., user_id=user.id, ...)` with no alias and no owner. This function, `resolve_workflow_tool`, `VirtualAssistantService.create_from_tool_invocation` and `get_toolkit` must carry the alias and `owner_user_id` so the tool is built with the listed integration (creator's when `runs_as_owner`). Merged code of EPMCDME-15559 may be changed for this.
- Required test: build a tool with an alias (global workflow, creator-only integration and a mismatched-type case) and inspect the credentials the built tool actually holds, not just the resolver arguments.

### Model and storage
- New optional field on `WorkflowTool`: `script_tool_access: Optional[list[ScriptToolAccess]] = None`, where `ScriptToolAccess` is `{name: str, integration_aliases: list[str] = []}`. It is a related new shape: assistant tool entries carry a single `integration_alias`, here the field is a list. Stored in the workflow config like all `WorkflowTool` fields; default None, no migration. Duplicate names are refused.
- Save-time validation in `workflows/validation/resources.py`, for `execute_workspace_script` steps only (a map elsewhere is refused, naming the step):
  - tool exists and is script-callable (reuse the exclusion/callable check; not a second list).
  - each alias exists for the creator (`created_by` of the workflow), is in the workflow's project, and fits the tool's credential type. `_is_integration_alias_valid(user, ...)` (`resources.py:364`) takes a `User`; for the creator it gets a path through `owner_user_id` in `find_setting_for_tool`. Decision: validate against `created_by`; map edits are not restricted to the creator. For a non-global workflow, save validates the alias against the creator while the run looks it up among the running user's integrations, so save is only a plausibility check there.
  - Credential type: a real check is implemented. `CredentialValidator.validate_tool_credentials` does not compare types, so a new helper compares the setting's `credential_type` (a `CredentialTypes` enum) with the tool's expected type from `ToolMetadataService.get_credential_type(config_class)` (a `str`), by value (`.value`). A tool that takes no credentials accepts no alias. If the expected type cannot be derived, the check is skipped with a logged warning, and the runtime build test above still guards it.
  - Failures refuse the save, naming tool and alias.

### Publish warning
- `WorkflowPublishValidationResponse` (`rest_api/models/workflow_marketplace.py`) gets a `warnings` list of items with a `kind` field. Existing consumer-slot warnings keep their current shape and gain `kind="consumer_slot"` as an additive field; the new `kind="script_step_owner_integration"` item names the step, tool and aliases and says the tool runs under the author's integration for everyone who runs the workflow.
- New collector next to `collect_consumer_slot_integration_warnings`. It takes a `treat_as_global` parameter (the proposed state): at publish `_apply_publish` flips `is_global` only later, so reading the stored flag would return nothing on the very transition the warning is for. Save and publish callers pass the proposed value. Wired into the router save/publish paths with the same catch-all (advisory, never blocks).

### Tests and docs to update
- Rewrite assertions that the workflow scope's `callable_tools()` is `None` (`test_workflow_tool_step_scope.py:124`, `test_toolkit_script_registry.py:159`), `test_context.py:156`, and tests asserting the resolver's old signature/return (`WorkflowToolResolver` fakes in the script_tool_calls tests).
- `test_sdk_reference.py`: the two-example assertion changes with the new alias example; the SDK reference shown to assistants must say an alias is refused there.
- New tests per acceptance criterion, including: unlisted alias from script text never reaches the creator; global workflow without `created_by` (`owner_mode=unknown_creator`) gives `bad_arguments` for a listed alias and does not reach the running user's integrations; a non-global workflow (`owner_mode=none`) still resolves the listed alias among the running user's; absent policy denies; the collector with `treat_as_global=True` on a stored non-global workflow; the policy reaching `ScriptToolRegistry` through `AgentWorkspaceToolkit`.
- Docs: `docs/workspace-script-sdk.md` and `src/codemie_tools/data_management/code_executor/README.md` (alias parameter, step policy, accepted risk for assistants, spec 7.5). Epic spec 7.5 gets two line edits: the residual risk below, and the PROJECT-settings row now says the reach is closed only for tools outside the list; a listed tool with no aliases still reaches PROJECT-level settings through the automatic lookup, with no access check.

## Acceptance criteria
Those of EPMCDME-15568 in `ticket.md` (backend-testable ones), each covered by a test; the editor criterion belongs to codemie-ui. "Alias exists for the author" means the workflow's creator. A global workflow with no known creator refuses a listed alias with `bad_arguments`. The publish warning is produced from the proposed global state at publish and save-as-global.

## Non-goals
- No UI (codemie-ui editor and warning display); the UI consumes `warnings[].kind` separately.
- No new error codes, no protocol version bump.
- No alias for assistant tools; assistants only refuse an alias.
- No access check on PROJECT-type settings in other contexts; no owner control for assistants.
- No per-run call cap; no change to the exclusion list or `script_callable` flags.
- No breaking change to the existing assistant warning shape (only an added `kind`).
- No DB migration; no restriction of map edits to the creator.

## Risks
- Open: tests must prove an unlisted alias never reaches the creator's account and that the built tool holds the credentials claimed.
- Accepted: any editor of a workflow can bind the creator's alias to it; existence is validated against the creator, the save error reveals which aliases the creator lacks, and the warning goes to the publisher, not the account owner.
- Accepted: a listed tool with no aliases still reaches PROJECT-level settings without an access check.
- Accepted: each call builds its tool without a deadline, and calls under the creator's account share one token limit.
- Accepted: no upper bound on the size of the map.
- Accepted: the alias is bound by name, not by id.
- Accepted: no check that the creator is still active or still in the project at run time; stored configs that bypass save validation get only the run-time `select()` check.
- Accepted: the runner writes the script; the map bounds tool and alias, not arguments.
- Decided: the `bad_arguments` text lists the creator's permitted aliases to any caller, as the acceptance criteria require.
- The `warnings` field and `kind` are a contract addition for codemie-ui.
- Signature changes (selector / ResolvedTool / owner id) touch merged 15559 code.
