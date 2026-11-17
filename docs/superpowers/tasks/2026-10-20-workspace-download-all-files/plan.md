# Workspace "Download all files" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `GET /v1/workspaces/{workspace_id}/files/download-all` returns one zip of every file the caller's workspace lists.

**Architecture:** The archive logic lives in a new `src/codemie/service/workspace_archive.py` module (types, typed errors, zip builder, filename helper, audit), and `AgentWorkspaceService.build_workspace_archive` is a thin delegate to it, so the large service module is not grown further. The builder reuses the merged DB + virtual-upload file set and the per-blob skip-and-warn read (`_collect_input_file`), builds the zip in memory with stdlib `zipfile`, and raises typed errors. A new sync route maps those errors to 400/413/500 without leaking `str(exception)`.

**Tech Stack:** FastAPI, SQLModel repos, stdlib `zipfile`/`io`, pytest + `unittest.mock`.

**Spec:** `/home/siarhei/projects/codemie-dev/codemie/docs/superpowers/tasks/2026-10-20-workspace-download-all-files/spec.md` (research: `technical-analysis.md` in the same dir).

Commit per task using the repository's existing convention (see `.ai-run/guides/standards/git-workflow.md`).

## Global Constraints

- Authorization is `get_workspace(workspace_id, user)` only; foreign and missing workspaces give the same response.
- Archive built in memory, synchronous GET; no job, queue, temp file, streaming, new table.
- Entry name = stored `path`; entry bytes = `FileObject.bytes_content()` of what `read_file` returns.
- Status codes: empty 400 ("Workspace has no downloadable files"), over-cap 413 (message states the limit, suggests downloading individual files), all-files-fail 500 ("None of the workspace files could be read"), other failures 500 generic. Partial = 200 + `_SKIPPED_FILES.txt` + `X-Workspace-Skipped-Count`.
- Responses and the manifest carry workspace-relative paths only; never blob owner/name, storage exception text, or other workspaces' data.
- `download_workspace_file`, `download_file`, `list_files`, `get_workspace_input_files`, workspace tools and `_as_http_error` behavior unchanged.
- Audit: one `logger.info` line per request (user id, workspace id, included count, skipped count, total bytes, outcome in `success|partial|empty|too_large|failed`) plus `BaseMonitoringService.send_count_metric`.
- Only run tests/lint scoped to touched files; whole-suite gates are owned by the calling flow.

## Review Focus

- A workspace file whose stored path contains `..`, is absolute, or is reserved (e.g. `codemie_runtime_sdk.py`): skipped and reported, never written as a zip entry (Task 1).
- A DB file and a virtual upload with the same path: exactly one entry (DB row wins) (Task 1).
- Workspace name containing quotes, slashes or non-ASCII: `Content-Disposition` filename stays header-safe (Task 1 filename helper, Task 3 header test).
- A workspace path literally named `_SKIPPED_FILES.txt`: manifest gets a numeric suffix, workspace file untouched (Task 1).
- An unexpected exception whose text contains a blob key reaches the route: response body must not contain it (Task 3).

---

### Task 1: Archive builder in the service

Test-first: yes — `test_build_workspace_archive_*` in a new service test file fail with `AttributeError` (no `build_workspace_archive`).

**Files:**
- Modify: `src/codemie/configs/config.py:~579` (next to the `WORKSPACE_SCRIPT_*` settings)
- Create: `src/codemie/service/workspace_archive.py` (the archive builder; takes the service instance, imports `AgentWorkspaceService` under `TYPE_CHECKING` only to avoid an import cycle)
- Modify: `src/codemie/service/agent_workspace_service.py` (thin `build_workspace_archive` delegate after `get_workspace_input_files`; extract the `_workspace_source_files` helper from that method)
- Test: `tests/codemie/service/test_agent_workspace_service_download_archive.py`

**Interfaces:**
- Produces (module level in `workspace_archive.py`):
  - `class WorkspaceArchive(NamedTuple)`: `content: bytes`, `filename: str`, `included_count: int`, `skipped_paths: list[str]`, `total_bytes: int`
  - `class WorkspaceArchiveTooLargeError(Exception)` (message is client-safe, states the limit)
  - `class WorkspaceArchiveFailedError(Exception)` (message is client-safe)
  - `build_workspace_archive(service: AgentWorkspaceService, workspace_id: str, user: User) -> WorkspaceArchive`
- Produces (in `agent_workspace_service.py`): `AgentWorkspaceService.build_workspace_archive(self, workspace_id: str, user: User) -> WorkspaceArchive`, which only calls the module function above.
- Config: `WORKSPACE_DOWNLOAD_MAX_FILES: int = 200`, `WORKSPACE_DOWNLOAD_MAX_BYTES: int = 104857600` (100 MiB).

- [ ] **Step 1: Write failing tests** using the `AgentWorkspaceService.__new__` + `MagicMock` `repository`/`file_repository` pattern (see `tests/codemie/service/test_agent_workspace_service_missing_blobs.py`); `FileObject(name=..., mime_type=..., owner=..., content=b"...")` as blob stand-in. Open the produced `content` with `zipfile.ZipFile(BytesIO(...))`. Cases, each one test:
  - integrity: binary (`b"\x00\xff\x01"`) and text entries equal the bytes returned by `read_file`; a `str` content is encoded to bytes.
  - nested folders: `a/b/c.txt` and `top.txt` keep their names.
  - virtual upload row (patch `_get_conversation_uploaded_files`) is included; a virtual row with the same path as a DB row yields one entry with the DB content.
  - non-owner / missing: `repository.get_by_id_for_user` returns `None` -> `ValidationException`, `file_repository.read_file` never called.
  - empty: no files -> `ValidationException` with "Workspace has no downloadable files".
  - file-count cap: `monkeypatch` `config.WORKSPACE_DOWNLOAD_MAX_FILES=2`, 3 files -> `WorkspaceArchiveTooLargeError`, `read_file` not called.
  - byte cap: `config.WORKSPACE_DOWNLOAD_MAX_BYTES=10`, virtual rows have `size=0`, second read pushes total past 10 -> `WorkspaceArchiveTooLargeError` and no further `read_file` call.
  - partial failure: one `read_file` raises `RuntimeError("s3://bucket/owner/secret-key")` -> archive has the good file plus `_SKIPPED_FILES.txt` listing only the workspace path, `skipped_paths == [path]`, and the manifest text does not contain `secret-key`.
  - manifest collision: a workspace file at `_SKIPPED_FILES.txt` plus one failing file -> manifest entry is `_SKIPPED_FILES_1.txt`, original entry intact.
  - all fail: every read raises -> `WorkspaceArchiveFailedError("None of the workspace files could be read")`, no archive.
  - entry-name safety: rows with path `../evil.txt`, `/abs.txt`, `codemie_runtime_sdk.py` are skipped (listed in `skipped_paths`, `read_file` not called for them) while a normal sibling is kept.
  - `filename`: workspace name `My "ws"/é` -> only `[A-Za-z0-9._-]` characters plus `.zip`; blank name falls back to the workspace id.
- [ ] **Step 2: Run to verify failure:** `pytest tests/codemie/service/test_agent_workspace_service_download_archive.py -v` -> FAIL (`AttributeError`/import error).
- [ ] **Step 3: Add the two settings** to `config.py` with a one-line comment each (per `.ai-run/guides/development/configuration-patterns.md`).
- [ ] **Step 4: Extract `_workspace_source_files(self, workspace: AgentWorkspace) -> list[AgentWorkspaceFile]`** from `get_workspace_input_files` (DB rows from `repository.list_files`, then virtual rows not shadowed by an equal DB path, same order) and make `get_workspace_input_files` iterate it. Behavior must be identical.
- [ ] **Step 5: Implement the archive builder** in `workspace_archive.py` (the service method delegates to it), in this order: `get_workspace`; `_workspace_source_files`; empty -> `ValidationException("Workspace has no downloadable files")`; count > `config.WORKSPACE_DOWNLOAD_MAX_FILES` -> `WorkspaceArchiveTooLargeError` (before any read); for each file, validate the name with `validate_relative_path(path, MAX_WORKSPACE_PATH_LENGTH)` and `is_reserved_workspace_path` (failure or `ValueError` -> skipped, no read; the validated normalized name must equal the stored `path`, otherwise skip); read through the existing `_collect_input_file(wf, collected, warnings)` (a call that appends nothing means skipped); add `bytes_content()` to a `zipfile.ZipFile(BytesIO, "w", ZIP_DEFLATED)`; keep a running total and raise `WorkspaceArchiveTooLargeError` as soon as it exceeds `WORKSPACE_DOWNLOAD_MAX_BYTES`. Nothing included -> `WorkspaceArchiveFailedError("None of the workspace files could be read")`. Skipped paths present -> write `_SKIPPED_FILES.txt` (numeric suffix `_SKIPPED_FILES_1.txt`, `_2`... on collision), one workspace path per line. Add a docstring note on the AC4 limitation (GCP `backslashreplace`, filesystem text-mode read; S3/Azure byte-exact; same as single-file download). A small module-level `_archive_filename(workspace)` builds the header-safe name.
- [ ] **Step 6: Run to verify pass:** the new file plus `pytest tests/codemie/service/test_agent_workspace_service_run_files.py tests/codemie/service/test_agent_workspace_service_missing_blobs.py tests/codemie/service/test_agent_workspace_service_script_run.py -v` -> PASS.
- [ ] **Step 7: Commit.**

---

### Task 2: Audit log and metric

Test-first: yes — `test_build_workspace_archive_audit_*` assert one log line and one metric per outcome; fail because nothing is logged or emitted.

**Files:**
- Modify: `src/codemie/service/workspace_archive.py` (the builder from Task 1)
- Test: `tests/codemie/service/test_agent_workspace_service_download_archive.py` (append)

**Interfaces:**
- Consumes: `build_workspace_archive`, `WorkspaceArchive`, the two typed errors from Task 1.
- Produces: module-level `_audit_archive(user: User, workspace_id: str, progress: _ArchiveProgress) -> None` in `workspace_archive.py`, where `_ArchiveProgress` is a small dataclass (`outcome`, `included`, `skipped`, `total_bytes`) that the zip worker updates as it goes; metric name constant `WORKSPACE_ARCHIVE_METRIC = "workspace_archive_download"` with attributes `{"outcome": ..., "user_id": ...}` via `BaseMonitoringService.send_count_metric` (`codemie.service.monitoring.base_monitoring_service`).

- [ ] **Step 1: Write failing tests** (patch `logger` in the `codemie.service.workspace_archive` module and `BaseMonitoringService.send_count_metric`): `success` (all included, 0 skipped), `partial`, `empty`, `too_large` (both caps), `failed` (all-fail, non-owner `ValidationException`, and an unexpected `RuntimeError` from the zip step). Each asserts exactly one `logger.info` call whose text contains user id, workspace id, included, skipped, total bytes and the outcome, and exactly one metric call with that outcome. Also: a raising `send_count_metric` must not change the result (success still returned).
- [ ] **Step 2: Run to verify failure:** `pytest tests/codemie/service/test_agent_workspace_service_download_archive.py -k audit -v` -> FAIL.
- [ ] **Step 3: Implement `_audit_archive`** (log, then metric inside `try/except Exception` that only `logger.warning`s) and call it exactly once on every exit of `build_workspace_archive`: the Task 1 body becomes a private worker `_zip_workspace_files(service, workspace_id, user, progress)` that records `outcome`/counts in the `_ArchiveProgress`, and the public function calls `_audit_archive` from a `finally` block (the public signature does not change). Typed errors and `ValidationException` re-raise unchanged; any other exception is audited `failed` and re-raised.
- [ ] **Step 4: Run to verify pass:** whole new test file -> PASS.
- [ ] **Step 5: Commit.**

---

### Task 3: Route, error mapping and router tests

Test-first: yes — new router tests call `download_all_workspace_files`, which does not exist yet.

**Files:**
- Modify: `src/codemie/rest_api/routers/agent_workspace.py` (new route after `download_workspace_file`, line ~196; new helper beside `_as_http_error`, line ~51)
- Modify: `src/codemie/rest_api/main.py` (add `X-Workspace-Skipped-Count` to the `CORSMiddleware` `expose_headers`, next to `Content-Disposition`; no automated test, config edit verified by review)
- Modify: `tests/codemie/rest_api/router_guards/baselines/openapi.json` (regenerate the OpenAPI router-guard baseline; the diff is only the new path)
- Modify: `.ai-run/guides/api/rest-api-patterns.md` (short subsection, see Step 5)
- Test: `tests/codemie/rest_api/routers/test_agent_workspace_download_all.py` (new; no router test exists for this module)

**Interfaces:**
- Consumes: `workspace_service.build_workspace_archive(workspace_id, user) -> WorkspaceArchive`; `WorkspaceArchive`, `WorkspaceArchiveTooLargeError` and `WorkspaceArchiveFailedError` imported from `codemie.service.workspace_archive` (Task 1).
- Produces: `download_all_workspace_files(workspace_id: str, user: User = Depends(authenticate)) -> Response` at `GET /workspaces/{workspace_id}/files/download-all`; `_archive_http_error(exception: Exception) -> ExtendedHTTPException`.

- [ ] **Step 1: Write failing tests.** Follow `.ai-run/guides/testing/testing-api-patterns.md` and the direct-call style of `tests/codemie/rest_api/routers/test_workflow_executions_run_files.py`: `patch("codemie.rest_api.routers.agent_workspace.workspace_service")`, call the function with `User(id="u1", username="u", name="u")`. Cases:
  - success: `Response` with `media_type == "application/zip"`, `Content-Disposition == 'attachment; filename="<archive filename>"'`, body equals `content`, no `X-Workspace-Skipped-Count` header.
  - partial: header `X-Workspace-Skipped-Count` equals `len(skipped_paths)` as a string.
  - `ValidationException("Workspace has no downloadable files")` -> `ExtendedHTTPException` code 400 (message via existing `_as_http_error`); non-owner `ValidationException` maps to the identical 400 as a missing workspace.
  - `WorkspaceArchiveTooLargeError("...limit...")` -> code 413, message text preserved in `details`.
  - `WorkspaceArchiveFailedError("None of the workspace files could be read")` -> code 500, that text in `details`.
  - `RuntimeError("s3://bucket/owner/secret-key")` -> code 500 and `"secret-key"` not in `message`, `details` or `help`.
  - existing `download_workspace_file` still returns its raw `Response` unchanged (one regression test, AC9).
  - route registration: the router has a `GET` route with path `/v1/workspaces/{workspace_id}/files/download-all`.
- [ ] **Step 2: Run to verify failure:** `pytest tests/codemie/rest_api/routers/test_agent_workspace_download_all.py -v` -> FAIL (`ImportError`).
- [ ] **Step 3: Implement `_archive_http_error`:** `ValidationException` -> `_as_http_error(exception)`; `WorkspaceArchiveTooLargeError` -> 413 with the exception text in `details` and a help hint to download individual files; `WorkspaceArchiveFailedError` -> 500 with its text in `details`; anything else -> 500 with a fixed generic `details` ("Workspace archive could not be created"), never `str(exception)`. Log the unexpected exception server-side with `logger.error` (type and message, no response leakage).
- [ ] **Step 4: Implement the route** (`dependencies=[Depends(authenticate)]` like siblings, sync `def`, `try/except Exception -> raise _archive_http_error(exception) from exception`). Return `Response(content=archive.content, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{archive.filename}"', ...})`, adding `X-Workspace-Skipped-Count` only when `archive.skipped_paths` is non-empty. Leave `_as_http_error` and the existing routes untouched. Add `X-Workspace-Skipped-Count` to `expose_headers` in `main.py` so browser clients can read it.
- [ ] **Step 5: Document** in `rest-api-patterns.md` a short "Workspace archive download" note: endpoint, status codes (200/400/413/500), `X-Workspace-Skipped-Count` and `_SKIPPED_FILES.txt`, the two cap settings, and the AC4 fidelity limitation. (Test-first: no for this step; plain text edit.)
- [ ] **Step 6: Run to verify pass:** new router test file, Task 1/2 service tests, and `ruff check` on touched files -> PASS.
- [ ] **Step 7: Commit.**

---

## Self-review notes

- Revised after code review (CR-001, CR-002): the CORS `expose_headers` entry (T3) and the extraction of the archive code from `agent_workspace_service.py` into `workspace_archive.py` (T1/T2) were added to the plan to match what was built; the service keeps a thin `build_workspace_archive` delegate.
- Spec coverage: approach/route (T3), merged set + reuse (T1 step 4-5), name re-validation (T1), caps (T1), empty/partial/all-fail/other errors (T1 + T3), audit (T2), fidelity note (T1 docstring, T3 guide), test list (T1-T3), AC9 regression (T1 step 6, T3).
- negative-constraints: honored. No-leak of blob owner/name or `str(exception)`: T1 manifest/path-only, T3 `_archive_http_error`. No async job, streaming, temp files, new table: T1 in-memory build, T2 log + metric only. No changes to existing routes/`can_download`/tools/`FileRepository`: T1 step 4 is a behavior-preserving extraction guarded by existing tests, T3 leaves `_as_http_error` untouched. No per-file authorization: none added. Only zip, no subset selection: none added. No UI change: none.
