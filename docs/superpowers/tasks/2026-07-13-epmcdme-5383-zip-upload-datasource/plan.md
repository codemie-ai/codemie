# Plan — EPMCDME-5383: ZIP Upload for File Datasource

## Requirements

Allow users to upload a ZIP archive to a file-type datasource. The backend extracts the archive contents, stores each file individually, and makes them visible as separate entries in the datasource. The ZIP itself is not stored.

Files inside the ZIP that the indexer can handle as plain text (e.g. `.md`, `.rst`) must be indexed, not silently dropped — aligned with the `PlainTextLoader` fallback behavior of direct upload.

---

## Tasks

### Task 1 — Extract `zip_utils` module with `expand_zip_file` ✅

**File**: `src/codemie/service/datasource/zip_utils.py` *(new)*

> Originally planned as `_expand_zip_file` on `FileDatasourceService`. Moved to a dedicated module after reviewer feedback (CR decoupled ZIP logic from service state, enables shared use and isolated testing).

Module contains:
- `ZipExtractionError(ValueError)` — typed exception raised on any extraction failure; carries `detail` and `help_text` fields. Does NOT use `ExtendedHTTPException` — HTTP translation happens at the router boundary.
- `_ZIP_MAX_UNCOMPRESSED_BYTES = 500 MB`
- `_ZIP_MAX_FILE_COUNT = 1000` — max extractable files
- `_ZIP_MAX_ENTRY_COUNT = 10 * _ZIP_MAX_FILE_COUNT` — hard guard on total infolist entries before the loop (prevents CPU exhaustion via crafted archives with millions of directory/metadata entries that would be skipped and never counted by the per-file guard)
- `_read_zip_entry(zf, info, current_total)` — reads a single entry in 64 KB chunks, tracking running total against the size limit; wraps `zf.open`/`entry.read` with `except (RuntimeError, zlib.error, struct.error)` re-raised as `ZipExtractionError` to handle encrypted or corrupted entries
- `expand_zip_file(zip_bytes)` — full extraction pipeline:
  - Rejects archives exceeding `_ZIP_MAX_ENTRY_COUNT` total entries upfront
  - Skips directory entries
  - Normalises Windows backslash separators before `os.path.basename`
  - Skips `._*` Apple Double resource-fork entries
  - Skips nested ZIPs (extension == `'zip'`) with a logged warning
  - Skips duplicate flat filenames with a logged warning
  - Enforces `_ZIP_MAX_FILE_COUNT` on extractable files
  - Reads member bytes via `_read_zip_entry` (enforces size limit per chunk)
  - Returns `list[tuple[str, bytes]]`

No HTTP/FastAPI imports in this module.

---

### Task 2 — Extract `_process_upload_file` as shared helper ✅

**File**: `src/codemie/service/datasource/file_datasource_service.py`

> Originally planned as inline logic in `upload_and_prepare_files` and duplicated in the router. Extracted to `FileDatasourceService._process_upload_file` after reviewer feedback to eliminate duplication.

Static method `_process_upload_file(file, user_id, file_repo)`:
- Guards `file.filename is None` — raises `ZipExtractionError` before any attribute access (CR-001)
- ZIP path: calls `expand_zip_file`; raises `ZipExtractionError` if archive is empty after extraction; writes each extracted entry via `_write_extracted_file` with rollback on partial failure (CR-003, see Task 5)
- Non-ZIP path: calls `file_repo.write_file` with `file.headers.get("content-type", "application/octet-stream")` (CR-002: `.get()` avoids `KeyError` when multipart part omits Content-Type sub-header); validates JSON schema if applicable

Static method `_write_extracted_file(name, content, user_id, file_repo)`:
- Validates JSON schema before writing (so storage is only touched after content passes)
- Guesses MIME type via `mimetypes.guess_type` with `"application/octet-stream"` fallback
- Returns `(FILE_PATH_DATA_NT, filename)`

Both `upload_and_prepare_files` (UPDATE path) and `index_knowledge_base_files` in the router (CREATE path) delegate to `_process_upload_file`.

---

### Task 3 — Apply ZIP expansion to the UPDATE path ✅

**File**: `src/codemie/service/datasource/file_datasource_service.py`

`upload_and_prepare_files` iterates `new_files` and calls `_process_upload_file` per file. ZIP vs non-ZIP branching is fully inside the helper.

---

### Task 4 — Apply ZIP expansion to the CREATE path in the router ✅

**File**: `src/codemie/rest_api/routers/index.py`

`index_knowledge_base_files` now calls `FileDatasourceService._process_upload_file` per file, eliminating the previously duplicated inline if/else block.

---

### Task 5 — CR-003: Rollback on partial ZIP extraction failure ✅

**Files**: `src/codemie/repository/base_file_repository.py`, `file_system_repository.py`, `aws_file_repository.py`, `gcp_file_repository.py`, `azure_file_repository.py`, `file_datasource_service.py`

If `_write_extracted_file` raises on entry N (e.g. a storage error), entries 0..N-1 are already written with no index record — permanent orphans.

Fix:
- Added abstract `delete_file(name, owner)` to `FileRepository` base class
- Implemented in all 4 concrete repositories:
  - `FileSystemRepository`: `os.remove` with `FileNotFoundError` silenced
  - `AWSFileRepository`: `s3_client.delete_object`
  - `GCPFileRepository`: `bucket.blob(name).delete()`
  - `AzureFileRepository`: `container_client.get_blob_client(name).delete_blob()`
- In `_process_upload_file`, the ZIP write loop now tracks `written: list[FILE_PATH_DATA_NT]`; on any exception, iterates `written` and calls `file_repo.delete_file` for each (deletion errors are logged as warnings and swallowed), then re-raises the original exception

---

### Task 6 — Align extension filter with indexer behaviour ✅

The implementation does NOT filter against `IndexKnowledgeBaseFileTypes`. Only nested ZIPs are excluded. All other extensions (`.md`, `.rst`, `.log`, etc.) are accepted — parity with direct-upload `PlainTextLoader` fallback.

---

## Invariants and Constraints

| Constraint | Rationale |
|---|---|
| ZIP itself not added to `uploaded_files` | Users should see extracted files, not the archive |
| Flat filenames (`os.path.basename`) | Avoids path separator issues across storage backends |
| `_ZIP_MAX_FILE_COUNT = 1000` enforced | Prevents unbounded storage consumption per upload |
| `_ZIP_MAX_ENTRY_COUNT = 10 000` enforced upfront | Prevents CPU exhaustion from crafted archives with huge skipped-entry counts |
| Nested ZIPs skipped (not stored verbatim) | Storing them would trigger `ZipLoader`/`ZipConverter` failure at index time |
| No extension guard beyond skipping nested ZIPs | Indexer accepts all unknown extensions via `PlainTextLoader`; guarding in ZIP path would be stricter than direct upload |
| Size guard enforced per-chunk, not on metadata | `ZipInfo.file_size` can be zeroed by crafted archives (zip bomb bypass) — actual decompressed bytes are counted |
| `ZipExtractionError(ValueError)` raised from service/utils layer | HTTP translation (`ExtendedHTTPException`) happens at the router boundary only |
| `RuntimeError`/`zlib.error`/`struct.error` caught in `_read_zip_entry` | Encrypted or corrupted entries would otherwise escape as unhandled exceptions returning HTTP 500 with internal details |
| Rollback on partial ZIP write failure | Prevents orphaned storage files when extraction fails mid-archive |
| `ZipLoader` in `LOADERS` dict left unchanged | Other code paths (SharePoint, direct loader use) may still pass ZIPs to it |

---

## Files Changed

| File | Change |
|---|---|
| `src/codemie/service/datasource/zip_utils.py` | **New** — `ZipExtractionError`, `expand_zip_file`, `_read_zip_entry`, size/count/entry guards |
| `src/codemie/service/datasource/file_datasource_service.py` | Add `_process_upload_file`, `_write_extracted_file`; update `upload_and_prepare_files`; CR-001/002/003 fixes |
| `src/codemie/rest_api/routers/index.py` | Delegate `index_knowledge_base_files` to `_process_upload_file` |
| `src/codemie/repository/base_file_repository.py` | Add abstract `delete_file` |
| `src/codemie/repository/file_system_repository.py` | Implement `delete_file` |
| `src/codemie/repository/aws_file_repository.py` | Implement `delete_file` |
| `src/codemie/repository/gcp_file_repository.py` | Implement `delete_file` |
| `src/codemie/repository/azure_file_repository.py` | Implement `delete_file` |
| `tests/codemie/service/datasource/test_file_datasource_service.py` | ZIP extraction and upload test cases |

**Not changed**: `file_extraction_utils.py`, `FileDatasourceUpdateProcessor`, `FileDatasourceProcessor`, `IndexKnowledgeBaseFileTypes`.
