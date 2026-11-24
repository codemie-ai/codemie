# Technical Research

**Task**: workspace conversation files authorization
**Generated**: 2026-11-23
**Research path**: codegraph

---

## 1. Original Context

Ticket EPMCDME-17679 (https://jiraeu.epam.com/browse/EPMCDME-17679)

Summary: [G0.18] L19: a workspace reference reads the conversation files of a different user. Type: Task. Repo: codemie. Risk: High.

Problem: Conversations can have files. The agent workspace endpoints (for example GET /v1/workspaces/conversations/{conversation_id}) accept any conversation id from the caller and sync the file references of that conversation into the caller's workspace. Reads from the workspace then fetch the original file, through the content and download endpoints. There is no check that the conversation belongs to the caller, so any user who knows a conversation id can read the file bytes of a different user.

Required change: A workspace reference to a conversation file gives access only to files of the caller. A reference to the file of a different user gives no file content.

Acceptance Criteria:
1. Given a conversation file of user A, when user B, who has no access to that conversation, references it in the workspace, then no file bytes are returned, by the content endpoint or by the download endpoint.
2. Given a conversation file of the caller, when the caller references it in the workspace, then the reference works as today.
3. A regression test covers these cases, and the test passes in CI.

Outside check (beyond CI): the security verification owner repeats the original exploit on the repaired code. Parent Story: EPMCDME-17676. Design cross-reference: part G0.18, defect L19.

---

## 2. Codebase Findings

### Existing Implementations

Purpose A: a workspace must expose only conversation files the caller may read.

- Workspace ownership gate: `AgentWorkspaceService.get_workspace` (`src/codemie/service/agent_workspace_service.py:305`) checks only `workspace.user_id == caller` (`AgentWorkspaceRepository.get_by_id_for_user`, `src/codemie/repository/agent_workspace_repository.py:65`). It says nothing about the conversation the workspace is keyed to. 13 callers (service, `workspace_archive.py`, workspace tools in `src/codemie_tools/data_management/workspace/tools.py` and `execute_workspace_script_tool.py`).
- Workspace creation: `create_workspace` (`agent_workspace_service.py:168`, REST `POST` in `src/codemie/rest_api/routers/agent_workspace.py:94`) takes any `conversation_id`, saves a row for (conversation_id, caller) (unique per pair), then calls `sync_uploaded_files` with the URLs from `_get_conversation_uploaded_file_urls(conversation_id)` (`:893`: `Conversation.find_by_id`, USER-role messages' `file_names`; no ownership check).
- Registered rows ARE gated: `sync_uploaded_files` (`:196`) skips a ref unless `_user_can_open` (`:281`) passes, which calls `can_download(..., share_token=None, ...)` (`src/codemie/service/file_service/download_rules.py:205`: safe ref; A owner==user.id; B/B2 workflow schema/icon; C MCP images; D own workspace blob; D2 source blob referenced in own workspace; F assistant icon; E share grant needs a token, so never true here). Same gate in `register_generated_files` (`:350`) and `validate_run_files` (`:268`).
- Virtual conversation files are NOT gated: `_get_conversation_uploaded_files(conversation_id)` (`:856`) rebuilds in-memory `AgentWorkspaceFile` rows from the same conversation history on every call, filtering only by `is_safe_blob_ref`, not source-owned, path normalisation, reserved paths. No `can_download`, no conversation ownership check. `blob_owner` is the original uploader (user A). Its consumers (all reached with a workspace that only has to belong to the caller):
  - `_get_workspace_file_or_raise` (`:846`, virtual fallback at `:851`) -> `get_file_content` (`:415`, REST content endpoint `routers/agent_workspace.py:164`), `download_file` (`:492`, REST download `:207`), `get_file_sandbox_url` (`:132`); `edit_file` and workspace tools (e.g. inspect-image tool) go through the same functions. These reads call `file_repository.read_file(file_name=blob_name, owner=blob_owner, ...)` directly.
  - `list_files` (`:398`), `grep_files` (`:567`, reads blob text), `_workspace_source_files` (`:605`) -> `get_workspace_input_files` (`:595`, file bytes handed to a script run, results exportable), `register_generated_files` dedupe only (`:340`).
- Archive is gated by a different mechanism: `_ensure_conversation_readable` (`src/codemie/service/workspace_archive.py:157`), called only from `_zip_workspace_files` (`:115`). Uses `Ability(user).can(Action.READ, conversation)`; raises `ValidationException("Workspace '<id>' not found")`. Skips the check when `Conversation.find_by_id` returns None. Docstring names the same root cause ("`create_workspace` accepts any conversation id"). Ability rule for Conversation READ: `Role.SHARED_WITH`, `Role.OWNED_BY` (`src/codemie/core/ability.py:148`); `Conversation.is_owned_by` is `user_id == user.id` (`rest_api/models/conversation.py:940`).
- Read of existing blob rows by the owning user: `GET .../files/content` and `.../files/download` for DB rows (`blob_owner` such as `workspace-<id>` or the original owner) have no per-read `can_download` re-check either (`get_file_content`, `download_file` read the stored `blob_owner`/`blob_name` as given).
- Distance from purpose: registration path and archive path enforce a check; list/content/download/grep/sandbox-url/script-input virtual path enforce none.

Other touched code: `src/codemie/service/file_service/blob_ref_rules.py` (`is_safe_blob_ref:62`, `is_source_owned_blob_ref:77`); `src/codemie/repository/agent_workspace_repository.py` (`find_by_blob`, `user_references_blob:211`); router `src/codemie/rest_api/routers/agent_workspace.py`. `GET /workspaces/conversations/{conversation_id}` (`routers/agent_workspace.py:120`) calls `get_workspace_by_conversation` (`service:311`), which only returns an EXISTING workspace for (conversation, caller) and does no sync.

### Decisions Already in the Code
- Conversation uploads are served as virtual rows from the conversation history instead of persisted rows (`:856` docstring: "not persisted ... reads are served directly ... without copying"). Alternative: persisted rows only. Reason recorded: avoid copying content. No access reason recorded.
- Row registration authorises by blob (`can_download`, comment at `:221` and `:281`: "a row grants its blob (workspace reads, share rule E), so only a blob the user may already open gets one"). The archive authorises by conversation (`Ability` READ). Two conventions for the same question. The `:281` docstring shows the design assumes a row implies the blob is openable; the virtual path bypasses that assumption.
- Not-found disguise convention: unauthorised access returns `ValidationException("Workspace '<id>' not found")` (archive, `get_workspace`), router maps via `_as_http_error` (`routers/agent_workspace.py:53`); `test_download_all_foreign_workspace_looks_like_a_missing_one` encodes it.
- Fail-open when the conversation does not exist (`workspace_archive.py:165`). Workflow runs key a workspace by execution id (`register_run_files` -> `sync_uploaded_files(execution_id, ...)`, `:245`), for which `Conversation.find_by_id` is None.
- Data direction: authorisation is stored per (workspace, user) but file ownership is on `blob_owner` in the token; the virtual path takes `blob_owner` from message history, not from the caller.

### Task Statements Checked Against the Code
- "accept any conversation id from the caller and sync ... into the caller's workspace": holds for `POST create_workspace` / `create_workspace` (`:168`, `:189`). The cited example `GET /v1/workspaces/conversations/{id}` does not sync (`:311`); it returns an existing workspace only. Partially contradicted for that endpoint.
- "sync the file references": partially contradicted. `sync_uploaded_files` skips refs the caller cannot open (`_user_can_open`). Content reaches B through virtual rows (`:856`), not synced rows. Verified by reading; no executed repro.
- "reads ... through the content and download endpoints": holds (`get_file_content`, `download_file` -> `_get_workspace_file_or_raise` virtual fallback).
- "no check that the conversation belongs to the caller": holds for everything except `build_workspace_archive` (`_ensure_conversation_readable` exists; the ticket does not mention it).
- AC2 "works as today": the caller's own files satisfy `can_download` Rule A (`owner == user.id`) and `Conversation.is_owned_by`. Shared-conversation recipients: Ability READ includes SHARED_WITH; `can_download` for them needs a share token (Rule E), unavailable in this path. Keeps-in-place mechanisms: none stated by the task.

### Consumers of Changed Contracts
- `_get_conversation_uploaded_files`: 7 callers, all in `agent_workspace_service.py`: lines 340, 398, 567, 611, 851 (listed above), plus internal. Tests that stub it: `test_agent_workspace_service_reserved_paths.py`, `test_agent_workspace_glob.py`, `test_agent_workspace_service_download_archive.py` (fixture `service`), `test_workspace_glob_tools.py`.
- `_workspace_source_files`: `get_workspace_input_files` (`:595`), `workspace_archive.py:116`.
- `_get_workspace_file_or_raise`: `get_file_content`, `download_file`, `get_file_sandbox_url`, `edit_file`.
- `get_file_content`/`download_file` callers: REST routes, workspace tools (`tools.py`, inspect-image tool, script tool via `get_workspace_input_files`), `execute_workspace_script` runs.
- `sync_uploaded_files`: 13 callers (`conversation_service.py`, `workflow_service.py`, `workflow_executions.py`, `assistant_handlers.py`, others) - these pass the caller's own conversation/execution id.
- `can_download`: 37 callers (files router, assistant icon service, workspace service); `tests/codemie/service/file_service/test_download_rules.py`.
- `_ensure_conversation_readable`: one caller (`workspace_archive.py:115`).

### Architecture and Layers Affected
Router (`routers/agent_workspace.py`) -> service (`AgentWorkspaceService`, `workspace_archive.py`) -> authorisation rules (`download_rules.py`, `core/ability.py`) -> repository (`agent_workspace_repository.py`, file repository `read_file`). Agent tools layer (`codemie_tools/data_management/workspace/*`) reuses the service.

### Integration Points
`Conversation` model (Postgres/SQL, `find_by_id`); `FileRepositoryFactory` file storage (owner-prefixed blobs); `SharedConversation` (download Rule E); `Ability`.

### Patterns and Conventions
Thin routers with `_as_http_error`; service raises `ValidationException`; tests build service via `AgentWorkspaceService.__new__` with `MagicMock` repository/file_repository.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/` present (security-patterns.md, service-layer-patterns.md, testing-service-patterns.md, testing-api-patterns.md); security-patterns.md has no workspace-specific entry.

### Architectural Decisions
Recorded in code comments only: `workspace_archive.py:157` docstring, `download_rules.py:174-181`, `agent_workspace_service.py:281`.

### Derived Conventions
Ownership-disguise as "not found"; reads of workspace blobs go through `file_repository.read_file(owner=blob_owner, ...)` with owner from the stored row.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/test_agent_workspace_service_download_archive.py`: archive; autouse fixture patches `Conversation.find_by_id` and `workspace_archive.Ability`; `test_build_workspace_archive_rejects_unreadable_conversation` (`:354`) is the nearest existing negative test.
- `tests/codemie/rest_api/routers/test_agent_workspace_download_all.py`: router with mocked service; `test_download_all_foreign_workspace_looks_like_a_missing_one`.
- `tests/codemie/service/test_agent_workspace_service_reserved_paths.py`: `_FakeRepository` + `__new__` service; `TestVirtualUploadsSkipReservedNames` covers `_get_conversation_uploaded_files` filtering; patches `_get_conversation_uploaded_file_urls`.
- `test_agent_workspace_service_run_files.py` (`validate_run_files`, `can_download` path), `test_agent_workspace_service_artifacts.py`, `test_agent_workspace_glob.py`, `tests/codemie/service/file_service/test_download_rules.py`, `test_blob_ref_rules.py`, `tests/codemie/repository/test_agent_workspace_repository.py`.

### Testing Framework and Patterns
pytest, `MagicMock`/`patch`, in-memory fake repository, `TestClient`-style router tests with service mocked; no real-Postgres tests in this area.

### Coverage Gaps
No test found for `get_file_content` / `download_file` with a virtual conversation file belonging to another user, nor `list_files`/`grep_files`/`get_workspace_input_files` with a foreign conversation. No router-level test for the content endpoint (`routers/agent_workspace.py:164`) or `POST create_workspace` with a foreign conversation id. No test for `_ensure_conversation_readable` on a missing conversation.

---

## 5. Configuration and Environment

### Environment Variables
`WORKSPACE_DOWNLOAD_MAX_FILES` (200), `WORKSPACE_DOWNLOAD_MAX_BYTES` (104857600) in `src/codemie/configs/config.py:676-678`; none govern authorisation.

### Configuration Files
None relevant.

### Feature Flags and Deployment Concerns
None found. Sandbox/script runner config is separate (`WORKSPACE_SCRIPT_*`).

---

## 6. Risk Indicators

- Virtual conversation rows (`agent_workspace_service.py:856`) bypass `can_download`; many read surfaces share it (content, download, sandbox url, list, grep, script input) - one gate vs per-endpoint gates is a spec decision.
- Two authorisation mechanisms for one purpose: blob-based `can_download` (registration) and conversation-based `Ability` READ (archive only); see "Existing Implementations".
- `_ensure_conversation_readable` fails open when the conversation is missing; workflow execution-id workspaces have no Conversation row.
- Shared-conversation recipients: Ability READ grants SHARED_WITH; `can_download` without share token denies. AC2/AC1 wording ("no access to that conversation") does not say which applies.
- Agent tools (script runner, inspect image, edit) share the same read functions; behaviour change affects agent runs, not only REST.
- Test gap: mock-heavy fixtures stub `_get_conversation_uploaded_files`, so existing tests would not catch the regression.
- Ticket's example endpoint (`GET .../conversations/{id}`) does not itself sync; reproduce via `POST create_workspace` then content/download (unverified by execution).
- Speculative: repository file rows for foreign blobs created before any fix (earlier syncs) cannot be found from here; only the sync path is gated so such rows would be refused at registration.

---

## 7. Summary for Complexity Assessment

The vulnerability sits in `AgentWorkspaceService` (one large service file) and touches one adjacent module, `workspace_archive.py`. Workspace ownership is checked per caller, but the conversation it is keyed to is not. `create_workspace` accepts any conversation id. Registered file rows are filtered with `can_download`, but virtual rows rebuilt from conversation history (`_get_conversation_uploaded_files`) are served with no check through content, download, sandbox URL, list, grep and script-input paths. Only the archive endpoint already has a conversation READ check (`_ensure_conversation_readable`).

Change surface is the service layer plus one or two tests: no schema, config, or router signature change is evidenced; the router maps `ValidationException` already. Technical novelty is low (two existing authorisation patterns to follow). The risk is breadth: ~7 internal callers of the virtual-file builder and the agent tools that reuse the read functions.

Test posture is mock-based and close to the needed shape (archive negative test, `_FakeRepository` fixture), but nothing covers the foreign-conversation read on content/download/list/grep. Key risks: choosing which check to apply (blob vs conversation), shared conversations, the fail-open on missing conversations, and workflow execution-id workspaces.

---

## 8. External References

None named by the task (the Jira URL was not fetched; the ticket text was provided verbatim in Section 1).
