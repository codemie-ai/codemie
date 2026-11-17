# Technical Research

**Task**: agent workspace files download archive zip (EPMCDME-15770)
**Generated**: 2026-10-20
**Research path**: codegraph

---

## 1. Original Context

**Summary:** Enable download of all files from an Agent Workspace
Issue type: Story. Epic: EPMCDME-13292.

**Description**
Users need a convenient way to download the complete contents of an Agent Workspace after an agent or workflow completes its work. Currently, files must be downloaded individually where downloadable links are available, which is inefficient for workspaces containing multiple generated artifacts and folders.

The platform should provide a user-facing action that packages all files the current user is authorized to access in the selected Agent Workspace into a single downloadable archive. This will simplify retrieval, sharing, retention, and local review of complete agent outputs while preserving workspace access boundaries.

**Preconditions**
- A user is authenticated in AI/Run CodeMie.
- The user has access to an Agent Workspace.
- The workspace contains one or more files, optionally organized in folders.
- The workspace is associated with the user's current conversation, agent run, or workflow run.

**Scenarios of Use**
1. A user opens an Agent Workspace that contains files created by an agent or workflow.
2. The user selects **Download all files**.
3. The platform validates the user's access to the workspace and its files.
4. The platform packages eligible workspace files and folders into a single archive.
5. The platform provides a working download for the archive.
6. The user downloads and extracts the archive locally, with the workspace folder structure preserved.
7. If the workspace contains no downloadable files or archive generation fails, the platform shows an actionable message.

**Affected Areas**
- Agent Workspace UI and file-management controls
- Workspace file listing and metadata APIs
- Archive generation and download delivery
- Workspace access control and authorization
- Agent and workflow output handling
- Error handling, audit logging, and automated tests

**Acceptance Criteria (verbatim)**
1. Users with access to an Agent Workspace can initiate a **Download all files** action from the workspace UI.
2. The action generates one downloadable archive containing all files the user is authorized to access in that workspace.
3. The archive preserves the workspace folder hierarchy and original file names.
4. The archive contains binary and text files without altering their contents.
5. The platform does not include files from another user's, conversation's, or workflow run's workspace.
6. The platform displays a clear progress, success, or failure state while the archive is being prepared.
7. If the workspace is empty, the download action is disabled or returns an actionable message.
8. If one or more files cannot be included, the platform reports the failure clearly and does not expose unauthorized file details.
9. Existing individual-file download behavior remains unchanged.
10. Automated tests cover authorization, archive content integrity, nested folders, empty workspaces, and archive-generation failures.

feature_area: agent workspace files download archive zip
Note: backend repo only (FastAPI/Python); UI is a separate repo.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/rest_api/routers/agent_workspace.py` - router `/v1`, tag "Agent Workspace", module-level `workspace_service = AgentWorkspaceService()`. Routes: `POST /workspaces`, `GET /workspaces`, `GET /workspaces/conversations/{conversation_id}`, `GET|PUT|DELETE /workspaces/{workspace_id}/files`, `GET .../files/content`, `GET .../files/download`, `POST .../files/edit`, `GET .../files/grep`, `POST /workspaces/{workspace_id}/execute`. Every route uses `Depends(authenticate)` and wraps the body in `try/except Exception -> _as_http_error` (ValidationException -> 400 "Invalid workspace request"; anything else -> 500 with `details=str(exception)`).
- Single-file download today: `download_workspace_file` (`GET /v1/workspaces/{workspace_id}/files/download?file_path=`) calls `workspace_service.download_file`, returns `fastapi.Response(content=file_object.content, media_type=file_object.mime_type, headers={"Content-Disposition": attachment; filename=<basename>})`. No streaming, whole blob in memory. AC9 requires this stays unchanged.
- `src/codemie/service/agent_workspace_service.py` - `AgentWorkspaceService` (60 callers). Relevant methods:
  - `get_workspace(workspace_id, user)` -> `repository.get_by_id_for_user(workspace_id, user.id)`, raises `ValidationException("Workspace '<id>' not found")` when the workspace is not the user's. This is the ownership boundary (same error for missing and foreign, no existence leak).
  - `list_files(workspace_id, user, glob)` -> DB rows (`repository.list_files`, soft-deleted excluded, ordered by path) plus "virtual" rows from `_get_conversation_uploaded_files(workspace.conversation_id)`. DB rows shadow virtual rows on equal path. Virtual rows (`workspace_id=""`, `checksum=""`, `size=0`, `version=0`) come from `Conversation.history[].file_names`, filtered by `is_safe_blob_ref`, not `is_source_owned_blob_ref`, not reserved path.
  - `get_workspace_input_files(workspace_id, user)` -> `WorkspaceInputFiles(files, warnings)`: the closest existing "read every file of the workspace" routine. For each DB file plus non-shadowed virtual file, `_collect_input_file` reads the blob via `file_repository.read_file(file_name=blob_name, owner=blob_owner, mime_type=...)`, returns `FileObject(name=workspace_file.path, content=...)`; an unreadable blob is skipped with a warning string `"Skipped '<path>': its stored file could not be read."` (logged, not raised). Used by `execute_workspace_script`.
  - `download_file(workspace_id, file_path, user)` -> `get_workspace` + `_get_workspace_file_or_raise` (DB row, then virtual row; else `ValidationException("File '<path>' not found")`) + `file_repository.read_file`.
  - `get_file_content` normalises text -> `str`, binary -> `bytes` (via `_is_binary_workspace_content`); `_grep_file` skips unreadable blobs.
- `src/codemie/repository/agent_workspace_repository.py` - `AgentWorkspaceRepository`: `get_by_id_for_user`, `get_by_conversation_for_user`, `list_for_user`, `list_files(workspace_id, include_deleted=False)`, `get_file`, `find_by_blob`, `soft_delete_file`, `delete_by_execution_id`, `user_references_blob`, `seed_references`.
- `src/codemie/rest_api/models/agent_workspace.py` - SQLModel tables `AgentWorkspace` (`conversation_id`, `user_id`, `name`, `status`), `AgentWorkspaceFile` (`workspace_id`, `path`, `blob_owner`, `blob_name`, `mime_type`, `checksum`, `size`, `version`, `deleted_at`), `AgentWorkspaceFolderOwner`. Response models: `WorkspaceFileItemResponse` (path, mime_type, checksum, size, version, update_date), `WorkspaceFileContentResponse`, etc. A workspace file path is a relative POSIX path, so folders are implicit in `path` (no folder rows other than skill folder-owners).
- Workflow runs key their workspace by execution id used as `conversation_id` (`register_run_files`, `delete_by_execution_id`); the (conversation_id, user_id) pair is unique (`uq_agent_workspaces_conversation_user`). So agent and workflow workspaces are the same table and the same download route applies.
- `src/codemie/service/file_service/download_rules.py::can_download(file_object, user, share_token, file_name_param, workspace_repo)` - rules A to E (owner, workflow schema, MCP images, workspace blob owned by user, D2 source blob referenced by user's own workspace, E share grant). Used by `routers/files.py` `_authorize_file_access` and `validate_run_files`. The per-workspace download route does NOT call it; it relies on workspace ownership only.
- `src/codemie/service/file_service/blob_ref_rules.py` (`is_safe_blob_ref`, `is_source_owned_blob_ref`) and `workspace_path_rules.py` (`validate_relative_path`, `is_reserved_workspace_path`, `MAX_WORKSPACE_PATH_LENGTH`) - path and blob safety predicates already applied when files enter the workspace.
- Storage abstraction: `src/codemie/repository/base_file_repository.py::FileRepository.read_file(file_name, owner, mime_type)` returns `FileObject`; backends `aws_file_repository.py` (S3 `get_object().read()`), `azure_file_repository.py`, `gcp_file_repository.py`, `file_system_repository.py`; obtained with `FileRepositoryFactory.get_current_repository()`. All read the entire blob into memory. No streaming or size-limited read API exists.
- Content type caveats (relevant to AC4 "contents not altered"): `GCPFileRepository.read_file` decodes text-based mime types to `str` with `errors="backslashreplace"` (lossy for non-UTF-8 bytes); `FileSystemRepository.read_file` opens `text/*` in text mode `'r'` (newline translation and decode) and others in `rb`. S3 and Azure return raw bytes. So `FileObject.content` may be `str` or `bytes` depending on backend and mime type. `FileObject` helpers: `bytes_content()`, `string_content()` in `src/codemie_tools/base/file_object.py`.
- No existing zip/archive response in the workspace code path. `skill_file_storage.py` only has zip bundle parsing (`_strip_bundle_wrapper`) for skill uploads, in the inbound direction.

### Architecture and Layers Affected
Router (`rest_api/routers/agent_workspace.py`) -> Service (`service/agent_workspace_service.py`) -> Repository (`repository/agent_workspace_repository.py`, `FileRepository` implementations) -> models (`rest_api/models/agent_workspace.py`). Cross-cutting: `rest_api/security/authentication.py::authenticate`, `ExtendedHTTPException`, logging (`codemie.configs.logger`). Layering follows `.ai-run/guides/architecture/layered-architecture.md`.

### Integration Points
- `FileRepositoryFactory.get_current_repository()` - blob reads (S3/Azure/GCP/filesystem).
- `Conversation.find_by_id` (lazy import in `_get_conversation_uploaded_file_urls`) - source of virtual uploaded files.
- `workflow_executions.py` uses `AgentWorkspaceService` and `register_run_files`; workflow-run workspaces reuse the same service.
- Agent tools (`codemie_tools/data_management/workspace/tools.py`, `BaseWorkspaceTool`) and the script runner consume `list_files`, `download_file`, `get_workspace_input_files`; their behavior is coupled to those service methods.

### Patterns and Conventions
- Routers: sync `def` handlers, `Depends(authenticate)` (both in `dependencies=[...]` and param), blanket `except Exception -> _as_http_error`.
- Services raise `ValidationException` for user-facing errors (mapped to 400); repository access via `self.repository`, blobs via `self.file_repository`.
- Per-item blob read failures are logged with `logger.warning` and skipped with a safe warning (no blob owner/name in the user-facing message) - see `_collect_input_file`, `_grep_file`.
- Audit logging: no dedicated audit facility found in the workspace path; logging is `logger.*` plus monitoring metrics (`send_count_metric`, `send_log_metric` in `service/monitoring/*`). Unresolved whether "audit logging" in the ticket means these or something else.

---

## 3. Documentation Findings

### Guides and Architecture Docs
Guides exist under `.ai-run/guides/` (listing only; not all opened): `api/rest-api-patterns.md`, `api/endpoint-conventions.md`, `architecture/layered-architecture.md`, `architecture/service-layer-patterns.md`, `development/error-handling.md`, `development/security-patterns.md`, `development/logging-patterns.md`, `development/performance-patterns.md`, `testing/testing-patterns.md`, `testing/testing-api-patterns.md`, `testing/testing-service-patterns.md`, `quality-gates.md`. No guide specific to Agent Workspace or archive download.

### Architectural Decisions
Inline comments only: `find_by_blob` "Deliberately no deleted_at filter - see D-4"; `can_download` rule comments; `register_run_files` docstring (ownership is validated at the run-start route). No ADR on workspace archives.

### Derived Conventions
Authorization for workspace routes = `get_by_id_for_user(workspace_id, user.id)`; foreign and missing workspaces are indistinguishable. Response for file bytes uses `fastapi.Response` with `Content-Disposition: attachment`.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/test_agent_workspace_glob.py`, `test_agent_workspace_service_reserved_paths.py`, `test_agent_workspace_service_run_files.py`, `test_agent_workspace_service_missing_blobs.py`, `test_agent_workspace_service_script_run.py`, `test_agent_workspace_service_tool_calling.py`, `test_agent_workspace_service_skill_rows.py` - service behaviors (list/glob, reserved paths, missing blobs, run files).
- `tests/codemie/repository/test_agent_workspace_repository.py` - repository queries.
- `tests/codemie/service/file_service/test_download_rules.py`, `test_workspace_path_parity.py`, `test_workspace_path_rules.py` - authorization and path rules.
- `tests/codemie/rest_api/routers/test_files.py`, `test_workflow_executions_run_files.py` - adjacent routers.
- `tests/codemie_tools/data_management/workspace/*` - workspace tools (glob, image inspect, toolkit, script).

### Testing Framework and Patterns
pytest (8.4.x per `__pycache__`), `unittest.mock`. Service tests commonly build the service with `AgentWorkspaceService.__new__(AgentWorkspaceService)` and assign `MagicMock` `repository` / `file_repository`, or use a `service` + in-memory `repository` fixture (reserved_paths tests). `FileObject(name=..., mime_type=..., owner=..., content=b"...")` is the stock blob stand-in.

### Coverage Gaps
- No test file for `routers/agent_workspace.py` was found (codegraph: `workspace_service` router symbol has "no tests found within 3 caller hops"); the existing single-file download route (`download_workspace_file`) has no router-level test located.
- No archive/zip tests exist (none expected; feature absent).

---

## 5. Configuration and Environment

### Environment Variables
Storage selection and locations in `codemie.configs.config`: `FILES_STORAGE_DIR` (filesystem backend), `FILES_STORAGE_GCP_REGION`, `AWS_S3_REGION`, `AWS_S3_BUCKET_NAME` (names as read in repository code; the storage-type selector variable was not retrieved in this pass). No workspace-specific env var and no existing size or file-count limit found for workspace downloads.

### Configuration Files
`src/codemie/configs/` (`config`, `logger`); `.ai-run/guides/development/configuration-patterns.md` governs adding config.

### Feature Flags and Deployment Concerns
No feature flag found for workspaces. Deployment concerns visible in code: all `read_file` implementations buffer whole blobs; multiple blobs in one request multiplies memory.

---

## 6. Risk Indicators

- Memory and latency: every `FileRepository.read_file` loads the full blob; no size, count or timeout cap exists on workspace reads. Speculative: an in-memory zip of a large workspace in a sync FastAPI handler could exhaust memory or hold a worker; the ticket's "progress state" (AC6) may imply an async or job-style flow, but no job/queue pattern was inspected for workspaces.
- AC4 content integrity vs backend behavior: `GCPFileRepository.read_file` decodes text mime types with `backslashreplace` and `FileSystemRepository.read_file` reads `text/*` in text mode, so bytes can differ from what was stored (`checksum` in `AgentWorkspaceFile` is available to compare, but virtual uploaded rows have `checksum=""`). `FileObject.content` can be `str` or `bytes`.
- Virtual uploaded-file rows (`version=0`, `workspace_id=""`, `size=0`) are listed by `list_files` but are not DB rows; any archive that should match what the UI lists must decide how to treat them. `get_workspace_input_files` already merges both.
- AC5/AC8 authorization: ownership = `get_by_id_for_user`; `can_download` is not applied per file on workspace routes. Blob rows are filtered by `is_safe_blob_ref` / `is_source_owned_blob_ref` only for virtual rows and at registration time.
- AC8 information exposure: `_as_http_error` returns `str(exception)` in `details` for non-validation errors (500); raw storage exceptions could leak blob owner/key if an archive failure reaches that path. `_collect_input_file` shows the safe-warning convention (path only).
- Zip path safety: workspace `path` values are normalised by `validate_relative_path` on write; entry names written to a zip should be re-checked against the same rules (traversal, absolute, reserved names like `codemie_runtime_sdk.py`, which `is_reserved_workspace_path` hides from listings).
- Router-level tests do not exist for `agent_workspace.py`; adding AC10 coverage needs a new router test harness (see `testing-api-patterns.md`).
- "Audit logging" has no existing hook in the workspace path; requirement interpretation is open.
- Speculative: the UI repo is separate; any new endpoint contract (path, response headers, error shape) must be agreed with the UI team.

---

## 7. Summary for Complexity Assessment

The change sits in the existing layered stack of the Agent Workspace feature: one router module (`routers/agent_workspace.py`), one service (`agent_workspace_service.py`), the existing repository and `FileRepository` implementations, with no schema change evident from what already exists (files are rows with a relative POSIX `path`, blobs are in object storage). The closest reusable building blocks are `list_files`/`get_workspace_input_files` (merge DB rows plus virtual uploaded rows, skip-and-warn on unreadable blobs) and `download_file`/`download_workspace_file` (ownership via `get_workspace`, `Response` with `Content-Disposition`). Python stdlib `zipfile` is sufficient; no archive code exists in the workspace path today.

Technical novelty is moderate: it is a new endpoint over well-trodden code, but it is the first multi-blob read in a single request, which exposes unbounded in-memory reads, backend-dependent content decoding (GCP, filesystem text mode) relevant to "contents unaltered", and the virtual-uploaded-file edge case. The UI is out of scope for this repo; the progress/failure state in AC6 and the "does not expose unauthorized file details" requirement in AC8 are the least-specified parts.

Test posture: strong service-level and repository-level tests for workspaces and download rules, with a clear mocking pattern (`__new__` + `MagicMock` repositories, or in-memory repository fixture), but no router tests for `agent_workspace.py` and no existing archive tests. Key risks: memory/latency of buffered reads, byte-fidelity across storage backends, error-detail leakage via `_as_http_error`, and unclear audit-logging expectations.

---

## 8. External References

None named by the task.
