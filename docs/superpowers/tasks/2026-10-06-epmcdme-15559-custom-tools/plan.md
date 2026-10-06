# EPMCDME-15559 Run a Script from a Workflow Step Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** A workflow tool step runs `execute_workspace_script` against the run workspace, and run-start files are registered in it under the execution id.

**Architecture:** Extract the download rules (A-E, `files.py:245-368`) and a shape rule into `codemie/service/file_service/`; `AgentWorkspaceService.register_run_files` and the router validation reuse them. `create_workflow_execution` registers before any conversation write. Tool-step resolution is confirmed by tests, not new code.

**Tech Stack:** Python, FastAPI, pytest. Strict typing (no `Any`, `X | None`, annotate everything, `from __future__ import annotations`).

Commit per task using the repository's existing convention (`EPMCDME-15559: <description>`). Whole-suite gates and the jobs-mode real run are owned by the calling flow, not this plan. Keep `tests/codemie/service/script_tool_calls/test_error_code_contract.py` green.

---

### Task 1: Shape rule `is_safe_blob_ref`

**Files:** Create `src/codemie/service/file_service/blob_ref_rules.py`; Modify `src/codemie/rest_api/routers/files.py:245-257,338`; Test `tests/codemie/service/file_service/test_blob_ref_rules.py`

Add pure `is_safe_blob_ref(file_object: FileObject) -> bool` applied to owner and name. It rejects empty, absolute, `..` part, backslash, bare `.` and control characters. Replace `_points_outside_owner` in `files.py` with it (inverted), updating its callers/tests.

- [ ] Test-first: yes — parametrized reject cases (empty, `/abs`, `../x`, `a\b`, `.`, `a\x00b`) fail with ImportError today; accept case `uuid/name.pdf`; existing files-route tests still pass.
- [ ] Implement, run `pytest tests/codemie/service/file_service tests/codemie/rest_api/routers/test_files*.py`.

### Task 2: Extract ownership rules A-E to the service layer

**Files:** Create `src/codemie/service/file_service/download_rules.py`; Modify `src/codemie/rest_api/routers/files.py:206-368`; Test `tests/codemie/service/file_service/test_download_rules.py`

Move `_can_read_workflow_schema`, `_is_readable_workflow_schema`, `_owns_workspace_blob`, `_conversation_refers_to_file`, `_has_share_grant` and the rule order of `_authorize_file_access` into a public `can_download(file_object: FileObject, user: User, share_token: str | None, file_name_param: str, workspace_repo: AgentWorkspaceRepository) -> bool` (shape rule first, then A own id, B schema, C MCP images, D own `workspace-<id>`, E share grant). The router's `_authorize_file_access` becomes `if not can_download(...): _raise_file_not_found()`. Behaviour unchanged; the service never imports from the router.

- [ ] Test-first: yes — new tests call `can_download` directly: accepts own id, own `workspace-<id>`, share grant (token supplied); rejects foreign owner and unsafe shape. Fails today (module missing). Existing `files.py` route tests must pass unchanged.
- [ ] Implement, run the two test dirs above.

### Task 3: Skip unsafe tokens in `sync_uploaded_files`; add `register_run_files`

**Files:** Modify `src/codemie/service/agent_workspace_service.py:~145` (`sync_uploaded_files`); Test `tests/codemie/service/test_agent_workspace_service_run_files.py`

- `sync_uploaded_files`: skip and log tokens failing `is_safe_blob_ref` (defence in depth; affects its five callers).
- New `register_run_files(self, execution_id: str, file_urls: Sequence[str], user: User) -> None`: no-op on empty list; decode each URL, raise a client error (existing typed exception, see `.ai-run/guides/development/error-handling.md`) when the token is unsafe, not downloadable (`can_download`, no share token) or its blob is missing; then call `sync_uploaded_files(execution_id, file_urls, user)`. Idempotent per execution id. Database errors propagate.

- [ ] Test-first: yes — with and without a conversation the workspace keyed by the execution id holds the files; empty list creates no workspace; DB error propagates; missing blob raises; foreign-owner and `..` tokens raise; `sync_uploaded_files` skips an unsafe token while still registering a safe one (other callers' accept case).
- [ ] Implement and run the new test file plus `tests/codemie/service/test_agent_workspace_service_tool_calling.py`.

### Task 4: Config cap and router validation

**Files:** Modify `src/codemie/configs/config.py` (near `FILE_DATASOURCE_MAX_UPLOAD_COUNT`, line ~143), `src/codemie/rest_api/routers/workflow_executions.py:~237-340,909`; Test `tests/codemie/rest_api/routers/test_workflow_executions_run_files.py`

Add `WORKFLOW_RUN_FILES_MAX_COUNT: int = Field(default=20, gt=0)`. In the create route, next to `_validate_workflow_supports_files_and_raise`, add a validator that returns 400 (existing `ExtendedHTTPException`) when `file_names` exceeds the cap, or any token is malformed (does not decode), unsafe, or not downloadable via `can_download`. Run before the router sync at line 263. Leave the router sync in place.

- [ ] Test-first: yes — 400 for cap+1 files, malformed token, `..` token, foreign-owner token; 200 path for own-id token; conversation workspace still seeded by the router sync.
- [ ] Implement and run the new file plus `tests/codemie/rest_api/routers/test_workflow_executions*.py`.

### Task 5: Reorder `create_workflow_execution` and register run files

**Files:** Modify `src/codemie/service/workflow_service.py:~228-348`; Test `tests/codemie/service/test_workflow_service.py`

Mint `execution_id` and call `AgentWorkspaceService().register_run_files(execution_id, file_names, user)` (only when `file_names` non-empty) before `_get_or_create_conversation` and `conversation.update()`. Keep the chat-branch sync and `execution_config.save` as they are.

- [ ] Test-first: yes — registration is called once, with and without a conversation, under the execution id, before the conversation save; a registration failure leaves no conversation message and no `workflow_execution_ref` (assert `_get_or_create_conversation`/`conversation.update` not called); empty `file_names` registers nothing; DB error fails creation.
- [ ] Implement and run `tests/codemie/service/test_workflow_service.py`.

### Task 6: Fix `delete_by_execution_id` race

**Files:** Modify `src/codemie/service/assistant/virtual_assistant_service.py:152`; Test `tests/codemie/service/assistant/test_virtual_assistant_service_delete.py`

Use `cls.assistants.pop(key, None)` over the key snapshot instead of `del`.

- [ ] Test-first: yes — force the interleaving (dict subclass or patch deleting a key between snapshot and delete); raises `KeyError` today.
- [ ] Implement and run the test.

### Task 7: Tool-step and scope tests (confirmation, no production change expected)

**Files:** Test `tests/codemie/workflows/test_tool_node_script_step.py`, `tests/codemie/service/script_tool_calls/test_workflow_tool_step_scope.py`, extend `tests/codemie/rest_api/security/test_workflow_context.py`

Seam tests per `.ai-run/guides/testing/testing-patterns.md`. If one fails because of a real gap, fix the minimal production code and note it in the commit.

- [ ] Test-first: no — characterisation of already-wired behaviour. Cover AC 1/3 (tool node passes execution id as conversation, step output is the whole script-tool JSON stored under `output_key`, files changed in the workspace fake persist for a later step), AC 4 (missing script path fails the step with a clear message), AC 5 (failing script fails the step with its output), AC 6 (`ProjectScope` resolves a catalog tool with the running user; unknown or excluded tool errors), AC 7 (assistant step scope is only its assistant's tools, request field never widens).
- [ ] Run the three files.

### Task 8: Running-user lookup and handler-thread tests

**Files:** Test `tests/codemie/workflows/test_tool_node_running_user.py`, `tests/codemie/rest_api/security/test_workflow_context.py`

- [ ] Test-first: yes — a global workflow created by A and run by B gives `ScriptToolRegistry` (via `AgentWorkspaceToolkit._workflow_project()`/`get_tools()`) user B; variant with a pinned `integration_alias` on the step (`ToolNode._owner_user_id` returns the creator) asserts which user the registry holds; webhook/cron run asserts the trigger user; a PROJECT-type settings fixture asserts the documented reach (comment: reference for the follow-up). Fails or characterises per the real behaviour; if the registry holds the creator, fix `tool_service.py`/`tool_node.py` minimally so the running user is used.
- [ ] Test-first: no — AC 10 guard: `get_current_workflow_id()` is visible in a channel handler thread via `job_bridge._request_scoped_handlers` (`copy_context`). Passes on unchanged code.
- [ ] Run both files.

### Task 9: Stale comment

**Files:** Modify `src/codemie/service/script_tool_calls/handler.py:~111`

- [ ] Test-first: no — reword the stale "story 3 adds one" comment to say the alias field is deferred to the future Integration Control for Scripts story. Comment-only.

---

## Self-review notes

- negative-constraints: honored — no `integration_alias`/`CallSettings` (Tasks 7-9 add none, Task 9 only rewords a comment); router sync at `workflow_executions.py:263` kept (Task 4); no registration at resume (Task 5 touches create only); no access check on PROJECT settings (Task 8 asserts the reach as-is); no new error code or authorizer branch; no guards on other workspace routes; `input_files` and attachments untouched; no child-workflow files.
- Spec coverage: AC 1-6 Tasks 5, 7; AC 2 Tasks 3-5; AC 7 Task 7; AC 8 Task 8; AC 9 Tasks 1-4; AC 10 Task 8; AC 11 Task 6.
