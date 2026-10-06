# Technical Research

**Task**: workflow tool node, workspace script tool, run-start file registration (revised 2026-10-06: `integration_alias` deferred)
**Generated**: 2026-10-06
**Research path**: filesystem

> Scope update: `integration_alias` is deferred out of this story (to the future "Integration Control for Scripts" story, no Jira ticket yet). Alias findings from the first version of this document are collapsed into section 6 ("Deferred"). Authoritative scope is `spec.md` in this directory.

---

## 1. Original Context

Jira EPMCDME-15559 "Run a Script from a Workflow Step" (story 3 of the Tool Composition epic). Ticket text is in /private/tmp/claude-501/-Users-yanaasadchaya-Projects-epam-airun-codemie-dev-codemie/6787c8bb-1723-4019-be4d-00c2a76cc10c/scratchpad/ticket.txt; architecture spec: docs/stories/2026-09-28-custom-tools/architecture-spec.md (§5.3, §7.3, D21, D28). Story 1 (EPMCDME-15400/15402 script tool calls) is merged on main.

Work in this story (codemie repo only): (1) confirm `execute_workspace_script` resolves in a workflow tool step (tool node passes the execution id as conversation); (2) register run-start files by reference in the workspace keyed by the execution id via a new `AgentWorkspaceService.register_run_files`, with or without a conversation, validated by a shared shape rule and a downloadability (ownership) rule, with a router 400 and a file-count cap; (3) step output = whole script-tool JSON, changed files persist; (4) tests per acceptance criterion, including AC 8 (running user's lookup, never the creator's USER-type integrations); (5) small fix to `delete_by_execution_id`.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/workflows/nodes/tool_node.py` — `ToolNode._execute_regular_tool` builds a virtual assistant via `VirtualAssistantService.create_from_tool_config(..., execution_id=self.execution_id, owner_user_id=...)` (is_tool_step=True), then `ToolsService.find_tool_from_config(..., execution_id=...)`, runs the tool, returns the result as is. `ToolNode._owner_user_id` returns the creator for a global workflow when the step itself carries a pinned `integration_alias`. `build_unique_file_objects_list` (line ~195) loads blob content unchecked.
- `src/codemie/service/tools/tool_service.py` — `get_toolkit`, `find_tool_from_config`, `find_tool_by_invoke_request`, `find_setting_for_tool(user, project_name, integration_alias, owner_user_id=None)`. The workspace toolkit receives the running user (~line 114).
- `src/codemie/service/script_tool_calls/` — `context.py` (`ScriptScope`, `NoScope`, `ToolListScope`, `ProjectScope`, `ScriptToolRegistry`), `handler.py` (`_parse_payload` returns `(name, args)`, `_PAYLOAD_FIELDS = {"name", "args"}`; comment at line ~111 "story 3 adds one" is stale after the deferral), `authorizer.py`, `workflow_resolution.py` (`resolve_workflow_tool(user, project, name)`), `binding.py`, `exclusions.py`, `concurrency.py`.
- `src/codemie_tools/data_management/workspace/toolkit.py` — `AgentWorkspaceToolkit._workflow_project()` returns the project only for a `VirtualAssistant` with `is_tool_step`; `get_tools()` builds the script tool with `ScriptToolRegistry(self.user, workflow_project=...)`.
- `src/codemie/service/workspace_script_bridge.py` — bridge on only with the feature flag and jobs sandbox mode.
- `src/codemie/service/agent_workspace_service.py` — `sync_uploaded_files(conversation_id, file_urls, user)` (~line 145): dedups, registers by reference but reads each blob in full to checksum, silently skips undecodable URLs, registers an empty file for a missing blob. `create_workspace` (~line 120) seeds through it (call at line 138).
- Callers of `sync_uploaded_files` (five): `rest_api/handlers/assistant_handlers.py:274`, `rest_api/routers/workflow_executions.py:263`, `service/conversation_service.py:472`, `service/workflow_service.py:313`, `service/agent_workspace_service.py:138`.
- `src/codemie/service/workflow_service.py` — `create_workflow_execution` (~line 228). Real order: augment input, `_get_or_create_conversation` (saves a new conversation), mint `execution_id`, build execution, `conversation.update()` with a `workflow_execution_ref`, chat-branch `sync_uploaded_files` on the conversation id, `execution_config.save`. The `except` only re-raises. No validation inside the service. Only the router passes `file_names`; `workflow.py:1212`, `sub_workflow_node.py:115` and `workflow_evaluation_service.py:143` pass none.
- `src/codemie/rest_api/routers/workflow_executions.py` — create route (line ~237-340): `_validate_workflow_supports_files_and_raise` (line ~909) checks only the bedrock case; the router syncs the conversation (line 263) before the service. `resume_workflow_execution` (line ~390) takes `file_names` (line 424).
- `src/codemie/rest_api/routers/files.py` — download route; `_points_outside_owner` (line ~245) is a shape-only check (empty, absolute, `..`) with no user; ownership is decided by download rules A-E (own id, own `workspace-<id>`, share grant, MCP images, schema bucket).
- `src/codemie/service/assistant/virtual_assistant_service.py` — `delete_by_execution_id` (line 152, from initial commit `afc61fa12`) snapshots keys then does `del cls.assistants[key]`: a `KeyError` race in the workflow's unguarded final cleanup (`workflow.py:1027`).
- `src/codemie/rest_api/security/workflow_context.py` and `job_bridge._request_scoped_handlers` (`copy_context`): the workflow id reaches channel handler threads; untested today.

### Architecture and Layers Affected
- Service layer: `WorkflowService.create_workflow_execution`, `AgentWorkspaceService` (new `register_run_files`), new shape rule and ownership rule in `codemie/service/file_service/`, `VirtualAssistantService`.
- Router: `workflow_executions.py` (400 on bad token, count cap), `files.py` (reuses the extracted rules).
- Tests only for the tool node and script bridge confirmation. No DB migrations or models.

### Patterns and Conventions
- Strict typing, `from __future__ import annotations`, modern unions. A service never imports from a router. Seam tests per `.ai-run/guides/testing/testing-patterns.md`.

---

## 3. Documentation Findings

- Guides: `.ai-run/guides/` (agents, workflows, service-layer, security, testing, local-verification, quality-gates).
- Architecture spec §5.3: a run has one workspace keyed by the execution id; files given at start are registered there by this story. §7.3: workflow tool step scope = any catalog tool minus exclusions, running user's lookup (D28).
- Ticket decisions 1-8 stay, except the alias part of decision 6 (deferred).

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/script_tool_calls/` (context, authorizer, handler, exclusions, end_to_end, concurrency, error_code_contract), `tests/codemie/service/tools/test_find_tool_from_config_execution_id.py`, `tests/codemie/service/test_agent_workspace_service_tool_calling.py`, `tests/codemie/service/test_workflow_service.py`, `tests/codemie/rest_api/routers/test_workflow_executions*.py`, `tests/codemie/workflows/test_tool_node_context.py`, `tests/codemie/rest_api/security/test_workflow_context.py`.

### Coverage Gaps
- No test of run-start registration under the execution id (with or without a conversation), nor of its ordering relative to the conversation write.
- No accept/reject tests for the shape and ownership rules on registration.
- No test that a global workflow run by another user, including a step with a pinned alias, gives the script registry the running user; no fixture asserting PROJECT-type reach.
- No test that the workflow id reaches a handler thread; no forced-interleaving test for `delete_by_execution_id`.
- AC 4, 5, 7 and the workflow-id test are characterisation tests (pass on unchanged code).

---

## 5. Configuration and Environment

- Bridge needs the customer feature flag and jobs sandbox mode; a real-run confirmation of ticket decisions 1, 2, 7 needs a jobs-mode sandbox (manual). A file-count cap setting is new (value chosen at planning).

---

## 6. Risk Indicators and Deferred

- The ordering change in `create_workflow_execution` alters the chat path (on failure only): the execution id must be minted and files registered before `_get_or_create_conversation`.
- Shape rule applied inside `sync_uploaded_files` changes behaviour for all five callers (unsafe tokens skipped); an `owner != user.id` rule would break share-grant and `workspace-<id>` files, so ownership reuses the download rules.
- Registration reads every blob in full on the create request (up to three times in a chat start); accepted, unmeasured. A retry or a later failure orphans a workspace; no delete path exists.
- Resume gap: files supplied only at resume are not registered.
- PROJECT-type settings of the workflow's project stay reachable by automatic lookup without an access check (existing behaviour; follow-up).
- Deferred alias: the later story touches about eight places in five modules (`_parse_payload`, `authorize_tool_call`, four `resolve` signatures, `WorkflowToolResolver`, `resolve_workflow_tool`, `owner_user_id` not stored on `VirtualAssistant`), together with story 13. Documents still carrying the alias are listed in `spec.md` (Deferred).

---

## 7. Summary for Complexity Assessment

Production surface: `workflow_service.py` (reordered run start), `agent_workspace_service.py` (`register_run_files`, shape rule inside sync), a new small module in `codemie/service/file_service/`, `files.py` and `workflow_executions.py` (router validation, 400, cap), `virtual_assistant_service.py` (one-line race fix). No script bridge, SDK or Protocol changes. Tool-step resolution and the run workspace appear already wired, so that part is confirmation and tests. Test surface is about 11 acceptance criteria, several of them characterisation tests.

---

## 8. External References

- Ticket text (scratchpad `ticket.txt`) and the architecture spec above. Architecture reviews: `local/architecture-review/2026-10-06-epmcdme-15559-spec/report.md` and `...-spec-v2/report.md`.
