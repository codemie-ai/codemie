# EPMCDME-17679 Workspace Conversation-File Authorisation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A workspace keyed to a conversation the caller cannot read serves no conversation-file bytes on any read surface.

**Architecture:** One central gate in `AgentWorkspaceService.get_workspace` (the entry of every content, download, sandbox-url, list, grep, script-input, edit and tool path) reusing the existing conversation `Ability` READ check from `workspace_archive.py`. Failure raises the existing `ValidationException("Workspace '<id>' not found")`, which the router already maps through `_as_http_error` to HTTP 400 "Invalid workspace request" (the same response a missing workspace gets). Service layer plus tests only.

**Tech Stack:** Python, pytest, `unittest.mock`.

**Spec:** none (inline ticket EPMCDME-17679); research in `technical-analysis.md` next to this file.

Commit per task using the repository's existing convention.

## Acceptance criteria

- A conversation file of user A, referenced in the workspace by user B who cannot read that conversation: content and download endpoints return no bytes (the same HTTP 400 "Invalid workspace request" a missing workspace gets).
- A conversation file of the caller referenced in the caller's workspace is served as today (content, download).
- Regression tests cover both cases and pass.

## Global Constraints

- Reuse the `Ability(user).can(Action.READ, conversation)` rule of `_ensure_conversation_readable` (`src/codemie/service/workspace_archive.py:157`). No third mechanism. In practice only the conversation owner passes it: `Conversation.is_shared_with` always returns False (`conversation.py:946`) and Conversation READ is granted only to SHARED_WITH and OWNED_BY (`ability.py:149`), so admins do not pass either. Share links do not go through `Ability`; they use the share token and download rule E (`download_rules.py`), which this change does not touch.
- Missing `Conversation` row (workflow execution-id workspaces) keeps today's behaviour (fail-open).
- Foreign conversation gives the same `Workspace '<id>' not found` error (HTTP 400 via `_as_http_error`) as a missing workspace (no existence leak).
- No schema, config or router-signature change; no code comments; no feature docs.
- Tests are mock-based like neighbours; no real-Postgres tests.

## Assumptions recorded

- `create_workspace` is not itself gated: it only registers refs passing `can_download` (already enforced) and callers pass `User | UserEntity`, which `Ability` does not accept for the latter. Every later read of the created workspace is refused by the `get_workspace` gate.
- Added cost: one `Conversation.find_by_id` per `get_workspace` call.

## Review Focus

- Shared conversations via share links are unaffected: they use the share token and download rule E, not `Ability`; no test claims a non-owner `Ability` pass.
- Missing Conversation row (workflow execution id): still works (Task 1 test).
- Foreign conversation on list, grep, script-input and sandbox-url, not only content/download (Task 1 tests).
- Archive endpoint still rejects an unreadable conversation after its duplicate call is removed (existing test, Task 2).

---

### Task 1: Gate `get_workspace` on conversation READ

**Files:**
- Modify: `src/codemie/service/workspace_archive.py:115,157` (make the check public, drop the duplicate call)
- Modify: `src/codemie/service/agent_workspace_service.py:60,305-315`
- Test: `tests/codemie/service/test_agent_workspace_conversation_access.py` (create)

**Interfaces:**
- Produces: `ensure_conversation_readable(workspace: AgentWorkspace, user: User) -> None` in `codemie.service.workspace_archive`, same body and error as today's `_ensure_conversation_readable`.

- [ ] **Step 1: Write the failing tests** in the new file, using the `_FakeRepository` / `AgentWorkspaceService.__new__` fixture style of `test_agent_workspace_service_reserved_paths.py:47-93`, but WITHOUT patching `_get_conversation_uploaded_file_urls` or `_get_conversation_uploaded_files`. Patch `codemie.rest_api.models.conversation.Conversation.find_by_id` to return a `SimpleNamespace(history=[SimpleNamespace(role=ChatRole.USER, file_names=[FileObject(name="a.txt", mime_type="text/plain", owner="user-a").to_encoded_url()])])` and `codemie.service.workspace_archive.Ability` (as in `test_agent_workspace_service_download_archive.py:63-70`). Caller is `User(id="user-b")`. Tests:
  - `test_foreign_conversation_content_and_download_refused`: `Ability.return_value.can.return_value = False`; `get_file_content` and `download_file` raise `ValidationException` matching `Workspace 'ws-1' not found`; `service.file_repository.read_file.assert_not_called()`.
  - `test_foreign_conversation_other_read_surfaces_refused`: same setup; `get_file_sandbox_url`, `list_files`, `grep_files`, `get_workspace_input_files`, `get_workspace_by_conversation` all raise the same error; `read_file` not called.
  - `test_own_conversation_file_is_served`: `can` returns True; `get_file_content` returns the stub bytes and `read_file` is called with `owner="user-a"`'s blob (caller owns the conversation, as today); `download_file` also succeeds.
  - `test_gate_allows_whatever_ability_allows`: `can` returns True while the blob owner differs from the caller's id; `get_file_content` succeeds (pins that the gate delegates to `Ability` only; makes no claim about a real non-owner scenario).
  - `test_missing_conversation_keeps_working`: `find_by_id` returns `None`, `Ability` not consulted; `get_workspace("ws-1", user)` returns the workspace.
- [ ] **Step 2: Run** `poetry run pytest tests/codemie/service/test_agent_workspace_conversation_access.py -v`. Expected: the foreign-conversation tests FAIL (bytes returned), the others PASS.
- [ ] **Step 3: Rename `_ensure_conversation_readable` to `ensure_conversation_readable`** in `workspace_archive.py:157` and delete its call at `:115` (the service now performs it inside `get_workspace`, which `_zip_workspace_files` calls on the line above). Keep the `Ability` import there so the existing archive test's patch target still applies.
- [ ] **Step 4: Gate `get_workspace`** (`agent_workspace_service.py:305`): after the not-found check call `ensure_conversation_readable(workspace, user)` before returning. Import it from `codemie.service.workspace_archive` on the existing import line (`:60`). In `get_workspace_by_conversation` (`:311`), call the same function on the found workspace before building the response.
- [ ] **Step 5: Run** the new test file again. Expected: all PASS.

Test-first: yes — `test_foreign_conversation_content_and_download_refused` fails today because the virtual conversation row is served with no check.

### Task 2: Repair existing tests under the new gate

**Files:**
- Modify (only where failing): known candidates are `tests/codemie/service/test_agent_workspace_service_reserved_paths.py`, `tests/codemie/service/test_agent_workspace_glob.py`, `tests/codemie/service/test_agent_workspace_service_run_files.py`, `tests/codemie/service/test_agent_workspace_service_artifacts.py`, `tests/codemie_tools/data_management/workspace/test_workspace_glob_tools.py`, `tests/codemie/service/test_artifact_publisher.py`, `tests/codemie/service/assistant/test_create_subagent_executors.py`, `tests/codemie/rest_api/handlers/test_tool_call_resume_restore.py`, `tests/codemie/workflows/test_tool_node_script_step.py`, `tests/codemie/rest_api/routers/test_workflow_executions_run_files.py`, plus the workspace toolkit, image and script tool tests under `tests/codemie_tools/`, and any other test under `tests/` that fails in Step 1. Locate each with `Glob`; do not rely on the paths above alone.

- [ ] **Step 1: Run the whole suite, not a `-k` subset**: `poetry run pytest tests/codemie tests/codemie_tools -q` (the repo's `make test` runs `pytest tests/`; use it if the run is practical). A `-k workspace` filter misses tests that reach the real service under other names, and `tests/codemie_tools/` must be included. List failures. Expected failures are tests that call the real `get_workspace` and now reach the unpatched `Conversation.find_by_id`.
- [ ] **Step 2: Fix each failure the least invasive way**: add an autouse fixture patching `codemie.rest_api.models.conversation.Conversation.find_by_id` (return `None`, or a mock with `codemie.service.workspace_archive.Ability` allowing READ where the test serves conversation files) in that module, mirroring `readable_conversation` in `test_agent_workspace_service_download_archive.py:63-70`. Do not weaken assertions.
- [ ] **Step 3: Re-run** `poetry run pytest tests/codemie tests/codemie_tools -q` (or `make test`). Expected: all PASS, including `test_build_workspace_archive_rejects_unreadable_conversation`.

Test-first: no — adapts existing fixtures to the new gate; behaviour is pinned by Task 1 tests.

## Negative-constraint pass

- "A reference to a different user's file gives no file content": honoured by Task 1 gate on every read path.
- Constraints stated as non-goals (no schema/config/router change, no comments, no docs, no third mechanism): no task touches them; the gate reuses the archive's `Ability` rule.
- Fail-open for a missing Conversation row is kept deliberately (Task 1 `test_missing_conversation_keeps_working`).
