# Technical Analysis — EPMCDME-5383: ZIP Upload for File Datasource

## Summary

Add the ability to upload a ZIP archive to a file-type datasource so the system extracts its contents and stores each individual file as a separate datasource entry. The fix required changes to **two separate upload paths** — the original analysis captured only the UPDATE path; the CREATE path was missed and contained the same bug.

A secondary finding: the extension guard inside `_expand_zip_file` was stricter than the indexer's actual behavior, causing `.md` and other text-based files inside a ZIP to be silently skipped even though direct upload of those same files works via the `PlainTextLoader` fallback.

---

## Codebase Findings

### Entry Points and Request Models

There are **two independent upload paths** for file datasources:

**CREATE path**
`POST /v1/knowledge_base/{project_name}/files`
→ `index_knowledge_base_files` (`src/codemie/rest_api/routers/index.py:2033`)
→ `FileDatasourceProcessor` (background task)

**UPDATE path**
`PUT /v1/knowledge_base/{project_name}/indexes/{name}/files`
→ `update_knowledge_base_files` (`src/codemie/rest_api/routers/index.py:1429`)
→ `UpdateFileDatasourceUseCase.execute()` (`src/codemie/use_cases/datasource/update_file_datasource_use_case.py:50`)
→ `FileDatasourceService.upload_and_prepare_files()` (`src/codemie/service/datasource/file_datasource_service.py:256`)

**`IndexKnowledgeBaseFileRequest`** (CREATE, `src/codemie/rest_api/models/index.py:1566`):
- `files: List[UploadFile]`
- Validators: count and size only; no extension filter at the model level

**`UpdateKnowledgeBaseFileRequest`** (UPDATE, `src/codemie/rest_api/models/index.py:1377`):
- `files: Optional[List[UploadFile]] = None`
- `MAX_FILE_COUNT: ClassVar[int] = 10`
- Same count/size validators; no extension filter

### Current (pre-fix) File Upload Flow

**CREATE path** — inline in router, no service extraction:
```
index_knowledge_base_files(request, ...)
  for file in request.files:
    content = file.file.read()
    file_object = file_repo.write_file(name=file.filename, ...)  ← ZIP stored verbatim
    files_paths.append(FILE_PATH_DATA_NT(...))
    uploaded_files.append(file_object.name)
  FileDatasourceProcessor(files_paths=files_paths, ...)
```

**UPDATE path** — delegated to service:
```
upload_and_prepare_files(new_files, user, uploaded_files_to_keep, index)
  for file in new_files:
    content = file.file.read()
    file_object = file_repo.write_file(name=file.filename, ...)  ← ZIP stored verbatim
    new_paths.append(FILE_PATH_DATA_NT(...))
    new_filenames.append(file_object.name)
  return PreparedFilesResult(all_files_paths, new_files_paths, uploaded_files)
```

Both paths then invoke the indexer which calls:
```
extract_documents_from_bytes(file_bytes, file_name, ...)
  loader_class = LOADERS.get(file_ext, PlainTextLoader)  ← fallback for unknown ext
```

### ZIP Support Status Before Fix

| Layer | Status | Detail |
|---|---|---|
| `IndexKnowledgeBaseFileTypes.ZIP` | ✅ exists | `index.py:1549` — `ZIP = 'zip'` |
| `LOADERS['zip']` | ✅ mapped | `file_extraction_utils.py:62` — `ZipLoader` (langchain_markitdown) |
| `index_knowledge_base_files` (CREATE) | ❌ gap | Stored ZIP verbatim; `ZipLoader`/`ZipConverter` failed on binary content |
| `upload_and_prepare_files` (UPDATE) | ❌ gap | Same — no extraction before storage |
| `_expand_zip_file` extension guard | ❌ over-strict | Used `IndexKnowledgeBaseFileTypes` to filter; excluded `.md` and other text files that the indexer accepts via `PlainTextLoader` fallback |

### Why ZipLoader Fails

`LOADERS['zip']` → `ZipLoader` from `langchain_markitdown` uses `ZipConverter`, which attempts UTF-8 decode of zip member bytes. Binary content (PDF, DOCX, PPTX, images) raises `UnicodeDecodeError`. The resulting `ValueError` is caught in `extract_documents_from_bytes`, which returns `[]` → processor sees 0 chunks → `NoChunksImportedException`.

### Extension Guard vs. Indexer Inconsistency

`_expand_zip_file` originally guarded with:
```python
supported = {e.value for e in IndexKnowledgeBaseFileTypes}
```
This is stricter than the indexer at `file_extraction_utils.py:143`:
```python
loader_class = LOADERS.get(file_ext, PlainTextLoader)
```
Any extension not in `LOADERS` falls back to `PlainTextLoader` — so `.md`, `.rst`, `.log`, and other plain-text files ARE indexed when uploaded directly, but were silently skipped inside a ZIP. The guard should exclude only file types the indexer cannot handle (currently none — `PlainTextLoader` covers everything).

### Storage Layer

`FileRepositoryFactory.get_current_repository()` returns `FileSystemRepository`, `AWSFileRepository`, or `AzureFileRepository`. All expose the same interface:
```python
write_file(name: str, mime_type: str, owner: str, content: Any) -> FileObject
```
`FileObject.name` equals the `name` argument exactly.

### Test Coverage

| File | Relevance |
|---|---|
| `tests/codemie/service/datasource/test_file_datasource_service.py` | Covers `upload_and_prepare_files`, `_expand_zip_file`, `compute_file_changes`, `parse_uploaded_files` |
| `tests/codemie/use_cases/datasource/test_update_file_datasource_use_case.py` | Covers the UPDATE execute flow end-to-end |
| `tests/codemie/datasource/loader/test_file_extraction_utils.py` | Covers loader selection by extension |

---

## Risk Indicators

1. **ZIP bomb / decompression bomb** — a crafted archive with small compressed size but enormous uncompressed content could exhaust memory or disk. A `_ZIP_MAX_UNCOMPRESSED_BYTES` guard is required (implemented: 500 MB cap).
2. **Filename collisions** — two files with the same name in different ZIP subdirectories. Strategy: flatten with `os.path.basename` and skip duplicates with a warning (implemented).
3. **Nested ZIPs** — spec does not require recursive extraction; nested ZIPs are skipped (not stored verbatim — storing them would trigger `ZipLoader` failure on next index).
4. **Missing files if ZIP has only unsupported content** — if every file in the archive is binary-only unsupported (an edge case after removing the extension guard), `_expand_zip_file` returns `[]`; the background task gets `files_paths=[]` and will raise `NoChunksImportedException`. This is the same behavior as uploading any single unsupported file directly.
5. **CREATE path not using the service layer** — `index_knowledge_base_files` is inline router code, not delegated to `FileDatasourceService`. The ZIP expansion logic must be duplicated there until that function is refactored into the service (out of scope for this task).
6. **`ZipLoader` in LOADERS becomes unreachable for file-datasource uploads** — once expansion happens at upload time, no `.zip` reaches `extract_documents_from_bytes`. The entry can remain (other paths like SharePoint may use it) but is effectively dead for file datasources.

---

## Implementation Approach

Three changes required:

1. **`_expand_zip_file` in `FileDatasourceService`** — add helper; guard removed nested ZIPs and ZIP bomb; flatten filenames. Extension filter changed to exclude only ZIP itself (not all `IndexKnowledgeBaseFileTypes`) so that text-based files like `.md` are passed through to the `PlainTextLoader` fallback at index time.

2. **UPDATE path in `upload_and_prepare_files`** — call `_expand_zip_file` when file extension is `zip` rather than storing verbatim.

3. **CREATE path in `index_knowledge_base_files` (router)** — apply the same ZIP expansion inline (mirrors the UPDATE path logic). Requires `import mimetypes` in the router.

**Files NOT touched:**
- `file_extraction_utils.py` — `ZipLoader` stays for non-file-datasource paths
- `UpdateKnowledgeBaseFileRequest` — no model changes needed
- `FileDatasourceUpdateProcessor` / `FileDatasourceProcessor` — receive the expanded file list transparently
