# Spec: Download all files from an Agent Workspace (EPMCDME-15770)

Backend only (FastAPI). The UI repo is out of scope; this spec defines the API contract it will consume.

## Goal

One authenticated request returns a single zip containing every file the caller's workspace lists, with folder structure and file names preserved, so a user can retrieve a whole agent or workflow output at once.

## Approach

- New route in `src/codemie/rest_api/routers/agent_workspace.py`, next to `files/download`: `GET /v1/workspaces/{workspace_id}/files/download-all`. It uses `Depends(authenticate)` like its siblings. It returns `fastapi.Response` with `application/zip` and `Content-Disposition: attachment; filename=<workspace name or id>.zip`.
- The archive logic lives in a new module, `src/codemie/service/workspace_archive.py`: the `WorkspaceArchive` result type, the two typed errors, the zip builder, the filename helper and the audit. `AgentWorkspaceService.build_workspace_archive` stays as the public entry point and only delegates to it, which keeps the already large service module cohesive. The module takes the service instance and imports it for type checking only, so there is no import cycle. Authorization is `get_workspace(workspace_id, user)` (owner-only; foreign and missing workspaces are indistinguishable, so there is no existence leak). Files are the same merged set as `get_workspace_input_files` (`agent_workspace_service.py`): DB rows plus virtual conversation uploads, DB rows shadowing virtual ones, reserved paths excluded. The per-blob read and skip-and-warn logic is reused, not duplicated. The existing method's behavior for `execute_workspace_script` must not change.
- The archive is built in memory with stdlib `zipfile` (`ZIP_DEFLATED`) over `BytesIO`. Each entry is `path` as stored, written with `FileObject.bytes_content()`, the same content handling as the single-file download.
- Entry names are re-validated with `workspace_path_rules` (`validate_relative_path`, `is_reserved_workspace_path`). An entry that fails is treated as a skipped file.
- CORS. `X-Workspace-Skipped-Count` is added to the `expose_headers` of the `CORSMiddleware` in `src/codemie/rest_api/main.py`, next to `Content-Disposition`. Without it a browser UI cannot read the header on a cross-origin call, so the partial-archive signal would never reach the user.
- Synchronous GET. There is no job, queue or polling. The AC6 "progress / success / failure" state is the HTTP outcome (distinct status codes and messages below), which the UI renders.

## Behavior

- **Caps.** Two settings in `codemie.configs.config`, per `configuration-patterns.md`: maximum file count and maximum total uncompressed bytes. Choose conservative defaults. The file-count cap is checked on the file list before any blob is read. The byte cap is checked on bytes actually read (virtual rows have `size=0`) and aborts as soon as it is exceeded. Exceeding either cap returns **413** with a message stating the limit and suggesting that the user download individual files.
- **Empty workspace** (no eligible files) returns **400** with an actionable message ("Workspace has no downloadable files"). The service raises a message of this kind; no zip is produced.
- **Partial failure.** Files that cannot be read (missing or unreadable blob) or whose path fails validation are omitted. The archive then includes a text entry `_SKIPPED_FILES.txt` listing the skipped workspace paths, one per line, and the response carries `X-Workspace-Skipped-Count`. If that name collides with a workspace path, a numeric suffix is used. Messages and the manifest contain only workspace-relative paths of the caller's own files. They never contain blob owner or blob name, storage exception text, or anything about other workspaces.
- **All files fail** returns **500** with a generic actionable message ("None of the workspace files could be read"). It never returns an empty zip.
- **Other errors** return **500** with a generic message. The route must not pass `str(exception)` to the client for archive failures, so the blanket `_as_http_error` path must not leak storage details. The 413 and 400 cases must keep their own status codes through that handler.
- **Audit.** Every request logs one structured `logger.info` line with user id, workspace id, included file count, skipped count, total bytes, and outcome (`success`, `partial`, `empty`, `too_large`, `failed`). It also emits the existing monitoring metric helpers (`send_count_metric`, as used in `service/monitoring/*`). There is no new table.
- **Fidelity (AC4).** Blobs are read as `application/octet-stream`, so every backend returns raw bytes (no GCP charset decoding, no filesystem text mode) and entries are byte-exact.

## Acceptance criteria

1. An authenticated owner of a workspace can call `GET /v1/workspaces/{workspace_id}/files/download-all` and receive one `application/zip` response (AC1, AC2).
2. The zip contains every listed workspace file, including virtual uploaded conversation files and files in nested folders, under its stored relative path and file name (AC2, AC3).
3. Entry contents equal the stored bytes, binary and text alike (AC4).
4. A caller who does not own the workspace gets the same response as for a nonexistent workspace, and no archive. Another workspace's files are never included (AC5).
5. Success, empty (400), over-cap (413), partial (200 plus skip report) and total failure (500) are distinguishable by status code and message (AC6, AC7).
6. Unreadable files are omitted and reported by workspace path only, via `_SKIPPED_FILES.txt` and `X-Workspace-Skipped-Count`, and that header is exposed through CORS so browser clients can read it. No blob owner or name, storage error text or foreign-workspace detail appears in any response (AC8).
7. If every file fails, the response is an error and not an empty zip.
8. `download_workspace_file`, `download_file`, `list_files`, `get_workspace_input_files` and the workspace tools behave as before (AC9).
9. One structured audit log line plus a metric is emitted per request with the fields listed above.
10. Automated tests (AC10):
    - authorization (owner, non-owner, missing);
    - archive integrity for binary and text content;
    - nested folders;
    - virtual uploaded files;
    - empty workspace;
    - partial failure;
    - all-fail;
    - file-count and byte caps;
    - entry-name safety (traversal, reserved names);
    - a new router test file for this route, following `testing-api-patterns.md`, since `agent_workspace.py` has none today.

## Non-goals

- UI changes.
- Async or background archive jobs, status polling, or a persisted archive or download link.
- Streaming or temp-file archives.
- Changes to `FileRepository` backends or to byte-exact fidelity of text blobs.
- Changes to existing routes, `can_download` rules, or workspace tools and script execution.
- A new audit table or event store.
- Per-file authorization beyond workspace ownership. Ownership is the existing boundary for workspace routes.
- Selecting a subset of files, glob filters or folder-only download.
- Archive formats other than zip.

## Risks

- The in-memory build holds up to the byte cap plus the compressed copy per request. Defaults must be set conservatively, and concurrent requests multiply memory.
- The UI contract (path, `X-Workspace-Skipped-Count` header, error shapes) needs agreement with the UI team.
- The CORS `expose_headers` change in `main.py` has no automated test, because importing the full app in a unit test is heavy. It is a one-line config edit, verified by review only.
- The router blanket `except Exception` may swallow typed 400/413 errors unless the route is written to preserve them.
