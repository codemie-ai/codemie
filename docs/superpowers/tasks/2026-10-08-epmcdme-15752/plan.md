# Script step scope and integration alias (EPMCDME-15752) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** A workflow script step lists the tools its script may call and per tool the integration aliases; the script's optional `integration_alias` is only a selector among them.

**Architecture:** One frozen pydantic `ScriptStepPolicy` (single `select()` decision point) is built from the step in `VirtualAssistantService.create_from_tool_config`, carried on `VirtualAssistant`, handed by `AgentWorkspaceToolkit` to `ScriptToolRegistry`, and enforced by `ProjectScope`. Handler passes one `ToolCallSelector`; scopes return `ResolvedTool`.

**Tech Stack:** Python, pydantic, pytest, FastAPI.

Commit per task using the repository's existing convention. Spec: `spec.md` in this directory (section names below refer to it). Paths are under `src/codemie/service/script_tool_calls/` unless stated. Tests are run with `poetry run pytest <path> -v`. Whole-suite gates are out of scope.

negative-constraints (honored): no new error codes (named `CODE_*` constants only, Task 3); `v` stays 1 (Task 2/6); no alias for assistant tools, assistants only refuse (Task 3); no DB migration, default None (Task 1); existing assistant warning shape unchanged, only `kind` added (Task 8); map edits not restricted to creator (Task 7); no exclusion list or `script_callable` change; `unknown_creator` never falls back to the running user (Tasks 1, 3); absent policy denies (Task 1); no UI.

---

### Task 1: Model field and `ScriptStepPolicy`

**Files:**
- Modify: `src/codemie/core/workflow_models/workflow_models.py:99` (`WorkflowTool`)
- Create: `src/codemie/service/script_tool_calls/policy.py`
- Test: `tests/codemie/service/script_tool_calls/test_policy.py`

Test-first: yes — `select()` table test: unlisted tool -> tool_unavailable; no aliases + alias given -> bad_arguments; listed [a,b] with c -> bad_arguments naming a and b; none with two -> bad_arguments; none with single [a] -> a; `unknown_creator` + listed alias -> bad_arguments; `creator` -> `runs_as_owner=True`; `none` -> False; `policy=None` denies.

- [ ] Add `ScriptToolAccess(name: str, integration_aliases: list[str] = [])` and `WorkflowTool.script_tool_access: Optional[list[ScriptToolAccess]] = None` (refuse duplicate names in a validator).
- [ ] Create `ToolCallSelector(name, integration_alias=None)` and `ResolvedTool(tool, alias, runs_as_owner)` (frozen dataclasses) in `policy.py`, plus the new symbols below:

```python
class ScriptStepPolicy(BaseModel, frozen=True):
    allowed: dict[str, tuple[str, ...]] = {}
    owner_mode: Literal["none", "creator", "unknown_creator"] = "none"
    owner_user_id: str | None = None
    def select(self, name: str, alias: str | None) -> "PolicyDecision": ...
    def listed(self) -> dict[str, tuple[str, ...]]: ...
```

`PolicyDecision(alias: str | None, runs_as_owner: bool)`; refusals raise `ToolCallRefused` with `CODE_TOOL_UNAVAILABLE` / `CODE_BAD_ARGUMENTS` (find the exception and constants in `handler.py`/`authorizer.py`). Implement the rules in spec "Policy". A module-level helper `deny_all_decision(name)` handles an absent policy.
- [ ] Run the new test; expect PASS.

### Task 2: Selector through payload, authorizer and handler

**Files:**
- Modify: `handler.py` (`_PAYLOAD_FIELDS` ~49, `_parse_payload`, `_handle_tool_call`), `authorizer.py` (`authorize_tool_call`), `context.py` (`ScriptScope.resolve`, `WorkflowToolResolver`)
- Test: `tests/codemie/service/script_tool_calls/test_handler.py`, `test_authorizer.py`, `test_error_code_contract.py`

Test-first: yes — payload with `integration_alias: ""` or a non-string gets `bad_arguments`; a valid alias reaches `scope.resolve` as `ToolCallSelector`; authorizer applies the exclusion function to `ResolvedTool.tool` and still blocks the script tool by name; contract test still forbids string-literal codes.

- [ ] Accept `integration_alias` in `_PAYLOAD_FIELDS`/`_parse_payload` (non-empty string), delete the "deferred" comment, return a `ToolCallSelector`.
- [ ] `authorize_tool_call(context, selector) -> ResolvedTool`; `ScriptScope.resolve(selector) -> ResolvedTool | None`; `WorkflowToolResolver = Callable[[User, str, ToolCallSelector, ...], ResolvedTool]` as settled in Task 4. Update handler to use `.tool` for the `ScriptCallable`/run steps.
- [ ] Update fakes in existing script_tool_calls tests to the new signature/return.

### Task 3: Scopes and error mapping

**Files:**
- Modify: `context.py` (`NoScope`, `ToolListScope`, `ProjectScope` ~100-105, `ScriptToolRegistry.context()`)
- Test: `test_context.py` (line ~156), `test_workflow_tool_step_scope.py` (lines ~124, ~204)

Test-first: yes — `ProjectScope(policy).resolve` for an unlisted tool raises tool_unavailable without calling the resolver; a `bad_arguments` from `select()` or the resolver is not swallowed into tool_unavailable while other exceptions still are; `ToolListScope.resolve` with an alias raises `bad_arguments` and without alias is unchanged; `NoScope` fails closed; `callable_tools()` of `ProjectScope` returns listed names with aliases, filtered by `is_known_excluded`, not `None`.

- [ ] `ProjectScope(policy, user, project, resolver)`: call `policy.select` before any build and outside try/except; except chain starts with `except ToolCallRefused: raise`, then existing `ValueError`/`Exception` -> `None`. Absent policy -> deny.
- [ ] `ToolListScope.resolve`: alias present -> `ToolCallRefused(CODE_BAD_ARGUMENTS)`; return `ResolvedTool(tool, None, False)`.
- [ ] `ScriptToolRegistry(user, policy=None)` replaces `workflow_project`; `context()` returns `ProjectScope` when a policy is set. Rewrite the old `callable_tools() is None` assertions.

### Task 4: Alias and owner through the build path

**Files:**
- Modify: `workflow_resolution.py` (`resolve_workflow_tool`), `src/codemie/service/assistant/virtual_assistant_service.py` (`create_from_tool_invocation`), `src/codemie/service/tools/tool_service.py` (`find_tool_by_invoke_request` :119, `get_toolkit`, `get_core_tools`, `find_setting_for_tool` :187)
- Test: `tests/codemie/service/script_tool_calls/test_workflow_resolution.py` (new), `tests/codemie/service/tools/test_tool_service_owner_user_id.py`

Test-first: yes — build a tool with alias in a global workflow where only the creator holds the integration and inspect the credentials the built tool holds (not just resolver args); mismatched credential-type case; settings lookup failing for an alias raises `NoIntegrationForAliasError(ValueError)` which the resolver maps to `ToolCallRefused(CODE_BAD_ARGUMENTS)` with a clear message; existing `ValueError` callers still work.

- [ ] Add `NoIntegrationForAliasError(ValueError)` raised where `find_setting_for_tool` raises `SETTING_NOT_FOUND_ERROR` for an alias.
- [ ] Thread `integration_alias` and `owner_user_id` (default None) through the four functions; `resolve_workflow_tool(user, project, selector, owner_user_id=None)` returns `ResolvedTool`. `ProjectScope` passes `owner_user_id` only when `decision.runs_as_owner`.
- [ ] Keep the `finally` deletion of the temp assistant.

### Task 5: Plumbing step -> scope

**Files:**
- Modify: `src/codemie/rest_api/models/assistant.py` (`VirtualAssistant`), `virtual_assistant_service.py:~77` (`create_from_tool_config`), `src/codemie_tools/data_management/workspace/toolkit.py` (`_workflow_project`, ~75 union comment)
- Test: `tests/codemie_tools/data_management/workspace/test_toolkit_script_registry.py` (line ~159), `tests/codemie/service/tools/test_toolkit_service_script_registry.py`

Test-first: yes — `create_from_tool_config` builds a policy with `owner_mode` creator (global with `created_by`), `unknown_creator` (global, none), `none` (not global), and an empty policy when no map; `is_tool_step` is True iff the policy is set; the policy reaches `ScriptToolRegistry` through `AgentWorkspaceToolkit` (VirtualAssistant stays in the union).

- [ ] Add `script_step_policy: Optional[ScriptStepPolicy]` on `VirtualAssistant` (non-persisted); make `is_tool_step` a derived property; `create_from_tool_config` receives the workflow's `is_global`/`created_by` (from `ToolNode`, which already has them) and builds the policy for every tool step.
- [ ] Toolkit passes `assistant.script_step_policy` and the project to `ScriptToolRegistry`.

### Task 6: SDK and reference

**Files:**
- Modify: `src/codemie_tools/data_management/code_executor/runtime_sdk/codemie_runtime_sdk.py` (`call_tool` ~285, `call_tools`), `runtime_sdk/codemie_runtime_sdk_reference.md` (lines 8-36)
- Test: `tests/codemie_tools/data_management/code_executor/runtime_sdk/test_codemie_runtime_sdk.py`, `test_sdk_reference.py`, `test_sdk_public_surface.py`

Test-first: yes — `call_tool("t", {}, integration_alias="a")` sends `{"name","args","integration_alias"}` and omits the key when None; `call_tools` item with non-string/empty alias raises a bad-arguments error locally; reference text documents the alias and states assistants refuse it; the two-example assertion is updated.

- [ ] `call_tool(name, args=None, *, integration_alias=None, timeout=None)`; validate alias type per `call_tools` item; update the reference markdown and `test_sdk_reference.py`; protocol `v` unchanged.

### Task 7: Save-time validation

**Files:**
- Modify: `src/codemie/workflows/validation/resources.py` (`_is_integration_alias_valid` :364, `_validate_tools_avaiability` :669, `validate_workflow_config_resources_availability` :720)
- Test: `tests/codemie/workflows/test_config_resources_validation.py`

Test-first: yes — a map on a non-`execute_workspace_script` step is refused naming the step; unknown tool, non-script-callable tool, alias missing for `created_by`, alias outside the project, mismatched credential type, and a credentialless tool given an alias each refuse the save with a message naming tool and alias; undeterminable expected type skips with a logged warning; valid map passes.

- [ ] Reuse the exclusion/callable check (no second list). Validate aliases against `created_by` through `owner_user_id` in `find_setting_for_tool`.
- [ ] New helper compares setting `credential_type.value` with `ToolMetadataService.get_credential_type(config_class)`.

### Task 8: Publish warning

**Files:**
- Modify: `resources.py` (next to `collect_consumer_slot_integration_warnings` :483), `src/codemie/rest_api/models/workflow_marketplace.py` (`WorkflowPublishValidationResponse`), `src/codemie/rest_api/routers/workflow.py` (~220 `_consumer_slot_warnings_sync`, publish and save paths)
- Test: `tests/codemie/workflows/test_workflow_missing_integration_user_slots.py`, `tests/codemie/rest_api/routers/test_workflow.py`

Test-first: yes — `collect_script_step_owner_integration_warnings(config, user, treat_as_global=True)` on a stored non-global workflow returns an item with `kind="script_step_owner_integration"` naming step, tool and aliases; no aliases or `treat_as_global=False` returns none; existing consumer-slot items gain `kind="consumer_slot"` with otherwise identical shape; a collector exception never fails save or publish.

- [ ] Add `warnings` list to the response; callers pass the proposed global value (`_apply_publish` flips it later); same catch-all wrapper.

### Task 9: Docs

**Files:**
- Modify: `docs/workspace-script-sdk.md` (14-61), `src/codemie_tools/data_management/code_executor/README.md` (~279-330, 384), `docs/stories/2026-09-28-custom-tools/architecture-spec.md` (section 7.5)

Test-first: no — documentation only.

- [ ] Document the alias parameter, step policy and accepted risk for assistants; add the two 7.5 line edits from the spec (residual risk; PROJECT-settings row closed only for tools outside the list).
