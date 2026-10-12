# Technical Research

**Task**: workflow script step tool-call scope integration alias
**Generated**: 2026-10-08
**Research path**: filesystem

---

## 1. Original Context

Source: `docs/superpowers/tasks/2026-10-08-epmcdme-15752/ticket.md` (EPMCDME-15752, sub-task of story EPMCDME-15568, epic EPMCDME-15364), verbatim.

**Summary:** Backend: step scope and integration alias for script tool calls (codemie)

```text
h2. Scope

Backend part of EPMCDME-15568 (repository: codemie). Policy for the script execution step of a workflow: the author lists the tools the script may call and, per tool, the integration aliases it may use. See the parent story for decisions and acceptance criteria.

h2. Work

* Add the optional {{integration_alias}} to the payload ({{_parse_payload}}), {{authorize_tool_call}}, the {{resolve}} signatures of the scopes, {{WorkflowToolResolver}} and {{resolve_workflow_tool}}.
* Add it to the SDK ({{call_tool}}, {{call_tools}} items) and to the SDK reference in the script tool's description, with a contract test. No new error codes ({{tool_unavailable}} for a tool outside the map, {{bad_arguments}} for an alias outside its list or missing when several are listed).
* A map on the step (tool name to a list of aliases) read by the workflow scope: resolves only listed tools, checks aliases ({{find_setting_for_tool}}, the creator's id for a global workflow, the running user's integrations otherwise); a step with no map calls no tool. {{callable_tools()}} returns the listed tools for the generator and the test run.
* The field on {{WorkflowTool}} and its validation at save time (reuse {{_is_integration_alias_valid}} and the tool checks in {{workflows/validation/resources.py}}): the tool exists and is script-callable, the alias exists for the author, is in the workflow's project and fits the tool's credential type.
* Publish warning for a global workflow whose script step lists aliases, next to {{collect_consumer_slot_integration_warnings}}.
* Refuse an alias in the assistant scope (chat and workflow assistant step) with {{bad_arguments}}.
* Tests for each acceptance criterion of the parent story; update the SDK page and the code executor README (accepted risk for assistants, no owner control).
```

Acceptance criteria (parent story EPMCDME-15568), verbatim from ticket.md:

```text
* Given a script step with no map (or an empty map), when its script calls any tool, then the call fails with {{tool_unavailable}} and the tool is not run.
* Given a map listing tool T, when the script calls T, then it runs; given a tool not in the map, then {{tool_unavailable}}.
* Given tool T listed with no aliases, when the script calls it, then it runs under the running user's automatic lookup in the workflow's project; an alias in that call gives {{bad_arguments}}.
* Given T listed with aliases [a, b] in a global workflow, when the script passes a, then T runs under the creator's integration a; when it passes c, then {{bad_arguments}} listing a and b and T is not run; when it passes none, then {{bad_arguments}}.
* Given T listed with the single alias a, when the script passes none, then T runs under a.
* Given a workflow that is not global, when the script passes a listed alias, then T runs under the running user's integration of that alias in the workflow's project; if that user has none, a clear error.
* Given the script text, a run-start file or a model-written script names an alias the step does not list, when it runs, then the creator's account is not reached.
* Given a workflow is saved, when the map names a tool that does not exist or is not script-callable, or an alias that does not exist for the author, is outside the workflow's project or does not fit the tool's credential type, then the save is refused with a message naming the entry.
* Given a global workflow whose script step lists aliases is published or saved as global, then a warning says that the tool runs under the author's integration for everyone who runs the workflow.
* Given a chat or a workflow assistant step, when a call carries an alias, then {{bad_arguments}} and the tool is not run; calls without an alias behave as before.
* Given the test run and the generator of a script step, then they offer and accept only the tools of the map.
* Given the editor of a workflow script step, then the author picks tools and, per tool, aliases from their own integrations; an empty selection is shown as "no tools".
```

---

## 2. Codebase Findings

### Existing Implementations
All paths under `/Users/yanaasadchaya/Projects/epam/codemie/codemie`. Python project (Poetry, `pyproject.toml`), packages `src/codemie` (backend) and `src/codemie_tools` (tools/SDK). No `.codegraph/` index.

Script tool-call package `src/codemie/service/script_tool_calls/`:
- `handler.py` — `_handle_tool_call(context, payload, gate, max_payload_bytes)` calls `_parse_payload(payload)` (returns `(name, args)`), then `authorize_tool_call(context, name)`, then checks `ScriptCallable`, `_validated_kwargs`, `tool.run_for_script`. `_PAYLOAD_FIELDS = frozenset({"name","args"})` (line ~49); unknown fields are refused with `CODE_BAD_ARGUMENTS`. The comment in `_parse_payload` says "The 'alias' field is deferred to the future Integration Control for Scripts story".
- `authorizer.py` — `authorize_tool_call(context, name) -> BaseTool`: blocks the script tool by name (`tool_blocked`), calls `context.scope.resolve(name)`, raises `tool_unavailable` if None or `is_excluded_from_script_calls(tool)`. Signature has no alias.
- `context.py` — `WorkflowToolResolver = Callable[[User, str, str], BaseTool]`; `ScriptScopeKind` (NONE/ASSISTANT/WORKFLOW); `ScriptScope` Protocol (`resolve(name)`, `callable_tools()`); `NoScope` (resolve raises `no_context`; `callable_tools()` = `()`); `ToolListScope` (assistant tool list; `callable_tools()` returns tools); `ProjectScope(user, project, resolver=resolve_workflow_tool)` (resolve: `is_known_excluded` prefilter, then resolver; `ValueError` or any exception becomes `None`; `callable_tools()` returns `None` = whole catalog); `ScriptRunContext` (frozen dataclass: user, scope, scope_kind); `ScriptToolRegistry(user, workflow_project=None)` — `context()` returns `ProjectScope(self._user, self._workflow_project)` when `workflow_project` is set, else the `ToolListScope` filled via `fill()`, else `NoScope`.
- `workflow_resolution.py` — `resolve_workflow_tool(user, project, name)`: lazily imports services; `VirtualAssistantService.create_from_tool_invocation(name, user, project)` then `ToolsService.find_tool_by_invoke_request(name, ToolkitService.get_toolkit_methods(), assistant, user, project)`; deletes the temp assistant in `finally`. `create_from_tool_invocation` already accepts an `integration_alias` parameter (unused by the resolver); it calls `ToolsService.get_toolkit(tool_name, user, project, integration_alias)` which has **no** `owner_user_id` argument at that call site.
- `binding.py` — `bind_script_registries` fills registries with the final tool list for assistants (`registry.fill(list(callable_tools.values()), assistant)`).
- `exclusions.py`, `registry_prefilter.py` — exclusion function and prefilter (`is_known_excluded`).

Wiring of the workflow script step:
- `src/codemie_tools/data_management/workspace/toolkit.py` — `AgentWorkspaceToolkit._workflow_project()` returns `assistant.project` only when `VirtualAssistant.is_tool_step` is True; passed as `ScriptToolRegistry(self.user, workflow_project=...)` into `ExecuteWorkspaceScriptTool`. `_script_tool_description` composes the base description plus `read_sdk_reference(tool_calling)`.
- `src/codemie/service/assistant/virtual_assistant_service.py` — `create_from_tool_config(tool_config: WorkflowTool, user, project_name, execution_id, owner_user_id=None)` creates the step's virtual assistant with `is_tool_step=True` (line ~77). The marker carries only the project; neither the step's map nor the workflow's `is_global`/creator id reach `ScriptToolRegistry` today.
- `src/codemie/workflows/nodes/tool_node.py` — `ToolNode._owner_user_id` (line ~370): creator's `created_by.user_id` only when `workflow_config.is_global` and `_tool_config.integration_alias is not None`; else None. Passed as `owner_user_id` at lines ~136, 194-220.
- `src/codemie/service/tools/tool_service.py` — `ToolsService.find_setting_for_tool(user, project_name, integration_alias, owner_user_id=None)` (line 187): looks up `SettingsService.retrieve_setting` by user id (`owner_user_id or user.id`), project, alias; raises `ValueError(SETTING_NOT_FOUND_ERROR)`. `get_toolkit_from_workflow_tool_config`, `get_toolkit(..., integration_alias, owner_user_id=)` at lines ~44-95.

Models and validation:
- `src/codemie/core/workflow_models/workflow_models.py` — `WorkflowTool` (line 99): fields `id, tool, tool_args, integration_alias: Optional[str], tool_result_json_pointer, trace, mcp_server, resolve_dynamic_values_in_response, input_key, tokens_size_limit`. It already has a single `integration_alias`; no map field exists. `WorkflowConfig.is_global` / `created_by` in `workflow_config.py` (line ~118).
- `src/codemie/workflows/validation/resources.py` — `_is_tool_available` (342), `_is_integration_alias_valid(user, project_name, alias)` (364, wraps `find_setting_for_tool`, ValueError -> False), `_tool_requires_missing_integration` (credential check via `CredentialValidator.validate_tool_credentials`), `_validate_tools_avaiability` (669, loops `workflow_config.tools`), `_validate_tools_from_assistants_availability` (alias check for assistant tools at ~649), `validate_workflow_config_resources_availability` (720), `collect_consumer_slot_integration_warnings(workflow_config, user) -> list[dict]` (483; dict shape `assistant_ref`, `tool_name`, `credential_type`; only covers assistants).
- `src/codemie/rest_api/routers/workflow.py` — imports the collector (line 59); `_consumer_slot_warnings_sync` (line ~220) wraps it with a catch-all so a failing advisory never fails a save; async wrapper follows.

SDK (`src/codemie_tools/data_management/code_executor/`):
- `runtime_sdk/codemie_runtime_sdk.py` — `call_tool(name, args=None, *, timeout=None)` builds `{"name","args"}` (line 285); `call_tools(calls, ...)` copies each dict item (`payload = dict(item)`), so extra keys already pass through to the backend; validates only dict with string `name`. Error codes as constants (`CODE_BAD_ARGUMENTS`, `CODE_TOOL_UNAVAILABLE`, ...).
- `runtime_sdk/codemie_runtime_sdk_reference.md` — SDK reference text in the script tool description (lines 8-36 hold the signatures, `call_tool`/`call_tools` bullets and examples); `<<placeholder>>` substitution done in `sdk_reference.py::read_sdk_reference`.
- `docs/workspace-script-sdk.md` — SDK page (call_tool/call_tools sections, lines 14-61). `src/codemie_tools/data_management/code_executor/README.md` — lines ~279-330 cover `call_tool`/`call_tools`; line 384 points at architecture spec section 7.5 for accepted risks.

### Architecture and Layers Affected
- Protocol/SDK layer (runtime SDK + reference markdown) — optional field.
- Script tool-call service layer (`handler`, `authorizer`, `context` scopes, `workflow_resolution`).
- Workflow node / virtual assistant wiring (`ToolNode`, `VirtualAssistantService.create_from_tool_config`, `AgentWorkspaceToolkit`) — the path by which the step's configuration reaches the scope.
- Workflow model layer (`WorkflowTool`) and validation (`workflows/validation/resources.py`), REST router save/publish warnings (`rest_api/routers/workflow.py`).
- Docs (SDK page, code executor README).

### Integration Points
- Settings lookup: `ToolsService.find_setting_for_tool` -> `SettingsService.retrieve_setting`.
- `CredentialValidator.validate_tool_credentials` for "fits the tool's credential type".
- Toolkit stack imports this package (hence the lazy imports in `workflow_resolution.py`; `context.py` imports `workflow_resolution` at module level).
- Dependency direction: `handler` -> `authorizer` -> `context` -> `workflow_resolution` -> (lazy) `service.assistant`, `service.tools`; `codemie_tools` workspace toolkit -> `script_tool_calls.context`.

### Patterns and Conventions
- Scope as protocol object with `resolve(name)`/`callable_tools()`; authorizer has no per-scope branch and applies exclusion to every resolved tool (spec D21). Tools opt in via class flag `script_callable`; snapshot `tests/codemie/service/script_tool_calls/callable_tool_classes.txt`.
- Error codes are a closed set of SDK constants; backend uses the names, a contract test (`test_error_code_contract.py`) forbids string literals and unknown codes.
- Resolver failures are swallowed to `None` in `ProjectScope.resolve` (`ValueError` and backstop `Exception`) - relevant because an alias error must surface as `bad_arguments`, not as `tool_unavailable`.
- Advisory warnings never block save (router catch-all); validation failures raise `ExtendedHTTPException` 400 with details.
- Apache license header on all files; Ruff standards (`.ai-run/guides/standards/code-quality.md`).

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/` exists (agents, api, architecture, data, development, integration, security, standards, testing, workflows). Relevant: `agents/agent-tools.md`, `architecture/service-layer-patterns.md`, `development/error-handling.md`, `development/security-patterns.md`, `testing/testing-patterns.md`, `quality-gates.md` (not opened in detail).
- Epic spec `docs/stories/2026-09-28-custom-tools/architecture-spec.md` (v6, 2026-10-08): line 252 (error codes; alias not in protocol v1 as built; no `integration_mismatch`), line 326 (credentials and scope), line 565 (scope table row for workflow tool step), line 570 (detailed alias rules), D19 (line 625), D21 (627), D28 (634), accepted-risk rows at 583 and 595-596.
- Story `docs/stories/2026-09-28-custom-tools/children/script-owner-control/story.md` (the EPMCDME-15568 story); `children/script-integration-control/story.md` is a superseded placeholder (absorbed). Sibling `workflow-script-step` (EPMCDME-15559) story/analysis is the built predecessor.

### Architectural Decisions
- Spec 570: tool not in map -> `tool_unavailable`; alias must be listed or `bad_arguments` naming permitted ones; none passed with exactly one listed -> that one; several -> `bad_arguments`; tool listed with no alias -> running user's automatic lookup, alias in call -> `bad_arguments`. Listed alias in global workflow resolves under the creator (`ToolNode._owner_user_id`), else among the running user's integrations. Chat/assistant step: alias refused with `bad_arguments`, no alias for assistants' tools planned.
- D19: adding optional `integration_alias` is additive; protocol `v` stays 1. D21 v6: `resolve(name, alias)`; `callable_tools()` lists the listed tools (no longer `None`). Accepted risk for global assistants (spec 583, 7.5) must be documented in the SDK page and code executor README.

### Derived Conventions
Per-call alias must come only from step configuration for the creator's account; the script-provided alias is only a selector among listed aliases.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/script_tool_calls/`: `test_authorizer.py`, `test_context.py`, `test_handler.py`, `test_workflow_tool_step_scope.py` (ProjectScope with fake catalog; asserts `callable_tools() is None` at line 124 and `["catalog_tool"]` for assistant scope at line 204), `test_end_to_end.py`, `test_error_code_contract.py`, `test_exclusions.py`, `test_registry_exclusion_sync.py`, `test_concurrency.py`.
- `tests/codemie_tools/data_management/workspace/test_toolkit_script_registry.py` (line 159 asserts `callable_tools() is None` "the whole catalog"), `test_execute_workspace_script_tool.py`, `test_toolkit_script_description.py`.
- `tests/codemie_tools/data_management/code_executor/runtime_sdk/`: `test_codemie_runtime_sdk.py`, `test_sdk_public_surface.py`, `test_sdk_reference.py` (contract tests for the reference/description).
- `tests/codemie/service/tools/test_toolkit_service_script_registry.py`, `test_tool_service_owner_user_id.py`.
- `tests/codemie/workflows/test_config_resources_validation.py`, `test_workflow_missing_integration_user_slots.py` (validation and consumer-slot warnings); `tests/codemie/rest_api/routers/test_workflow.py`.

### Testing Framework and Patterns
pytest (`pytest.ini`), some unittest-style classes. Fake catalog via `unittest.mock.patch` of lazily imported services; `CodeMieTool` subclasses with `script_callable = True`; `User(id=..., username=...)` fixtures. Per AGENTS.md, tests are written or run only when explicitly requested (this ticket does request tests).

### Coverage Gaps
No tests exist for: an alias in the payload/SDK, a map on the step, `callable_tools()` of the workflow scope returning the listed tools, alias validation at save, or a script-step publish warning. Existing assertions that the workflow scope's `callable_tools()` is `None` (test_workflow_tool_step_scope.py:124, test_toolkit_script_registry.py:159) encode the behaviour this ticket changes.

---

## 5. Configuration and Environment

### Environment Variables
None specific found for this area (tool-calling limits come from `tool_calling_limits.py` `ToolCallingSettings`, resolved by `resolve_tool_calling_settings()`).

### Configuration Files
`pyproject.toml`, `pytest.ini`, `Makefile` (quality gates per `.ai-run/guides/quality-gates.md`). Workflow definitions are YAML parsed in `workflow_config.py` (`tools:` list becomes `WorkflowTool`).

### Feature Flags and Deployment Concerns
No feature flag for the script step found. The workflow `tools` are persisted in the workflow config; no DB migration located for `WorkflowTool` (config is YAML/JSON-backed - not verified whether the model is stored as JSON column).

---

## 6. Risk Indicators

- Plumbing gap: the step's map, `is_global` and creator id do not reach `ScriptToolRegistry`/`ProjectScope`; the only channel today is the `is_tool_step` marker carrying a project (`toolkit.py:_workflow_project`, `virtual_assistant_service.create_from_tool_config`). Speculative: a new carrier on `VirtualAssistant` or the registry will be needed; `VirtualAssistant` stores no `owner_user_id` (spec/placeholder story notes this).
- `resolve_workflow_tool` -> `create_from_tool_invocation` -> `ToolsService.get_toolkit` has no `owner_user_id`; `find_setting_for_tool` supports it, but the build path used by the resolver does not pass it. Speculative: threading an owner id through several signatures (spec estimated ~8 places in 5 modules).
- `ProjectScope.resolve` swallows `ValueError` and all exceptions to `None` (=> `tool_unavailable`), which conflicts with "alias outside list -> `bad_arguments`" and "clear error if the user has no integration"; error mapping needs care.
- Existing tests assert `callable_tools() is None` for the workflow scope; behaviour change breaks them (intended).
- `_PAYLOAD_FIELDS` and `_parse_payload`'s unknown-field refusal: older SDK/backend mixes (old backend answers `bad_arguments` to the new field - additive, `v` stays 1).
- `call_tools` already forwards extra item keys, so the backend must validate type of `integration_alias` (non-string/empty).
- `collect_consumer_slot_integration_warnings` covers only assistants and returns dicts with a fixed shape consumed by the UI; a new warning kind changes the response contract with `codemie-ui` (out of scope here). Router also needs to know the workflow is global/published at both save and publish paths.
- Save-time validation: "script-callable" check requires resolving tool class `script_callable` flag from a tool name; `_is_tool_available` only checks existence. Credential-type fit uses `CredentialValidator`, whose behaviour with an explicit alias is exercised only in `_tool_requires_missing_integration`.
- Field storage format and any DB/schema impact for the new `WorkflowTool` field not verified.
- Security: alias from script text must never reach the creator's account unless listed; contract/negative tests are essential.

---

## 7. Summary for Complexity Assessment

The change spans five layers in the codemie repository: the runtime SDK and its reference markdown (optional `integration_alias` on `call_tool`/`call_tools`), the script tool-call service (`handler._parse_payload`, `authorizer.authorize_tool_call`, three scope `resolve` signatures, `WorkflowToolResolver`, `resolve_workflow_tool`), the wiring from a workflow tool step to the scope (`ToolNode`, `VirtualAssistantService.create_from_tool_config`, `AgentWorkspaceToolkit`, `ScriptToolRegistry`), the `WorkflowTool` model with save-time validation in `workflows/validation/resources.py`, and the publish-warning path in the workflow router, plus docs (`docs/workspace-script-sdk.md`, code executor README). Roughly 12-15 source files plus tests and two docs.

Technical novelty is moderate: the pieces exist (scope protocol, `find_setting_for_tool` with `owner_user_id`, alias validator, warning collector) but the step's configuration has no path into the scope today, the resolver build path lacks an owner id, and the scope contract flips from "whole catalog" (`callable_tools() is None`) to a listed set. Error mapping is subtle because the scope currently swallows resolver failures to `tool_unavailable`.

Test coverage is good around the script tool-call package and SDK reference (dedicated contract tests), but nothing covers alias or map behaviour, and several current assertions encode the old workflow-scope behaviour. Key risks: security of the creator's account, plumbing the owner/global flag, error-code mapping, and the warning/validation contract with the UI.

---

## 8. External References

None named by the task as outside paths. In-repo sources named and read: `docs/superpowers/tasks/2026-10-08-epmcdme-15752/ticket.md` (reproduced in Section 1), `docs/stories/2026-09-28-custom-tools/architecture-spec.md` (key facts in Sections 3 and 6: lines 252, 326, 565, 570, D19, D21, D28), `children/script-owner-control/story.md` and `children/script-integration-control/story.md` (read in part). Not read in detail: sibling `story.md`, `technical-analysis.md`, `children/workflow-script-step`, `children/script-tool-calls`.
