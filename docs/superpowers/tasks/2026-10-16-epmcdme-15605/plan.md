# Workflow icon upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Commit per task using the repository's existing convention.

**Goal:** Let a workflow icon be uploaded as an image file, stored through `FileRepository` in a workflow icon namespace, and saved as the workflow's existing `icon_url`.

**Architecture:** A new authenticated upload endpoint on the workflow router validates type and size, then writes bytes under the bucket owner with a `workflow-icons/` name prefix and returns the file URL. Save paths keep `icon_url` as-is, but reject namespace URLs whose file does not exist. The download rule gains one workflow-scoped read case. No new column, no migration, no YAML key.

**Tech Stack:** Python, FastAPI, SQLModel, pytest, existing `FileRepository` factory.

**Spec:** `docs/superpowers/tasks/2026-10-16-epmcdme-15605/spec.md`

## Global Constraints

- Allowed types: `image/png`, `image/jpeg`, `image/webp`, `image/gif` (assumed; reviewer confirms against codemie-ui).
- Maximum size: 2 MB (`2 * 1024 * 1024` bytes), assumed; same confirmation.
- Rejections are `ExtendedHTTPException` 400; the message names the reason (type or size).
- No new DB column, no migration, no new YAML key, no change to `/v1/files/`.
- Orphaned icon files are never deleted.

## Review Focus

- Declared `image/png` but bytes are a PDF or script: reject by sniffing. Test in Task 1.
- Empty file (0 bytes): reject as invalid image. Test in Task 1.
- Oversize file just above 2 MB: reject with size message, without reading the whole body. Test in Task 2.
- Namespace-looking `icon_url` with `..` or a non-namespace name: reject at save. Test in Task 3.
- Viewer without edit rights submits a changed `icon_url`: denied by existing gate. Test in Task 3.

---

### Task 1: Icon validation and storage service

**Files:**
- Create: `src/codemie/service/workflow_icon_service.py`
- Test: `tests/codemie/service/test_workflow_icon_service.py`

**Interfaces:**
- Consumes: `FileRepositoryFactory().get_current_repository()` (`src/codemie/repository/repository_factory.py`); `FileObject` (`src/codemie_tools/base/file_object.py:107`); `config.CODEMIE_STORAGE_BUCKET_NAME`.
- Produces: `WORKFLOW_ICON_PREFIX = "workflow-icons/"`, `WORKFLOW_ICON_MAX_BYTES = 2 * 1024 * 1024`, `ALLOWED_ICON_MIME_TYPES: dict[str, str]` (mime -> extension), `store_workflow_icon(data: bytes, filename: str, declared_mime: str) -> FileObject` (raises `ExtendedHTTPException` 400), `is_workflow_icon_file(owner: str, name: str) -> bool`, `icon_exists(file_url: str) -> bool`.

- [ ] **Step 1: Write the failing test**

Test-first: yes — a PNG-declared file whose bytes are not a PNG raises 400 naming "type"; a 0-byte file raises 400; a valid PNG is stored with name starting `workflow-icons/` under the bucket owner.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/codemie/service/test_workflow_icon_service.py -v`
Expected: FAIL with import error for `workflow_icon_service`.

- [ ] **Step 3: Write minimal implementation**

Sniff magic bytes: PNG `\x89PNG\r\n\x1a\n`, JPEG `\xff\xd8\xff`, GIF `GIF87a`/`GIF89a`, WebP `RIFF....WEBP`. Require declared mime in the allowlist AND sniffed type equal to declared. Generated name: `f"{WORKFLOW_ICON_PREFIX}{uuid4().hex}.{ext}"`. Write via `repo.write_file(name=..., mime_type=declared_mime, owner=config.CODEMIE_STORAGE_BUCKET_NAME, content=data)`.
`icon_exists`: parse URL with the same decoding used by the download route (`StringSerializer` next to `FileObject.to_encoded_url`, `file_object.py:147`), require owner == bucket and prefix match, then `repo.read_file(name, owner)` in a try block returning bool.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/codemie/service/test_workflow_icon_service.py -v`
Expected: PASS

---

### Task 2: Upload endpoint on the workflow router

**Files:**
- Modify: `src/codemie/rest_api/routers/workflow.py` (add route near `create_workflow` at `:604`)
- Test: `tests/codemie/rest_api/routers/test_workflow.py`

**Interfaces:**
- Consumes: `store_workflow_icon` (Task 1); `WriteFileResponse` (`src/codemie/rest_api/models/files.py`).
- Produces: `POST /v1/workflows/icon` (multipart `file: UploadFile`) -> `WriteFileResponse(file_url=...)`, requires `authenticate`.

- [ ] **Step 1: Write the failing test**

Test-first: yes — uploading an `image/svg+xml` file returns 400 whose message names the type; uploading a PNG over 2 MB returns 400 naming the size; a request without auth is rejected; a valid PNG returns 200 with a `file_url`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/codemie/rest_api/routers/test_workflow.py -k icon -v`
Expected: FAIL with 404 on `/v1/workflows/icon`.

- [ ] **Step 3: Write minimal implementation**

Read at most `WORKFLOW_ICON_MAX_BYTES + 1` bytes from `file.file`; if longer, raise size 400. Otherwise call `store_workflow_icon(data, file.filename or "", file.content_type or "")`. Return `WriteFileResponse(file_url=result.to_encoded_url())`. Register before any `/workflows/{workflow_id}` route to avoid path shadowing.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/codemie/rest_api/routers/test_workflow.py -k icon -v`
Expected: PASS

---

### Task 3: Save-time check for namespace icon URLs

**Files:**
- Modify: `src/codemie/rest_api/routers/workflow.py` (create path near `_persist_and_finalize_new_workflow` `:522`; update path next to `_run_pre_persist_update_validation`, `:686`)
- Test: `tests/codemie/rest_api/routers/test_workflow.py`, `tests/codemie/service/test_workflow_service.py`

**Interfaces:**
- Consumes: `is_workflow_icon_file` and `icon_exists` (Task 1).
- Produces: `_validate_workflow_icon_url(icon_url: str | None) -> None` raising `ExtendedHTTPException` 400 when the URL is in the namespace but the file is missing.

- [ ] **Step 1: Write the failing test**

Test-first: yes — create or update with a namespace `icon_url` whose file does not exist returns 400; with an existing file it succeeds; with a plain `https://` URL it is saved unchanged; update by a user without write access returns the existing access-denied response and does not change `icon_url`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/codemie/rest_api/routers/test_workflow.py -k "icon_url" -v`
Expected: FAIL: missing-file namespace URL is accepted (201/200).

- [ ] **Step 3: Write minimal implementation**

Call `_validate_workflow_icon_url(request.icon_url)` in both create and update, before `WorkflowConfig(**request.model_dump())`. Do not add a new authorization path; the update gate at `workflow.py:662` already covers `icon_url`. Do not touch `_rewrite_yaml_config_from_objects`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/codemie/rest_api/routers/test_workflow.py tests/codemie/service/test_workflow_service.py -v`
Expected: PASS (existing tests stay green).

---

### Task 4: Workflow-scoped read rule for icon files

**Files:**
- Modify: `src/codemie/service/file_service/download_rules.py` (new rule between Rule B `:157` and Rule C `:161`)
- Modify: `src/codemie/core/workflow_models/workflow_config.py` (add `get_by_icon_url` classmethod mirroring `Assistant.get_by_icon_url`, `src/codemie/rest_api/models/assistant.py:1198`)
- Test: `tests/codemie/service/file_service/test_download_rules.py`

**Interfaces:**
- Consumes: `is_workflow_icon_file` (Task 1); `WorkflowConfig.get_by_icon_url(url: str) -> WorkflowConfig | None`; `Ability(user).can(Action.READ, workflow)`.
- Produces: `_is_readable_workflow_icon(file_object: FileObject, user: User) -> bool`, added to `can_download` as Rule B2.

- [ ] **Step 1: Write the failing test**

Test-first: yes — a viewer of a workflow whose `icon_url` equals the file's encoded URL can download it; a user with no view access gets False; a bucket-owned non-icon file (e.g. `workflows/x.svg`, memory snapshot) is still governed by the existing rules; a non-owner, non-viewer cannot read a file in the namespace that no workflow references.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/codemie/service/file_service/test_download_rules.py -k icon -v`
Expected: FAIL: viewer gets False.

- [ ] **Step 3: Write minimal implementation**

`_is_readable_workflow_icon`: return False unless `file_object.owner == config.CODEMIE_STORAGE_BUCKET_NAME` and `is_workflow_icon_file(...)`; compute `url = file_object.to_encoded_url()`; `wf = WorkflowConfig.get_by_icon_url(url)`; return `wf is not None and Ability(user).can(Action.READ, wf)`. Wrap the lookup in try/except returning False with a warning log, matching `_can_read_workflow_schema`. Add the call after Rule B.
Note: the lookup is an unindexed query on `workflows.icon_url`; the spec forbids schema change, so the index question is recorded, not solved here.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/codemie/service/file_service/test_download_rules.py -v`
Expected: PASS (existing rule tests stay green).

---

## Notes outside this plan

- codemie-ui (not in this repo): upload control beside the URL input, client-side type and size checks with the same limits, preview, replace and remove, and display wiring. Track in the codemie-ui repository; no tasks here.
- Limits confirmation: the 2 MB cap and the four MIME types must match codemie-ui assistant icons before merge.

## Negative-constraint pass

- Ruled out: inline data URL in `icon_url`. No task writes a data URL; Task 2 returns a file URL only.
- Ruled out: owner-scoped storage. Task 1 writes under the bucket owner with the `workflow-icons/` prefix.
- Ruled out: new `icon_file` column. No task adds a column; Task 4 uses the existing `icon_url`.
- Ruled out: deleting replaced or removed files. No task deletes files.
- Non-goal: `/v1/files/` behaviour. No task touches `files.py`.
- Non-goal: assistant icon behaviour. No task touches assistant code.
- Non-goal: YAML key or migration. Task 3 explicitly leaves `_rewrite_yaml_config_from_objects` alone.
- Non-goal: cropping or resizing. Not present.
- "Without access" rule: Task 3 test covers update denial through the existing gate.
