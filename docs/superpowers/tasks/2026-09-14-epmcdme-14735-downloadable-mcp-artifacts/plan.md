# EPMCDME-14735 — Implementation plans (runs 1–4, consolidated)

One section per sdlc run, kept as written. Line numbers and symbols reflect each run's baseline; later runs supersede earlier ones. Current design: `spec.md`.

> **2026-11-03:** ticket AC #8 is dropped and the attach tool is removed. Superseded: run 2 Task 5 (`AttachFileTool`), the attach parts of run 2 Task 7 (registration, counts 7 → 8) and of run 4 Task 7 (attach path check and oversize, `write_file` description pointing to attach). See `spec.md`, Run history.

---

# Run 1 — `2026-09-14-epmcdme-14735-downloadable-mcp-artifacts`

## Downloadable Non-Image MCP Artifacts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable MCP-generated non-image binary files (xlsx, csv, json, zip, pdf) to appear as downloadable `sandbox:/v1/files/...` links in chat instead of falling through to a raw JSON/base64 dump.

**Architecture:** A `_convert_resource_to_url()` function mirrors the existing `_convert_image_to_url()` pattern — base64-decode the item's `data` field, write bytes to a shared `mcp-artifacts` storage subdir, mutate the `MCPToolContentItem` in place. `_post_process_tool_result` gains a second dispatch branch for `item.type == 'resource'`; `_format_content_item` gains a `'resource_url'` branch returning the markdown link. No function signatures change and the existing image path is untouched.

**Tech Stack:** Python 3.x, FastAPI, SQLModel, `mimetypes` stdlib, `unittest.TestCase` + `unittest.mock.patch`.

### Global Constraints

- No per-user or per-conversation storage scoping; `MCP_ARTIFACTS_SUBDIR` is a shared deployment-level constant, matching the existing `MCP_IMAGES_SUBDIR` pattern.
- No changes to `_convert_image_to_url`, `_save_screenshot_to_storage`, or their call chain.
- No changes to `src/codemie/rest_api/routers/files.py` or `src/codemie/repository/repository_factory.py`.
- `MCPExecutionContext` is not threaded into any changed function; signatures of `_post_process_tool_result`, `_convert_mcp_response_to_tool_message`, and `MCPTool._post_process_output_content` are unchanged.
- Azure naming: every new storage subdir constant passes through `FileRepositoryFactory.build_subdir()`.
- Commit per task using the repository's existing convention.

---

#### Task 1: Storage constant and `_save_artifact_to_storage`

**Files:**
- Modify: `src/codemie/core/constants.py` near line 71
- Modify: `src/codemie/service/mcp/toolkit.py` near module-level constants (lines 56–65) and after `_save_screenshot_to_storage` (line 379)
- Create: `tests/codemie/service/mcp/test_toolkit_post_processing.py`

**Test-first: yes — tests for `_save_artifact_to_storage` (happy paths + error paths) written before implementation**

**Interfaces:**
- Produces: `_save_artifact_to_storage(data_b64: str, mime_type: str) -> str` — returns encoded URL string; raises `ValueError` on bad base64, `IOError`/`OSError` on storage failure.

- [ ] **Step 1: Create the test file with failing tests for `_save_artifact_to_storage`**

```python
# tests/codemie/service/mcp/test_toolkit_post_processing.py
import base64
import unittest
from unittest.mock import MagicMock, patch

from codemie.service.mcp.toolkit import _save_artifact_to_storage


class TestSaveArtifactToStorage(unittest.TestCase):
    def _make_repo(self, encoded_url="token"):
        repo = MagicMock()
        file_obj = MagicMock()
        file_obj.to_encoded_url.return_value = encoded_url
        repo.write_file.return_value = file_obj
        return repo

    @patch("codemie.service.mcp.toolkit.FileRepositoryFactory.get_current_repository")
    def test_happy_path_csv(self, mock_factory):
        mock_factory.return_value = self._make_repo("tok1")
        data = base64.b64encode(b"col1,col2\n1,2").decode()
        result = _save_artifact_to_storage(data, "text/csv")
        self.assertEqual(result, "tok1")
        mock_factory.return_value.write_file.assert_called_once()

    @patch("codemie.service.mcp.toolkit.FileRepositoryFactory.get_current_repository")
    def test_unknown_mime_uses_bin_extension(self, mock_factory):
        mock_factory.return_value = self._make_repo("tok2")
        data = base64.b64encode(b"data").decode()
        _save_artifact_to_storage(data, "application/x-unknown-type")
        name_arg = mock_factory.return_value.write_file.call_args[0][0]
        self.assertTrue(name_arg.endswith(".bin"), f"Expected .bin extension, got: {name_arg}")

    @patch("codemie.service.mcp.toolkit.FileRepositoryFactory.get_current_repository")
    def test_bad_base64_raises_value_error(self, mock_factory):
        mock_factory.return_value = self._make_repo()
        with self.assertRaises((ValueError, Exception)):
            _save_artifact_to_storage("not-valid-base64!!!", "text/csv")

    @patch("codemie.service.mcp.toolkit.FileRepositoryFactory.get_current_repository")
    def test_storage_error_propagates(self, mock_factory):
        repo = MagicMock()
        repo.write_file.side_effect = IOError("disk full")
        mock_factory.return_value = repo
        data = base64.b64encode(b"csv").decode()
        with self.assertRaises(IOError):
            _save_artifact_to_storage(data, "text/csv")
```

- [ ] **Step 2: Run tests to confirm they fail**

```
pytest tests/codemie/service/mcp/test_toolkit_post_processing.py -v
```

Expected: `ImportError` — `_save_artifact_to_storage` does not exist yet.

- [ ] **Step 3: Add `MCP_ARTIFACTS_BASE` to constants.py**

In `src/codemie/core/constants.py` near line 71, adjacent to `MCP_IMAGES_BASE = "mcp_images"`, add:

```python
MCP_ARTIFACTS_BASE = "mcp_artifacts"
```

- [ ] **Step 4: Add `MCP_ARTIFACTS_SUBDIR` and `_save_artifact_to_storage` to toolkit.py**

In `src/codemie/service/mcp/toolkit.py`, at the module-level constants block (around lines 56–65), add after the `MCP_IMAGES_SUBDIR` line:

```python
from codemie.core.constants import MCP_ARTIFACTS_BASE
MCP_ARTIFACTS_SUBDIR = FileRepositoryFactory.build_subdir(MCP_ARTIFACTS_BASE)
```

After `_save_screenshot_to_storage` (line 379), insert:

```python
def _save_artifact_to_storage(data_b64: str, mime_type: str) -> str:
    import mimetypes
    import uuid

    raw = base64.b64decode(data_b64)
    guessed = mimetypes.guess_extension(mime_type)
    ext = (guessed or ".bin").lstrip(".")
    name = f"{uuid.uuid4()}.{ext}"
    file_repo = FileRepositoryFactory.get_current_repository()
    file_obj = file_repo.write_file(name, mime_type, owner=MCP_ARTIFACTS_SUBDIR, content=raw)
    return file_obj.to_encoded_url()
```

- [ ] **Step 5: Run tests to confirm they pass**

```
pytest tests/codemie/service/mcp/test_toolkit_post_processing.py::TestSaveArtifactToStorage -v
```

---

#### Task 2: `_convert_resource_to_url`, `_post_process_tool_result` dispatch, `_format_content_item` branch

**Files:**
- Modify: `src/codemie/service/mcp/toolkit.py` — after `_convert_image_to_url` (line ~408), within `_post_process_tool_result` (lines 411–424), within `_format_content_item` (lines 427–451)
- Modify (append): `tests/codemie/service/mcp/test_toolkit_post_processing.py`

**Test-first: yes — tests for `_convert_resource_to_url`, `_post_process_tool_result`, and `_format_content_item` written before any implementation**

**Interfaces:**
- Consumes: `_save_artifact_to_storage(data_b64, mime_type) -> str` (Task 1)
- Produces: `_convert_resource_to_url(item: MCPToolContentItem) -> None` — mutates in place: `item.type` → `'resource_url'`, `item.text` → `"[Download](sandbox:/v1/files/{token})"` or `"[File could not be saved]"`, `item.data` and `item.mimeType` → `None`.

- [ ] **Step 1: Append failing tests to the test file**

```python
# Append to tests/codemie/service/mcp/test_toolkit_post_processing.py
from codemie.service.mcp.models import MCPToolContentItem, MCPToolInvocationResponse
from codemie.service.mcp.toolkit import (
    _convert_resource_to_url,
    _post_process_tool_result,
    _format_content_item,
)


class TestConvertResourceToUrl(unittest.TestCase):
    def _csv_item(self):
        item = MCPToolContentItem(type="resource", mimeType="text/csv")
        item.data = base64.b64encode(b"col1,col2\n1,2").decode()
        return item

    @patch("codemie.service.mcp.toolkit.FileRepositoryFactory.get_current_repository")
    def test_happy_path_mutates_item(self, mock_factory):
        repo = MagicMock()
        fo = MagicMock()
        fo.to_encoded_url.return_value = "abc123"
        repo.write_file.return_value = fo
        mock_factory.return_value = repo
        item = self._csv_item()
        _convert_resource_to_url(item)
        self.assertEqual(item.type, "resource_url")
        self.assertIn("sandbox:/v1/files/abc123", item.text)
        self.assertIsNone(item.data)
        self.assertIsNone(item.mimeType)

    @patch("codemie.service.mcp.toolkit.FileRepositoryFactory.get_current_repository")
    def test_storage_error_sets_error_text(self, mock_factory):
        repo = MagicMock()
        repo.write_file.side_effect = IOError("fail")
        mock_factory.return_value = repo
        item = self._csv_item()
        _convert_resource_to_url(item)
        self.assertEqual(item.type, "resource_url")
        self.assertEqual(item.text, "[File could not be saved]")
        self.assertIsNone(item.data)

    def test_image_mime_type_skipped(self):
        item = MCPToolContentItem(type="resource", mimeType="image/png")
        item.data = base64.b64encode(b"png").decode()
        _convert_resource_to_url(item)
        self.assertEqual(item.type, "resource")  # not mutated

    def test_missing_data_skipped(self):
        item = MCPToolContentItem(type="resource", mimeType="text/csv")
        item.data = None
        _convert_resource_to_url(item)
        self.assertEqual(item.type, "resource")  # not mutated


class TestPostProcessToolResult(unittest.TestCase):
    @patch("codemie.service.mcp.toolkit.FileRepositoryFactory.get_current_repository")
    def test_resource_item_dispatched(self, mock_factory):
        repo = MagicMock()
        fo = MagicMock()
        fo.to_encoded_url.return_value = "tok"
        repo.write_file.return_value = fo
        mock_factory.return_value = repo
        item = MCPToolContentItem(type="resource", mimeType="text/csv")
        item.data = base64.b64encode(b"a,b").decode()
        result = MCPToolInvocationResponse(content=[item])
        _post_process_tool_result(result)
        self.assertEqual(item.type, "resource_url")

    @patch("codemie.service.mcp.toolkit._convert_image_to_url")
    def test_image_item_routes_to_image_converter(self, mock_convert):
        item = MCPToolContentItem(type="image", mimeType="image/png")
        item.data = base64.b64encode(b"imgdata").decode()
        result = MCPToolInvocationResponse(content=[item])
        _post_process_tool_result(result)
        mock_convert.assert_called_once_with(item)

    def test_text_item_unchanged(self):
        item = MCPToolContentItem(type="text", text="hello")
        result = MCPToolInvocationResponse(content=[item])
        _post_process_tool_result(result)
        self.assertEqual(item.type, "text")


class TestFormatContentItem(unittest.TestCase):
    def test_resource_url_returns_text(self):
        item = MCPToolContentItem(type="resource_url")
        item.text = "[Download](sandbox:/v1/files/abc)"
        result = _format_content_item(item)
        self.assertEqual(result, "[Download](sandbox:/v1/files/abc)")

    def test_image_url_branch_unchanged(self):
        item = MCPToolContentItem(type="image_url")
        item.image_url = {"url": "sandbox:/v1/files/img", "detail": "high"}
        result = _format_content_item(item)
        self.assertIn("sandbox:/v1/files/img", result)
```

- [ ] **Step 2: Run tests to confirm they fail**

```
pytest tests/codemie/service/mcp/test_toolkit_post_processing.py -k "TestConvert or TestPostProcess or TestFormat" -v
```

Expected: `ImportError` for `_convert_resource_to_url` or assertion failures.

- [ ] **Step 3: Add `_convert_resource_to_url` to toolkit.py**

After `_convert_image_to_url` (around line 408), insert:

```python
def _convert_resource_to_url(item: "MCPToolContentItem") -> None:
    if item.type != "resource":
        return
    if item.mimeType and item.mimeType.startswith("image/"):
        return
    if not item.data:
        logger.warning("MCP resource item has no data field; skipping artifact conversion")
        return
    try:
        url = _save_artifact_to_storage(item.data, item.mimeType or "application/octet-stream")
        item.type = "resource_url"
        item.text = f"[Download](sandbox:/v1/files/{url})"
        item.data = None
        item.mimeType = None
    except (IOError, OSError, ValueError, base64.binascii.Error) as exc:
        logger.warning("Failed to store MCP artifact: %s", exc)
        item.type = "resource_url"
        item.text = "[File could not be saved]"
        item.data = None
```

- [ ] **Step 4: Extend `_post_process_tool_result` with the resource dispatch branch**

In `toolkit.py` at lines 411–424, add an `elif` branch immediately after the `item.type == 'image'` check:

```python
elif item.type == "resource":
    _convert_resource_to_url(item)
```

No other logic in this function changes.

- [ ] **Step 5: Extend `_format_content_item` with a `'resource_url'` branch**

In `toolkit.py` at lines 427–451, add before the catch-all `json.dumps` fallback:

```python
elif item.type == "resource_url":
    return item.text
```

- [ ] **Step 6: Run all tests to confirm they pass**

```
pytest tests/codemie/service/mcp/test_toolkit_post_processing.py -v
```

---

#### Task 3: `MCPToolContentItem` model additions

**Files:**
- Modify: `src/codemie/service/mcp/models.py` lines 392–443
- Modify (append): `tests/codemie/service/mcp/test_toolkit_post_processing.py`

**Test-first: yes — tests for `is_resource()` and `__str__` behavior written before patching the model**

**Interfaces:**
- Produces: `MCPToolContentItem.is_resource() -> bool`; `__str__` returns `self.text` for `'resource_url'` type.

- [ ] **Step 1: Append failing tests to the test file**

```python
# Append to tests/codemie/service/mcp/test_toolkit_post_processing.py

class TestMCPToolContentItemModel(unittest.TestCase):
    def test_is_resource_for_resource_type(self):
        item = MCPToolContentItem(type="resource")
        self.assertTrue(item.is_resource())

    def test_is_resource_for_resource_url_type(self):
        item = MCPToolContentItem(type="resource_url")
        self.assertTrue(item.is_resource())

    def test_is_resource_false_for_image(self):
        item = MCPToolContentItem(type="image")
        self.assertFalse(item.is_resource())

    def test_is_resource_false_for_text(self):
        item = MCPToolContentItem(type="text")
        self.assertFalse(item.is_resource())

    def test_str_resource_url_returns_text(self):
        item = MCPToolContentItem(type="resource_url")
        item.text = "[Download](sandbox:/v1/files/abc)"
        self.assertEqual(str(item), "[Download](sandbox:/v1/files/abc)")

    def test_str_resource_url_no_text_returns_fallback(self):
        item = MCPToolContentItem(type="resource_url")
        item.text = None
        result = str(item)
        self.assertIsInstance(result, str)
        self.assertNotEqual(result, "")
```

- [ ] **Step 2: Run tests to confirm they fail**

```
pytest tests/codemie/service/mcp/test_toolkit_post_processing.py::TestMCPToolContentItemModel -v
```

Expected: `AttributeError: 'MCPToolContentItem' object has no attribute 'is_resource'`.

- [ ] **Step 3: Add `is_resource()` to `MCPToolContentItem`**

In `src/codemie/service/mcp/models.py`, after the `is_image()` method (around line 440):

```python
def is_resource(self) -> bool:
    return self.type in ("resource", "resource_url")
```

- [ ] **Step 4: Extend `__str__` for `'resource_url'` type**

In `src/codemie/service/mcp/models.py`, in the `__str__` method, add a branch for `"resource_url"` before the default fallback:

```python
elif self.type == "resource_url":
    return self.text or "<Resource artifact unavailable>"
```

- [ ] **Step 5: Run full post-processing test suite**

```
pytest tests/codemie/service/mcp/test_toolkit_post_processing.py -v
```

All tests should pass.

---

### negative-constraints

Verified against spec Non-Goals section and inline constraints:

- **No per-user/per-conversation storage isolation** — no task threads `MCPExecutionContext` into any function; no call signatures change. Honored: `MCP_ARTIFACTS_SUBDIR` is a shared constant identical in structure to `MCP_IMAGES_SUBDIR`.
- **No access-control at the file-serving endpoint** — `src/codemie/rest_api/routers/files.py` is not touched in any task.
- **No URI-reference resource items** — `_convert_resource_to_url` only processes items with an inline `data` field; items with `data = None` are skipped without error.
- **No file lifecycle management** — no deletion or expiry logic added anywhere.
- **No change to existing image artifact pipeline** — `_convert_image_to_url` and `_save_screenshot_to_storage` are unchanged; Task 2 regression test confirms `_convert_image_to_url` is still called for `type == 'image'` items.
- **No chat UI changes** — the `sandbox:/v1/files/{url}` markdown link pattern is identical to what the image path already produces; no frontend changes.
- **No handling of image MIME types in the new code path** — `_convert_resource_to_url` guards against `item.mimeType.startswith("image/")` and returns without mutation, leaving such items on the existing image path.

---

# Run 2 — `2026-09-18-epmcdme-14735-filesystem-toolkit-artifacts`

## EPMCDME-14735 — Downloadable FileSystem-toolkit artifacts: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Files created by the FileSystem toolkit are ingested into the sandbox file store and returned to chat as working `sandbox:/v1/files/…` download links.

**Architecture:** Extract the existing 3-step store-and-mint primitive into one helper in `codemie_tools/base/`, migrate the three current callers onto it without changing a byte of their output, then use it from `WriteFileTool` and a new attach-file tool. No service-, repository- or UI-layer change: `toolkit_settings_service.py:227` already supplies a live repository and `user_id`.

**Tech Stack:** Python 3, pydantic v2, LangChain `BaseTool` via `CodeMieTool`, pytest.

**Spec:** `docs/superpowers/tasks/2026-09-18-epmcdme-14735-filesystem-toolkit-artifacts/spec.md`

### Global Constraints

- Helper returns a **bare** `sandbox:/v1/files/{to_encoded_url()}` URL and takes the filename **as given**. Callers own their own filename convention and return string.
- Both new writers name stored objects `f"{uuid4()}_{os.path.basename(name)}"` — `files.py:213` `_strip_uuid_prefix` only strips that prefix form.
- `owner=user_id` for all new artifacts.
- Size cap `config.FILES_STORAGE_MAX_UPLOAD_SIZE` enforced inside the helper. No new config knob.
- `file_repository=None` ⇒ do the primary job, omit the link, never raise.
- Existing filename conventions of the three current callers must NOT be standardized.
- Commit per task using the repository's existing convention.

---

#### Task 1: Shared store-and-mint helper

**Files:**
- Create: `src/codemie_tools/base/artifact_storage.py`
- Test: `tests/codemie_tools/base/test_artifact_storage.py`

**Interfaces:**
- Produces: `store_artifact(file_repository, name: str, content: bytes, owner: str) -> str` — returns the bare `sandbox:/v1/files/…` URL. Returns `None` when `file_repository` is falsy (logs a warning). Raises `ValueError` naming the limit when `len(content)` exceeds `config.FILES_STORAGE_MAX_UPLOAD_SIZE`. MIME derived as in `file_export_service.py:113-114` (`Path(name).suffix` → `mimetypes`, default `application/octet-stream`).

**Test-first: yes** — `test_store_artifact_returns_bare_sandbox_url` asserts the exact return `f"sandbox:/v1/files/{encoded}"` against a mock repo; fails with `ModuleNotFoundError: codemie_tools.base.artifact_storage`.

- [ ] **Step 1: Write the failing tests** in `tests/codemie_tools/base/test_artifact_storage.py`, using an ad-hoc mock repo whose `write_file` returns an object with `to_encoded_url()` (follow `tests/codemie/service/test_skill_file_storage.py`). Cover: bare-URL return; `owner` kwarg passed through verbatim; `name` passed through unmodified (no uuid added by the helper); `None` + warning when repository is falsy; `ValueError` naming the limit when content exceeds `FILES_STORAGE_MAX_UPLOAD_SIZE` **and** `write_file` never called; MIME `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` for `.xlsx`.
- [ ] **Step 2: Run** `pytest tests/codemie_tools/base/test_artifact_storage.py -v` — expect collection failure.
- [ ] **Step 3: Implement** the module. Size check first, then `write_file(name=name, mime_type=…, content=content, owner=owner)`, then return the f-string. Module-level `logger`. Import `config` from `codemie.configs` lazily inside the function if a module-level import creates a cycle from `codemie_tools`.
- [ ] **Step 4: Run** the suite — expect PASS.

---

#### Task 2: Migrate `FileExportService.store_exported_bytes`

**Files:**
- Modify: `src/codemie_tools/data_management/code_executor/file_export_service.py:103-125`
- Test: `tests/codemie_tools/data_management/code_executor/test_file_export_service.py` (create)

**Interfaces:** Consumes `store_artifact` from Task 1. Public signature and return string unchanged.

**Test-first: yes** — `test_store_exported_bytes_return_string_unchanged` asserts the exact string `" File 'report.csv', URL \`sandbox:/v1/files/ENC\`"` (leading space, backticks). No such test file exists today, so it fails on collection.

- [ ] **Step 1: Write the characterization test** against the **current** implementation first — it must pass before the refactor. Also assert the stored name still matches `^[0-9a-f-]{36}_report\.csv$` and `owner == user_id`.
- [ ] **Step 2: Run** it against unmodified source — expect PASS (this is the AC #7 baseline).
- [ ] **Step 3: Refactor** L112-122 to build `unique_filename` as today, then delegate the write to `store_artifact(...)` and wrap its bare URL back into the existing sentence. Keep the `except Exception → log, return None` wrapper (L123-125) so the new `ValueError` size cap degrades to `None` here, unchanged behavior.
- [ ] **Step 4: Run** the same test — expect PASS with no edit to the assertions.

---

#### Task 3: Migrate `GenerateImageTool` and the MCP artifact saver

**Files:**
- Modify: `src/codemie_tools/data_management/file_system/generate_image_tool.py:55-65`
- Modify: `src/codemie/service/mcp/toolkit.py:382-406`
- Test: `tests/codemie/service/mcp/test_toolkit_post_processing.py` (extend)

**Interfaces:** Consumes `store_artifact`. `_store_b64_image` keeps `{uuid4()}.png`; `_save_artifact_to_storage` keeps `{uuid4()}.{ext}` and `owner=MCP_ARTIFACTS_SUBDIR` — neither convention changes (spec non-goal).

**Test-first: yes** — `test_store_b64_image_returns_sandbox_url_unchanged` asserts `_store_b64_image` still returns exactly `f"sandbox:/v1/files/{ENC}"` and the stored name still ends `.png`; add the mirrored assertion for `_save_artifact_to_storage`'s `owner=MCP_ARTIFACTS_SUBDIR`. Write and pass both against current source before refactoring.

- [ ] **Step 1: Write both characterization tests**; run against unmodified source — expect PASS.
- [ ] **Step 2: Refactor** both call sites to delegate the `write_file` + URL formatting to `store_artifact`, keeping each caller's own name/owner computation in place.
- [ ] **Step 3: Run** `pytest tests/codemie/service/mcp/test_toolkit_post_processing.py tests/codemie_tools/data_management -v` — expect PASS unchanged, including the existing `test_happy_path_mutates_item` and `test_storage_error_sets_error_text`.

---

#### Task 4: `WriteFileTool` — binary path plus minted link

**Files:**
- Modify: `src/codemie_tools/data_management/file_system/tools.py:110-135`
- Modify: `src/codemie_tools/data_management/file_system/toolkit.py:139`
- Test: `tests/codemie_tools/data_management/file_system/test_file_system_tools.py` (extend)

**Interfaces:** Consumes `store_artifact`. Produces `WriteFileTool(root_dir=…, file_repository=…, user_id=…)` — new fields declared `Optional[Any] = Field(exclude=True, default=None)` / `str = ""`, matching `generate_image_tool.py:36-38`. `WriteFileInput` gains `is_base64: bool = False` ("set true when `text` is base64-encoded binary content, e.g. xlsx/zip/pdf").

`execute` keeps its existing return `f"File written successfully to {file_path}"` **unchanged** — the link is NOT appended here. See Task 6 for why.

**Test-first: yes** — `test_write_file_tool_mints_download_link` builds the tool with a mock repo, writes `report.csv`, and asserts the stored name matches `^[0-9a-f-]{36}_report\.csv$`, `owner == "user-1"`, and that the tool exposes the minted bare URL for post-processing. Fails today: `WriteFileTool` has no `file_repository` field.

- [ ] **Step 1: Write the failing tests**: link minted on write; `is_base64=True` writes real bytes (round-trip a small zip through `wb` and assert byte equality); `file_repository=None` still writes the file and returns the existing message with no link and no exception (AC #8); an oversized write surfaces a clear message naming the limit and stores nothing (AC #6).
- [ ] **Step 2: Run** — expect FAIL on the unknown `file_repository` field.
- [ ] **Step 3: Implement.** Add the three fields. In `execute`, decode `base64.b64decode(text)` when `is_base64` and open `"wb"`, else keep the current `"w"` + utf-8 path (`tools.py:129-131`). After a successful disk write, read the bytes back and call `store_artifact(self.file_repository, f"{uuid4()}_{os.path.basename(file_path)}", content, self.user_id)`, storing the result on the instance for Task 6. Wrap in try/except → log and continue, so a storage failure never fails the write.
- [ ] **Step 4: Wire** `toolkit.py:139` to pass `file_repository=self.file_repository, user_id=self.user_id or ""`.
- [ ] **Step 5: Run** `pytest tests/codemie_tools/data_management/file_system -v` — expect PASS, including the untouched `test_write_file_tool` substring assertion at L50-55.

---

#### Task 5: `AttachFileTool`

**Files:**
- Create: `src/codemie_tools/data_management/file_system/attach_file_tool.py`
- Modify: `src/codemie_tools/data_management/file_system/tools_vars.py` (append `ATTACH_FILE_TOOL`)
- Test: `tests/codemie_tools/data_management/file_system/test_attach_file_tool.py` (create)

**Interfaces:** Produces `AttachFileTool(root_dir, file_repository, user_id)` with `AttachFileInput(file_path: str)`. Reuses `CodeExecutorTool._validate_export_paths([file_path], self.root_dir)` **unchanged** (`code_executor_tool.py:625-637`) — it is already a `@staticmethod(export_files, workdir)`. On success returns the minted link; the returned string is short, so truncation is not a concern here.

`ATTACH_FILE_TOOL = ToolMetadata(name="attach_file_to_conversation", …)` — description must tell the model to call it with the path of a file it generated (e.g. via `run_command_line_tool`) to make it downloadable by the user.

**Test-first: yes** — `test_attach_file_returns_download_link` writes a real file under a tmp `root_dir` and asserts the result contains `sandbox:/v1/files/` and that the stored name is `{uuid}_closed_positions_analysis.xlsx`. Fails: module does not exist.

- [ ] **Step 1: Write the failing tests**: happy path for xlsx and for csv/json/zip/pdf (AC #4, binary bytes read with `rb`); absolute path, `../` traversal and `/etc/...` each raise `ToolException` via `_validate_export_paths` (AC #9); missing file returns a clear error without raising; `file_repository=None` returns a non-link message without raising (AC #8).
- [ ] **Step 2: Run** — expect collection failure.
- [ ] **Step 3: Implement** the tool and its metadata: validate, resolve under `root_dir`, read bytes, `store_artifact(...)` with the `{uuid4()}_{basename}` name and `owner=self.user_id`, return `f"File '{basename}' is ready for download: {url}"`. `is_safe` stays at the `CodeMieTool` default (`False`).
- [ ] **Step 4: Run** the suite — expect PASS.

---

#### Task 6: Truncation-safe link delivery for `WriteFileTool`

**Files:**
- Modify: `src/codemie_tools/data_management/file_system/tools.py` (`WriteFileTool` only)
- Test: `tests/codemie_tools/data_management/file_system/test_file_system_tools.py` (extend)

**Interfaces:** Consumes the minted URL stashed by Task 4. `WriteFileTool` overrides `_post_process_output_content(self, output, *args, **kwargs)` — the same hook the MCP path uses (`mcp/toolkit.py:301-305`) — calling `super()` then appending `f"\n\n[Download]({url})"` when a URL was minted.

**Why:** `CodeMieTool._run` (`base/codemie_tool.py:83-84`) runs `_limit_output_content` **before** `_post_process_output_content`. A link appended inside `execute()` is part of the truncated payload and is silently dropped on large output. Appending in the post-process hook puts the link after truncation, so it always survives. This is the mechanism chosen for AC #10.

**Test-first: yes** — `test_download_link_survives_output_truncation`: set `tool.tokens_size_limit` to a tiny value, run `tool._run(file_path=…, text=<content large enough to truncate>)`, and assert the result both contains the truncation marker and still ends with the `sandbox:/v1/files/` link. This fails if the link is appended inside `execute()`.

- [ ] **Step 1: Write the failing test** as described, plus `test_post_process_no_link_when_repository_none` asserting the output is untouched.
- [ ] **Step 2: Run** — expect FAIL (link truncated away / absent).
- [ ] **Step 3: Implement** the `_post_process_output_content` override and remove any link-appending from `execute()`.
- [ ] **Step 4: Run** — expect PASS.

---

#### Task 7: Toolkit registration, UI visibility and toolkit-test fixtures

**Files:**
- Modify: `src/codemie_tools/data_management/file_system/toolkit.py:51-60` (UI tool list), `:96-114` (`get_tools_ui_info`), `:136-144` (`get_tools`), plus the `tools_vars` import block at `:35-43`
- Test: `tests/codemie_tools/data_management/test_file_system_toolkit.py` (modify)

**Interfaces:** Consumes `AttachFileTool` / `ATTACH_FILE_TOOL` from Task 5.

**Gating decision:** `AttachFileTool` is gated exactly like the six existing admin tools — appended inside the `if self._is_file_system_tools_enabled():` block in `get_tools()`, and added to `FileSystemToolkitUI.tools` so it appears in `get_tools_ui_info` only on the admin + env-flag branch (`toolkit.py:101-105`). It is **not** added to the non-admin safe-tool list at `:108-110`. Rationale: it reads arbitrary paths under `root_dir`, the same trust level as `ReadFileTool`.

**Test-first: yes** — `test_get_tools_with_env_var` currently asserts exactly 7 tools and `test_get_tools_ui_info_admin_with_env_var` exactly 7; both fail with 8 once the tool is registered. Update both counts to 8 and add `ATTACH_FILE_TOOL.name` to the present/absent assertions.

- [ ] **Step 1: Update the hard-coded counts** (7 → 8 at `test_file_system_toolkit.py` L47 and L93) and extend the per-tool presence assertions; confirm the non-admin count stays 1 and that the attach tool is asserted **absent** from the non-admin branches.
- [ ] **Step 2: Add the wired-repository fixture.** Add a module-level `file_repository` fixture returning a `MagicMock` whose `write_file` returns a stub with `to_encoded_url() -> "ENC"`, plus `toolkit_with_repo = FileSystemToolkit.get_toolkit(configs={"user_id": "user-1"}, file_repository=<fixture>)`. Keep the existing `configs={}` fixture untouched. Define the fixture locally in this test module rather than in a shared `conftest.py` — the repo has no shared `file_repository` fixture and only two ad-hoc precedents, so a shared one is not yet earned.
- [ ] **Step 3: Add** `test_write_and_attach_tools_receive_repository`, asserting `WriteFileTool` and `AttachFileTool` from `toolkit_with_repo` both carry the repository and `user_id == "user-1"` — the currently untested wired case.
- [ ] **Step 4: Run** `pytest tests/codemie_tools/data_management/test_file_system_toolkit.py -v` — expect FAIL on the missing registration.
- [ ] **Step 5: Register** the tool: import `ATTACH_FILE_TOOL` and `AttachFileTool`, add `Tool.from_metadata(ATTACH_FILE_TOOL)` to `FileSystemToolkitUI.tools`, and append `AttachFileTool(root_dir=self.root_directory, file_repository=self.file_repository, user_id=self.user_id or "")` to `admin_tools`.
- [ ] **Step 6: Run** the toolkit and file_system suites — expect PASS.

---

### Self-review notes

- **Spec coverage:** AC1/AC2 → T1+T4+T6; AC3 → T5; AC4 → T4 (binary write) + T5 (format cases); AC5 → T1 (`owner` passthrough), T4/T5 (pass `user_id`); AC6 → T1 size cap; AC7 → T2+T3 characterization tests written and passing *before* the refactor; AC8 → T1 falsy-repo branch, asserted in T4 and T5; AC9 → T5; AC10 → T6.
- **negative-constraints:** *No UI-repo change* — no task touches `codemie-ui`. *No auto-capture / workdir diffing* — T5 is an explicit LLM-named path only; nothing inspects the working directory. *No MCP owner change* — T3 explicitly preserves `MCP_ARTIFACTS_SUBDIR` and asserts it. *No filename standardization* — T2/T3 keep each caller's own convention and assert the stored names. *No new config knob* — T1 reuses `FILES_STORAGE_MAX_UPLOAD_SIZE`. *No auth added to `GET /v1/files/`* — `files.py` is not modified by any task. *Other four admin tools unchanged* — only `WriteFileTool` is modified in `tools.py`; T7 touches registration only. *Retention* — absent from the plan.
- **Type consistency:** `store_artifact(file_repository, name, content, owner)` is used with that exact signature in T2, T3, T4 and T5; `ATTACH_FILE_TOOL` / `AttachFileTool` names are consistent between T5 and T7.

---

# Run 3 — `2026-10-02-epmcdme-14735-artifact-auth-gaps`

## EPMCDME-14735 Artifact / 12708 Auth Gaps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use `- [ ]` tracking.

**Goal:** No dead download links (no owner means nothing is stored), and share recipients can reach artifacts whose links exist only in persisted thoughts.

**Architecture:** One guard in `store_artifact` covers all five callers. The MCP no-user fallback is removed. `collect_message_file_tokens` also scans `thoughts[].message` (recursing into `children`), so the Rule E check and `shared_file_urls` both pick the tokens up from that one collector.

**Spec:** `docs/superpowers/tasks/2026-10-02-epmcdme-14735-artifact-auth-gaps/spec.md`

**Branch:** Work on `EPMCDME-14735_downloadable-mcp-artifacts`. Commit per task using the repository's existing convention.

**Dirty tree:** The user has uncommitted changes in several files, including `tests/codemie_tools/data_management/file_system/test_generate_image_tool.py` and `src/codemie_tools/data_management/file_system/image_generator.py`. A task that touches one of these files stages only its own hunks (`git add -p`). Never stage, revert or overwrite the pre-existing changes.

**Verified (spec Gap 3.3):** `GET /v1/share/conversations/{token}` (`rest_api/routers/share.py:65-81`) returns the whole `Conversation` as `Dict[str, Any]`. `history` is a JSONB list of `GeneratedMessage`, and `GeneratedMessage.thoughts: List[Thought]` (`models/conversation.py:135`) is not excluded on serialization. `Thought` (`chains/base.py:39-55`) carries `message`, `input_text` and `children`. So recipients do receive the persisted thoughts.

### Global Constraints
- No change to `rest_api/routers/files.py`, Rule C, `_save_screenshot_to_storage` or its `MCP_IMAGES_SUBDIR` fallback.
- Leave `enterprise/plugin/dependencies.py`, `execute_workspace_script_tool.py`, the agent `stream()` paths and the UI untouched.
- A non-empty owner gives byte-identical behavior (AC-6/AC7).

### Review Focus
- Whitespace-only owner `"  "` must be refused, not just `""` (Task 1).
- `store_artifact` returning `None` on the MCP path must not raise `AttributeError` (Task 2).
- A token that appears only in `input_text` must NOT be granted (Task 5).
- Thoughts nested 2+ levels deep in `children` are scanned (Task 5).
- A message with `thoughts=None` must not crash the collector (Task 5).

---

#### Task 1: `store_artifact` fails closed on an empty owner
**Files:** Modify `src/codemie_tools/base/artifact_storage.py:32-67`. Test: `tests/codemie_tools/base/test_artifact_storage.py`.
**Produces:** `store_artifact(...)` raises `ValueError("File could not be saved: it has no owner")` when `owner` is `None`, empty or whitespace-only.

Test-first: yes. A parametrized test over owner `None` / `""` / `"  "` expects `ValueError` and asserts that the mocked repo's `write_file` is never called.

- [ ] Write the parametrized test and run it. It FAILS today because the empty owner is passed through.
- [ ] Add the guard as the first statement of `store_artifact`: `if not owner or not owner.strip(): raise ValueError(...)`. It must run before the repo/size checks.
- [ ] Run the file's tests. Existing real-owner tests must still pass.

#### Task 2: MCP artifacts with no owner are not stored, and `MCP_ARTIFACTS_SUBDIR` is deleted
**Files:** Modify `src/codemie/service/mcp/toolkit.py:45,402-496`, `src/codemie/repository/repository_factory.py:18,84` and `src/codemie/core/constants.py:74`. Test: `tests/codemie/service/mcp/test_toolkit_post_processing.py`.
**Consumes:** the Task 1 `ValueError`.

Test-first: yes. (a) A resource with `owner=None` renders `[File could not be saved]`, calls no `write_file` and logs a warning. (b) When `store_artifact` is patched to return `None`, the item renders `[File could not be saved]` with no `AttributeError`.

- [ ] Replace the test at line ~107 that asserts `owner == MCP_ARTIFACTS_SUBDIR` with tests (a) and (b), and drop the import. Run them. Both FAIL.
- [ ] In `_save_artifact_to_storage`, delete the `owner = MCP_ARTIFACTS_SUBDIR` fallback and update its docstring. When there is no owner, log a warning and pass the owner through so that Task 1 raises `ValueError`. When `store_artifact` returns `None`, raise `ValueError("no file storage configured")` instead of calling `.removeprefix`. `_convert_resource_to_url`'s existing except tuple then renders `[File could not be saved]`.
- [ ] Delete `MCP_ARTIFACTS_SUBDIR` and its import from `repository_factory.py`, and delete `MCP_ARTIFACTS_BASE` from `constants.py`. They have no other readers. Remove the toolkit import too.
- [ ] Run the file's tests and `tests/codemie/service/mcp/`. Screenshot tests must be unchanged.

#### Task 3: File-system tools surface an empty owner and a missing repo
**Files:** Modify `src/codemie_tools/data_management/file_system/tools.py:281-293` (WriteFileTool). Tests: `tests/codemie_tools/data_management/file_system/test_file_system_tools.py`, `test_attach_file_tool.py` and `test_generate_image_tool.py` (dirty file: stage your own hunks only).

Test-first: yes. (a) WriteFileTool with `store_artifact` patched to return `None` must output "file was written but not stored for download" and no `sandbox:/v1/files/`. This FAILS today because the output is silent. (b) WriteFileTool, AttachFileTool and GenerateImageTool with `user_id=""` produce no `sandbox:/v1/files/` link and a could-not-be-saved warning or error. These pass once Task 1 is in.

- [ ] Write tests (a) and (b). Run them, and confirm (a) fails.
- [ ] In `WriteFileTool._store_artifact`, a `None` return sets `last_storage_error` (for example "no file storage is configured") so that the existing warning at L293 fires.
- [ ] Run the three test files.

#### Task 4: `FileExportService` refuses an empty `user_id`
**Files:** Modify `src/codemie_tools/data_management/code_executor/file_export_service.py:177-183`. Test: `tests/codemie_tools/data_management/code_executor/test_file_export_service.py`.

Test-first: yes. (a) `_collect_single_file` with `user_id=""` returns `None`, builds no `FileObject` and logs a warning. (b) `store_exported_bytes` with `user_id=""` returns the "was not stored" message, which comes from Task 1's `ValueError`.

- [ ] Write both tests. Run them, and confirm (a) fails.
- [ ] Add an early `if not self.user_id: logger.warning(...); return None` to `_collect_single_file`.
- [ ] Run the test file.

#### Task 5: Thought-borne tokens join the share grant
**Files:** Modify `src/codemie/service/share_conversation_service.py:37-52`. Tests: `tests/codemie/service/test_share_conversation_service.py` and `tests/codemie/rest_api/routers/test_files.py` (tests only, `files.py` is not changed).

Test-first: yes. The collector returns tokens from `thoughts[].message` and from `children` nested two levels deep. It returns no token found only in `input_text`, and `thoughts=None` is safe. `_build_shared_file_urls` includes a thought-only token. In `test_files.py`, a share recipient downloads a blob whose link is only in a persisted thought (200). A token only in `input_text` gets 404.

- [ ] Write the tests using the `_shared_conversation_with_history` helper in `test_files.py`. Run them. They FAIL.
- [ ] Extend the collector by extracting the existing text scan into `_tokens_in(text)`. Add a recursive walk over `getattr(message, "thoughts", None) or []` that reads only `thought.message` and then `thought.children`. Update the docstring to list the three carriers.
- [ ] Run both test files and `tests/codemie/rest_api/routers/test_share.py`.

---

# Run 4 — `2026-10-22-backend-artifact-publisher`

## Artifact Publisher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** One service-layer publisher puts MCP and file-tool artifacts into the conversation workspace, with a bounded share grant.
**Architecture:** `codemie_tools` holds types and a Protocol only; `codemie/service/artifact_publisher.py` implements it and is injected into producers. MCP content model gains spec-shaped fields; Rule E gains an ownership bound.
**Tech Stack:** Python, FastAPI, pydantic, pytest, ruff.
**Spec:** `docs/superpowers/tasks/2026-10-22-backend-artifact-publisher/spec.md` (research: `technical-analysis.md` same dir).

Commit per task using the repository's existing convention (ticket EPMCDME-14735). Tasks 11-12 are outside git: nothing is committed there.

### Global Constraints
- Path `artifacts/<turn-id>/<last-segment>`; duplicates `<name> (2).<ext>`, `(3)`; check order: size, strict `b64decode(validate=True)`, zip (`zipfile.is_zipfile`).
- No module-level `codemie.*` import in `codemie_tools`. `publish` never raises; failure reasons name the file.
- Rules A, B, C, D, D2 and `is_safe_blob_ref` in `download_rules.py` untouched; `mcp_images` never written again.
- Not-delivered text: "`<name>` stays on the MCP server and was not delivered", no link.

### Review Focus
- Name with `../`, `/`, or percent-escapes in a `uri` -> last decoded segment only.
- Zero-byte, oversize, invalid-base64 and fake `.xlsx` payloads -> reason text, nothing stored.
- Same file published twice in one turn, and then registered post-turn -> one row in Browse files.
- Foreign `workspace-<other>` token and foreign-user token pasted in history -> refused via share.
- Missing conversation / missing user -> per-user WARNING fallback / reason, never a raise.

---

#### Task 1: Publisher types
**Files:** Create `src/codemie_tools/base/artifact_publishing.py`; Test `tests/codemie_tools/base/test_artifact_publishing.py`
**Interfaces:** Produces `PublishContext(user_id, conversation_id: str|None, turn_id: str)` frozen dataclass; `PublishResult(link, name, size, mime, reason)` dataclass (`ok` property); `ArtifactPublisher` Protocol with `publish(ctx, name, *, content: bytes|str|None=None, path: str|None=None, mime: str|None=None, base64_content=False) -> PublishResult`; `ZIP_CONTAINER_EXTENSIONS` (moved from `file_system/tools.py:104`, re-imported there).
Test-first: yes — `PublishContext` is immutable and `ZIP_CONTAINER_EXTENSIONS` is importable from the new module and from `tools.py`.
- [ ] Write the test, see it fail, create the module, see it pass, commit.

#### Task 2: Workspace method and artifact guard
**Files:** Modify `src/codemie/service/agent_workspace_service.py` (near `upsert_binary_file` :113, `edit_file` :458, `upsert_text_file` :365, delete, script sync, `is_reserved_workspace_path`); Test `tests/codemie/service/test_agent_workspace_service_artifacts.py`
**Interfaces:** Produces `is_artifact_path(path) -> bool`; `AgentWorkspaceService.publish_artifact_file(conversation_id, user, path, content: bytes, mime) -> blob link` (skips the guard).
Test-first: yes — `edit_file`, delete, `upsert_text_file`, `upsert_binary_file` and script sync on `artifacts/t/x.csv` are refused; `publish_artifact_file` succeeds.
- [ ] Add guard beside `is_reserved_workspace_path` and call it in the five mutation paths; add the publisher-only method on `_get_or_create_workspace`. Reuse the `register_generated_files` `(owner,name,mime)` key so the row registers once.

#### Task 3: Publisher service
**Files:** Create `src/codemie/service/artifact_publisher.py`; Test `tests/codemie/service/test_artifact_publisher.py`
**Interfaces:** Consumes Tasks 1-2. Produces `class ArtifactPublisherService` implementing `ArtifactPublisher`, constructed with the file repository.
Test-first: yes — one test per Review Focus line: oversize base64 refused before decode; invalid base64; fake zip `.xlsx`; duplicate `(2)`/`(3)`; path-shaped name reduced to last segment; no conversation -> per-user store `<uuid>_<name>` plus WARNING; no user -> reason; disk `path` sized by `os.stat`; publish then `register_generated_files` -> exactly one row.
- [ ] Implement per spec section 2, no raising (wrap and return reason). Remove `store_artifact` in Task 7 once callers are gone.

#### Task 4: MCP content model
**Files:** Modify `src/codemie/service/mcp/models.py:392-468`; Test `tests/codemie/service/mcp/test_mcp_wire_parsing.py` + fixtures JSON captured from spec shape
**Interfaces:** Produces `MCPToolContentItem.resource` (`uri, mimeType, blob, text`), top-level `uri`, `name`; `file_name()` (percent-decoded last uri segment, else `name`, else `artifact`, MIME extension added); `is_resource()` removed.
Test-first: yes — `MCPToolInvocationResponse.model_validate` of wire JSON (embedded blob, embedded text, resource_link) keeps `uri`/`blob`; flat `{type:resource,data}` is not deliverable.
- [ ] Edit `test_toolkit_post_processing.py` to drop `is_resource` and hand-built flat items.

#### Task 5: MCP toolkit adopts publisher
**Files:** Modify `src/codemie/service/mcp/toolkit.py:305-552`, `toolkit_service.py:185-244`, `src/codemie/service/tools/toolkit_service.py:1058`; Test `test_toolkit_post_processing.py`, `test_execution_context_integration.py`
**Interfaces:** Consumes Tasks 3-4. Produces `MCPExecutionContext.request_uuid` (local-only, `exclude=True`); `get_mcp_server_tools(..., request_uuid=None)`; `_convert_mcp_response_to_tool_message(tool_result, ctx: PublishContext)` replacing `owner`.
Test-first: yes — embedded blob and text become workspace links `artifacts/<turn>/<name>`; resource link and empty resource give the not-delivered text; image publishes as `image.<ext>`; failure gives a reason, not a `data:` URL; no write to `MCP_IMAGES_SUBDIR`.
- [ ] Replace `_save_screenshot_to_storage`, `_save_artifact_to_storage` with publisher calls; workflow callers (`tool_node.py`) pass nothing (B6 out of scope).

#### Task 6: Enterprise adapter
**Files:** Modify `src/codemie/enterprise/plugin/dependencies.py:348-389`, `plugin_tools_delegate.py:100`; Test `tests/codemie/enterprise/plugin/test_mcp_converter.py`
**Interfaces:** Produces module-level `convert_mcp_response(enterprise_response, ctx: PublishContext) -> ...`; closure only builds the context from `user`/`request`.
Test-first: yes — stub enterprise object with spec payload yields a link; second test guarded by `pytest.importorskip("codemie_enterprise.plugin")` builds the real response and asserts `uri`, `blob`, file name and resource links survive `model_dump()`.
- [ ] Thread `request_uuid` through `get_plugin_tools` if the signature allows; else fresh uuid4 turn id, logged. Never "no owner".

#### Task 7: File producers adopt publisher
**Files:** Modify `src/codemie/service/tools/toolkit_settings_service.py:190-231`, `src/codemie_tools/file_system/{attach_file_tool,tools,generate_image_tool}.py`, `code_executor/file_export_service.py:104-123`, `base/artifact_storage.py`; Tests: the existing test files for each (see analysis section 4)
**Interfaces:** Consumes Tasks 1,3. `get_file_system_toolkit` builds `PublishContext(user.id, request.conversation_id, request_uuid)` and injects the publisher where `file_repository` is injected today.
Test-first: yes — attach tool: path check via shared helper (not `CodeExecutorTool._validate_export_paths`), oversize refused by `os.stat` before any read; `WriteFileTool` returns no link and its description points to attach; no publisher injected -> "not delivered: storage not configured"; `test_write_and_attach_tools_receive_repository` updated.
- [ ] Remove `store_artifact` if no caller remains. `GenerateWorkspaceImageToolV2` is not touched.

#### Task 8: Sandbox link leaf module
**Files:** Create `src/codemie/service/file_service/sandbox_links.py`; Modify `agents/tools/agent.py:36`, `share_conversation_service.py:30-69`, `download_rules.py`; Test `tests/codemie/service/file_service/test_sandbox_links.py`
**Interfaces:** Produces `SANDBOX_FILE_RE` (share variant, backtick excluded), `collect_message_file_tokens(messages)`; no service imports.
Test-first: yes — backticked links are excluded; the three importers use one pattern; thought-only tokens are not collected.
- [ ] Delete `_thought_tokens`; `download_rules` no longer imports the share service.

#### Task 9: Share grant bound (Rule E)
**Files:** Modify `src/codemie/service/file_service/download_rules.py:91-135`; Test `test_download_rules.py`, `test_share_conversation_service.py`
Test-first: yes — foreign `workspace-<other>` token and foreign-user token pasted into history are refused; sharer's registered workspace artifact (`find_by_blob` by conversation id) allowed; token in history with `owner == shared_by_user_id` allowed; legacy `mcp_images` and `<uuid>_` links still pass.
- [ ] Implement spec section 7 (i) or (ii); do not alter other rules.

#### Task 10: Download name
**Files:** Modify `src/codemie/rest_api/routers/files.py:261`; Test `tests/codemie/rest_api/routers/test_files.py`
Test-first: yes — `artifacts/<turn>/report.xlsx` is sent as `report.xlsx`.
- [ ] Apply `PurePosixPath(name).name` after the uuid-prefix strip, before `_safe_disposition`.

#### Task 11: mcp-mock (working dir `/home/aliaksei_chumakou/codemie-dev/mcp-mock`; not a git repo, nothing committed)
**Files:** Modify `content_generators.py:53-54`; its tests if any
Test-first: no — emit nested `resource {uri, mimeType, blob}` and `resource_link` items, with names such as `closed_positions_analysis.xlsx`.

#### Task 12: mcp-proxy (working dir `/home/aliaksei_chumakou/codemie-dev/mcp-proxy`; not a git repo, nothing committed)
**Files:** Modify `radar_client.py:93-138`, `tests/test_translate.py`, `README.md`
Test-first: yes — `_translate_content` forwards embedded resource (uri, mimeType, blob/text) and resource link unflattened; `RADAR_MCP_URL` stays user-configured.

---
**Negative-constraint pass:** honored by Task 5 (no workflow post-processing, no `mcp_images` write), Task 7 (V2 image tool untouched), Task 9 (Rules A-D2 untouched), Task 1 (no `codemie.*` import), Task 6 (never "no owner"), Tasks 11-12 (not committed). No task touches the UI, audio, `resources/read`, or the enterprise package. Description reminder for the MR: list Tasks 11-12 separately and ask a reviewer to post `/sanity`.
