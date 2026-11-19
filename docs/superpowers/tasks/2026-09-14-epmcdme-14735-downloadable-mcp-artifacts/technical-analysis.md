# EPMCDME-14735 — Technical analyses (runs 1–4, consolidated)

One section per sdlc run, kept as written. Line numbers and symbols reflect each run's baseline; later runs supersede earlier ones. Current design: `spec.md`.

---

# Run 1 — `2026-09-14-epmcdme-14735-downloadable-mcp-artifacts`

## Technical Research

**Task**: mcp toolkit artifacts sandbox files
**Generated**: 2026-09-14T00:00:00Z
**Research path**: filesystem

---

### 1. Original Context

EPMCDME-14735: Enable downloadable chat artifacts for non-image MCP-generated files. MCP tools generate non-image files (xlsx, csv, json, zip, pdf) that are written to disk but are not available to the user as downloadable chat artifacts. Images work because toolkit.py's _post_process_tool_result() calls _convert_image_to_url() which saves bytes to file storage and mints a sandbox:/v1/files/... URL. Non-image resource-typed items fall through to _format_content_item()'s generic JSON dump fallback with no URL minted. The fix is to add a _convert_resource_to_url() sibling function in toolkit.py that handles resource-typed content items with non-image MIME types by decoding their base64 data field, calling write_file() on the storage backend, and returning a real sandbox: URL. Prior investigation identified: codemie/src/codemie/service/mcp/toolkit.py as the main change site (_post_process_tool_result, _convert_image_to_url, _format_content_item), codemie/src/codemie/service/mcp/models.py for MCPToolContentItem model, codemie/src/codemie/service/mcp/client.py for invocation, codemie_tools/base/string_serializer.py and file_object.py for URL encoding/decoding, codemie/src/codemie/repository/repository_factory.py for storage backend, codemie/src/codemie/rest_api/routers/files.py for file serving (no changes needed). Security consideration: generated artifacts must be scoped to the correct user/conversation/session. Existing image artifact behavior must remain unchanged.

---

### 2. Codebase Findings

#### Existing Implementations

- `src/codemie/service/mcp/toolkit.py` — Main change site. Contains the full post-processing pipeline:
  - `MIME_TO_EXTENSION` dict (lines 56–63): maps image MIME types to extensions; covers only image types.
  - `_save_screenshot_to_storage(image_data, mime_type)` (lines 352–379): decodes base64, calls `file_repo.write_file(name, mime_type, owner=MCP_IMAGES_SUBDIR, content)`, returns `file_obj.to_encoded_url()`. Uses `MCP_IMAGES_SUBDIR` as owner (shared for all users).
  - `_convert_image_to_url(item)` (lines 382–408): in-place mutation — sets `item.type = 'image_url'`, sets `item.image_url = {"url": "sandbox:/v1/files/{url}", "detail": "high"}`, clears `item.data` and `item.mimeType`. Falls back to inline base64 on storage error.
  - `_post_process_tool_result(tool_result)` (lines 411–424): iterates `tool_result.content`; only dispatches items where `item.type == 'image'` to `_convert_image_to_url`. Resource-typed items are not handled.
  - `_format_content_item(item)` (lines 427–451): handles `text`/`error` (returns text), `image_url` (returns markdown with sandbox URL), and everything else (JSON dumps `item.model_dump()`). Resource items fall into the JSON dump fallback with no URL minted.
  - `_convert_mcp_response_to_tool_message(tool_result)` (lines 454–475): calls `_post_process_tool_result`, then calls `_format_content_item` per item, joins with newlines.

- `src/codemie/service/mcp/models.py` — `MCPToolContentItem` Pydantic model (lines 392–443):
  - Fields: `type: str` (default `"text"`), `text: str | None`, `data: str | None` (holds base64 payload for both image and resource content), `image_url: dict[str, str] | None`, `mimeType: str | None`.
  - `is_image()` returns `True` for `type in ("image", "image_url")`. No `is_resource()` method exists.
  - `__str__` handles `"data"`, `"image"`, `"image_url"` types; all other types fall to `self.text` or a fallback string.

- `src/codemie/repository/repository_factory.py` — `FileRepositoryFactory` with `get_current_repository()` dispatching to GCP/Azure/AWS/FileSystem backends based on `config.FILES_STORAGE_TYPE`. `build_subdir(name)` sanitizes underscores to hyphens for Azure. `MCP_IMAGES_SUBDIR = FileRepositoryFactory.build_subdir(MCP_IMAGES_BASE)` is module-level; `MCP_IMAGES_BASE = "mcp_images"` is defined in `src/codemie/core/constants.py` line 71.

- `src/codemie_tools/base/file_object.py` — `FileObject.to_encoded_url()` (line 147): serializes `[mime_type, owner, name]` via `StringSerializer.serialize()`. `FileObject.from_encoded_url()` (line 157): deserializes 3-element list. `StringSerializer` uses base64 over a length-prefixed format.

- `src/codemie/rest_api/routers/files.py` — `GET /v1/files/{file_name}` endpoint. Decodes the encoded URL via `FileService.get_file_object(file_name)`, determines response handler by MIME type (includes binary `get_attachment_response` for unknown types). No changes needed for serving.

- `src/codemie/service/mcp/client.py` — `MCPConnectClient.invoke_tool()` returns `MCPToolInvocationResponse`. Execution context is threaded through but not forwarded into toolkit post-processing.

#### Architecture and Layers Affected

- **Service layer** (`src/codemie/service/mcp/toolkit.py`): primary change. Three module-level functions and one dispatch table in `_post_process_tool_result` must be extended.
- **Model layer** (`src/codemie/service/mcp/models.py`): `MCPToolContentItem` may need an `is_resource()` helper and an updated `__str__` case for a new `resource_url` type if one is introduced.
- **Repository/storage layer** (`src/codemie/repository/repository_factory.py`, `src/codemie/core/constants.py`): if a separate subdir constant for non-image artifacts is introduced it must go through `FileRepositoryFactory.build_subdir()` and be defined in `constants.py`.
- **API layer** (`src/codemie/rest_api/routers/files.py`): confirmed no changes needed; existing serving infrastructure handles any MIME type via attachment or inline response.

#### Integration Points

- `FileRepositoryFactory.get_current_repository()` → `write_file(name, mime_type, owner, content)` — same call chain used by `_save_screenshot_to_storage`. Owner value determines namespace/isolation.
- `FileObject.to_encoded_url()` → produces the token appended to `sandbox:/v1/files/` in the URL minted for the LLM.
- `MCPExecutionContext` (`src/codemie/service/mcp/models.py`): carries `user_id`, `conversation_id`, `assistant_id`. Currently not passed into `_post_process_tool_result` or `_save_screenshot_to_storage`. Needed for user-scoped artifact ownership.
- `_limit_output_content` in `MCPTool` (lines 262–299): builds a text string from content items where `not item.is_image()` for token counting. Post-processed resource items would not be images, so their text/URL representation affects token budgets.

#### Patterns and Conventions

- In-place item mutation: `_convert_image_to_url` mutates the `MCPToolContentItem` in place (type reassignment, clearing data). The sibling function should follow the same pattern.
- Storage owner as subdir: images use `MCP_IMAGES_SUBDIR` (a shared per-deployment constant) as `owner`. If user-scoped storage is required, the execution context's `user_id` or `conversation_id` must be threaded into `_post_process_tool_result`.
- Fallback-on-error pattern: `_convert_image_to_url` falls back to inline base64 on `IOError`/`OSError`/`ValueError`/`base64.binascii.Error` rather than crashing. The resource function should apply the same defensive pattern.
- Azure compatibility: any new storage subdir constant must pass through `FileRepositoryFactory.build_subdir()` to replace underscores with hyphens when `FILES_STORAGE_TYPE == "azure"`.
- URL prefix convention: `sandbox:/v1/files/{encoded_url}` — all stored artifacts use this prefix in the text returned to the LLM.

---

### 3. Documentation Findings

#### Guides and Architecture Docs

- `.ai-run/guides/integration/mcp-integration.md` — present. Covers MCP config/auth router registration, slow-start timeout tuning (`MCP_SERVER_INIT_TIMEOUT`, `MCP_CONNECT_INIT_TIMEOUT`, `MCP_CLIENT_TIMEOUT`). Does not cover artifact post-processing or file storage scoping.
- `.ai-run/guides/data/database-patterns.md`, `.ai-run/guides/architecture/layered-architecture.md` — available in the guide set but not read for this task; relevant for storage and service layer conventions.

#### Architectural Decisions

- No ADR or inline decision comment found in the files read that addresses ownership/scoping policy for MCP-generated artifacts specifically. The existing image approach (shared `MCP_IMAGES_SUBDIR` owner) is an implicit convention, not a documented decision.

#### Derived Conventions

- New module-level functions in `toolkit.py` should follow the naming and signature style of `_save_screenshot_to_storage` and `_convert_image_to_url` (underscore-prefixed, takes an `MCPToolContentItem`, mutates in place or returns a URL string).
- Error handling in post-processing follows a log-and-fallback pattern rather than surfacing exceptions to the LLM.
- Storage subdirectory constants live in `src/codemie/core/constants.py` and are materialized via `FileRepositoryFactory.build_subdir()` at module load.

---

### 4. Testing Landscape

#### Existing Coverage

- `tests/codemie/service/mcp/test_toolkit_mcp_tool.py` — covers `MCPTool` initialization, `execute()` path A/B, timeout error conversion, and context-aware tool. Does not test post-processing functions.
- `tests/codemie/service/mcp/test_toolkit_mcp_toolkit_creation.py`, `test_toolkit_mcp_toolkit_factory.py`, `test_toolkit_mcp_toolkit_access.py`, `test_toolkit_mcp_toolkit_init.py`, `test_toolkit_mcp_toolkit_validation.py` — cover toolkit lifecycle, caching, schema creation, and tool access.
- None of these test files contain references to `_post_process_tool_result`, `_convert_image_to_url`, `_format_content_item`, or `_save_screenshot_to_storage`.

#### Testing Framework and Patterns

- pytest (from `pytest.ini` at repo root).
- `unittest.TestCase` subclasses used in MCP toolkit tests.
- `unittest.mock.MagicMock`, `AsyncMock`, `patch` used for external dependencies.
- `MCPServerConfig`, `MCPToolInvocationResponse`, `MCPToolContentItem` are instantiated directly in tests (no factories or fixtures).
- `FileRepositoryFactory` and storage backends would need mocking in new post-processing tests.

#### Coverage Gaps

- `_save_screenshot_to_storage` — no tests.
- `_convert_image_to_url` — no tests (including error fallback path).
- `_post_process_tool_result` — no tests.
- `_format_content_item` — no tests.
- `_convert_mcp_response_to_tool_message` — no tests.
- The new `_convert_resource_to_url` function (and any changes to `_post_process_tool_result` dispatch) will also have no pre-existing tests.

---

### 5. Configuration and Environment

#### Environment Variables

- `FILES_STORAGE_TYPE` — `Literal["filesystem", "aws", "azure", "gcp"]`, default `"filesystem"`. Determines which storage backend `FileRepositoryFactory` instantiates.
- `MCP_TOOL_TOKENS_SIZE_LIMIT` — default `30000`. Controls truncation in `_limit_output_content`.
- `MCP_CLIENT_TIMEOUT` — default `300.0` seconds. Per-invocation timeout; no direct impact on storage writes.
- `MCP_CONNECT_URL` — default `"http://localhost:3000"`. Bridge URL for tool invocation.
- `AZURE_STORAGE_CONNECTION_STRING`, `AZURE_STORAGE_ACCOUNT_NAME` — used only when `FILES_STORAGE_TYPE == "azure"`.
- `AWS_S3_REGION`, `AWS_S3_BUCKET_NAME` — used only when `FILES_STORAGE_TYPE == "aws"`.

#### Configuration Files

- `src/codemie/configs/config.py` — primary config class; all env vars above are read here.
- `src/codemie/core/constants.py` — `MCP_IMAGES_BASE = "mcp_images"` (line 71). Any new artifact subdir constant belongs here.
- `.env.example` — present at repo root; documents required env vars.

#### Feature Flags and Deployment Concerns

- No feature flags found governing MCP artifact post-processing specifically.
- Azure storage: `FileRepositoryFactory.build_subdir()` must be called for any new subdir constant to ensure Azure compatibility (underscores → hyphens).
- Existing image path uses `MCP_IMAGES_SUBDIR` as a shared owner with no per-user isolation. If user-scoped ownership is required for non-image artifacts, `MCPExecutionContext.user_id` or `conversation_id` must be threaded into the post-processing call chain — this is a behavioral extension beyond the image pattern.

---

### 6. Risk Indicators

- **No tests for the three functions being changed**: `_post_process_tool_result`, `_convert_image_to_url` (for regression), and `_format_content_item` all lack test coverage. Any new `_convert_resource_to_url` function also starts with zero coverage.
- **Execution context not propagated to post-processing**: `_post_process_tool_result` receives only the `MCPToolInvocationResponse` with no execution context. Threading `user_id`/`conversation_id` in requires a signature change to `_post_process_tool_result` and `_convert_mcp_response_to_tool_message`, which is called from `MCPTool._post_process_output_content`. This is a wider change surface than the task description implies if user-scoped ownership is enforced.
- **Shared owner for images**: current image storage uses `MCP_IMAGES_SUBDIR` as owner for all users. Speculative: if the same shared-owner pattern is reused for non-image artifacts, the security requirement ("scoped to the correct user/conversation/session") may not be met.
- **MIME type to extension for non-image types**: `MIME_TO_EXTENSION` only covers images. Non-image MIME types (xlsx, csv, json, zip, pdf) need extension mapping. The stdlib `mimetypes` module is already imported in `files.py` and can be used, but MCP content items may carry arbitrary MIME types or `None`.
- **`MCPToolContentItem` type field for resource items**: the MCP protocol uses `type: "resource"` for non-image binary content. The current model and `_format_content_item` have no branch for `"resource"`. Misidentification of item type would silently fall to the JSON dump fallback.
- **`is_image()` and `_limit_output_content`**: `_limit_output_content` filters `not item.is_image()` when computing token counts. Post-processed resource items (if typed `"resource_url"` or left as `"resource"`) will be counted in the token budget as text. If the URL text is short, this is benign; if the full base64 blob is still present at counting time, token counts spike.
- **Azure subdir naming**: any new constant for a non-image artifact subdir must pass through `FileRepositoryFactory.build_subdir()`.
- **`MCPToolContentItem.__str__` and `is_image()`**: neither handles `"resource"` or a new `"resource_url"` type. The `__str__` method falls to the default `self.text` branch, which returns `"<No text content available>"` for resource items with no text field. This affects how streamed content appears in logs and callbacks.

---

### 7. Summary for Complexity Assessment

The change is contained to a single service module (`src/codemie/service/mcp/toolkit.py`) with one model file (`src/codemie/service/mcp/models.py`) as a secondary touch point and a constants file (`src/codemie/core/constants.py`) if a new subdir constant is introduced. The core mechanical work — decoding base64, calling `write_file`, minting a `sandbox:/v1/files/` URL — directly parallels the existing `_save_screenshot_to_storage` / `_convert_image_to_url` pattern. The file-serving router requires no changes; it already handles arbitrary MIME types via attachment responses.

The primary technical novelty is the security scoping requirement. Images currently use a shared `MCP_IMAGES_SUBDIR` owner with no per-user isolation. If non-image artifacts must be scoped to a user or conversation, `MCPExecutionContext` must be threaded into `_post_process_tool_result` and `_save_screenshot_to_storage`, which changes the call signatures of three functions and their sole caller in `_convert_mcp_response_to_tool_message` / `MCPTool._post_process_output_content`. Whether image artifacts also need this change is a design decision not yet resolved in the codebase. This scoping question is the largest ambiguity in the implementation path.

Test coverage is the most significant risk: none of the five functions in the post-processing pipeline have tests today. New tests must mock `FileRepositoryFactory.get_current_repository()` and exercise the happy path, the storage error fallback, resource items with various MIME types, and the unchanged image path as a regression check. The MCP test suite is large and follows established `unittest.TestCase` + `unittest.mock` patterns, so new tests can follow those conventions directly.

---

### 8. External References

None named by the task beyond the files within this repository, all of which resolved and were read. Key facts sourced from each:

- `src/codemie/service/mcp/toolkit.py`: `_post_process_tool_result` handles only `item.type == 'image'`; `_format_content_item` falls to `json.dumps(item.model_dump())` for all other types; `_convert_image_to_url` uses `MCP_IMAGES_SUBDIR` as owner and emits `sandbox:/v1/files/{url}`.
- `src/codemie/service/mcp/models.py`: `MCPToolContentItem.data` holds base64 for both image and resource types; no `is_resource()` method; `MCPExecutionContext` carries `user_id`, `conversation_id`, `assistant_id`.
- `src/codemie_tools/base/file_object.py`: `to_encoded_url()` serializes `[mime_type, owner, name]`; `from_encoded_url()` requires exactly 3 deserialized values.
- `src/codemie/repository/repository_factory.py`: `MCP_IMAGES_SUBDIR = FileRepositoryFactory.build_subdir(MCP_IMAGES_BASE)`; `build_subdir` replaces `_` with `-` for Azure only.
- `src/codemie/core/constants.py` line 71: `MCP_IMAGES_BASE = "mcp_images"`.
- `src/codemie/rest_api/routers/files.py`: no changes needed; `GET /v1/files/{file_name}` serves binary content as attachment for unknown MIME types via `get_attachment_response`.

---

# Run 2 — `2026-09-18-epmcdme-14735-filesystem-toolkit-artifacts`

## Technical Research

**Task**: filesystem toolkit, file repository sandbox URLs, downloadable chat artifacts, agent tool output post-processing
**Generated**: 2026-09-18
**Research path**: codegraph

---

### 1. Original Context

EPMCDME-14735 "Enable downloadable chat artifacts for non-image MCP-generated files".

USER'S LOCAL REPRO (this run's actual trigger): user created a CodeMie assistant with only 'Toolkit: FileSystem (ToolSet.FILE_SYSTEM)', asked it to create a json and a csv file. The files WERE created on disk, but the chat returned only plain text (a local path), no downloadable artifact. Chat log: http://localhost:5173/share/conversations/jtoFunayxT65

JIRA TICKET BODY:
Summary: Enable assistant chat to provide working user-facing downloads for non-image artifacts generated by agents and exposed through MCP/filesystem resources.
Some agents generate report artifacts on disk through shell/filesystem MCP, but the files are not available to the user as downloadable chat artifacts. Representative scenario: RADAR CLI shell assistant writes closed_positions_analysis.xlsx/.csv/.json under a session dir like /home/codemie/<session>/. The assistant returned a local `filesystem:` path; chat did not treat it as a downloadable artifact. Images work because image MIME responses are ingested into the sandbox file store and rendered inline/downloadable. Non-image artifacts (xlsx, csv, json) remain MCP resources without a blob or sandbox URL => dead local filesystem link.

Acceptance Criteria (verbatim):
1. Given an MCP tool exposes or returns a generated non-image artifact (xlsx, csv, json, zip, pdf), when the assistant needs to provide it to the user, then the artifact is ingested into sandbox-backed / user-facing downloadable storage.
2. Given a generated artifact is ingested, when the response is rendered, then chat displays a Download chip / downloadable artifact with filename, size, MIME type, working clickable URL.
3. Given the RADAR CLI repro generates closed_positions_analysis.xlsx, when the user asks to download it, then the user gets a working download.
4. Given an MCP resource has no blob or cannot be ingested, chat must NOT show a fake/non-working `filesystem:` download link.
5. Supports at least: xlsx, csv, json, zip, pdf.
6. Generated artifacts are scoped to the correct user/conversation/session; must not expose another user's temp files from a shared MCP host.
7. Existing image artifact behavior remains unchanged.
8. Optional but preferred: explicit "attach file to conversation" tool/API so an agent can register a generated file path as a user-facing downloadable artifact without image/media-specific semantics.

Retention policy is OUT OF SCOPE (handled by cloud storage lifecycle policies).

PRIOR WORK ALREADY ON THIS BRANCH (EPMCDME-14735_downloadable-mcp-artifacts): the MCP-resource half is reportedly already implemented — _save_artifact_to_storage() and _convert_resource_to_url() in src/codemie/service/mcp/toolkit.py, wired into _post_process_tool_result(); UI side is a single replaceAll('sandbox:/v1/files/', ...) in codemie-ui Markdown.utils.ts.

EXISTING RESEARCH DOC CLAIM TO VERIFY (from /home/aliaksei_chumakou/Downloads/RealFileProblem_EPMCDME-14735.md):
The real repro does NOT go through MCP. It goes through FileSystemToolkit (src/codemie_tools/data_management/file_system/), a separate still-unfixed pipeline. Specific claims to VERIFY against current source (line numbers may have drifted):
- FileSystemToolkit declares `file_repository: Optional[Any] = None` and get_toolkit() populates it.
- get_tools() passes file_repository to GenerateImageTool and CodeExecutorTool, but NOT to the six "admin tools": ReadFileTool, ListDirectoryTool, WriteFileTool, CommandLineTool, DiffUpdateFileTool, ReplaceStringTool. Those classes don't even declare a file_repository field.
- WriteFileTool.execute() returns the bare string f"File written successfully to {file_path}".
- CommandLineTool.execute() returns raw subprocess stdout/stderr.
- The working reference pattern is the 3-step primitive: get bytes -> file_repository.write_file(name, mime_type, content, owner) -> f"sandbox:/v1/files/{stored_file.to_encoded_url()}". Used by CodeExecutorTool via FileExportService.store_exported_bytes (codemie_tools/data_management/code_executor/file_export_service.py) and by GenerateImageTool._store_b64_image.
- CodeExecutorTool's export_files is an LLM-supplied args_schema parameter (get_code_executor_input_schema in code_executor_tool.py), validated by _validate_export_paths (security only: rejects absolute paths, paths outside workdir, /proc /etc /root).

RESEARCH QUESTIONS I NEED ANSWERED:
1. Verify or refute each claim above against CURRENT source, with current file:line references. Is the FileSystemToolkit admin-tool path really missing file_repository wiring?
2. What is the exact current signature/contract of file_repository.write_file(...) and StoredFile.to_encoded_url()? Who implements FileRepositoryFactory / the repository interface, and how is user/owner scoping enforced (AC #6)?
3. Exactly how does FileExportService.store_exported_bytes work, including any size caps, MIME detection, and error handling? Is there an existing shared helper both CodeExecutor and a new FileSystem fix could call, or would one need extracting?
4. How does _validate_export_paths work and is it reusable outside CodeExecutorTool?
5. What does the already-implemented MCP half (src/codemie/service/mcp/toolkit.py _save_artifact_to_storage / _convert_resource_to_url / _post_process_tool_result) look like now — is it complete, and does it establish a pattern/helper the FileSystem fix should reuse rather than duplicate?
6. Is there any generic post-processing hook on tool results (agent-level) where a sandbox-URL minting step could live once for ALL toolkits, instead of per-tool?
7. Existing tests: what covers FileSystemToolkit tools, CodeExecutorTool export, file_export_service, and the MCP toolkit artifact path? Where do new tests belong, and what fixtures/mocks exist for file_repository?
8. How are the FileSystem toolkit tools registered/configured (ToolSet.FILE_SYSTEM), and does get_toolkit get called with file_repository in the real assistant path (i.e. is self.file_repository actually non-None at runtime for a FILE_SYSTEM-only assistant)? This is critical — if it's None at runtime the fix needs a different wiring point.
9. Risk indicators: size limits, binary vs text handling (WriteFileTool writes text; xlsx is binary produced by CommandLineTool), path traversal, user isolation, and anything touching security/auth.

---

### 2. Codebase Findings

#### Existing Implementations

**The FileSystem toolkit (the unfixed path)**

- `src/codemie_tools/data_management/file_system/toolkit.py`
  - `FileSystemToolkit` (L64-72) declares `root_directory`, `activate_command`, `user_id`, `file_repository: Optional[Any] = None` (L68), `image_generator`, `chat_model`, `code_isolation`, `input_files`.
  - `_is_file_system_tools_enabled()` (L74-82) — reads env `FILE_SYSTEM_TOOLS_ENABLED`, default `"false"`.
  - `_is_code_executor_enabled()` (L84-94) — reads env `CODE_EXECUTOR_ENABLED`, default `"false"`.
  - `get_tools()` (L116-146): constructs `GenerateImageTool(image_generator=…, file_repository=self.file_repository, user_id=self.user_id or "")` (L118-122); appends `CodeExecutorTool(file_repository=self.file_repository, user_id=self.user_id, input_files=self.input_files)` (L126-132) only when `CODE_EXECUTOR_ENABLED`; under `FILE_SYSTEM_TOOLS_ENABLED` appends six admin tools (L136-143) constructed with **only** `root_dir` / `activate_command` / `llm_model` — `self.file_repository` and `self.user_id` are not forwarded to any of them.
  - `get_toolkit()` (L148-169): classmethod accepting `configs`, `file_repository`, `chat_model`, `image_generator`, `input_files`; reads `root_directory`, `activate_command`, `user_id` from `configs` and passes `file_repository=file_repository` into the model.
  - `FileSystemToolkitUI` (L48-61): UI-visible tool list of 8 tools; `get_tools_ui_info(is_admin)` (L96-114) shows all 8 only for admin + env flag, else `GENERATE_IMAGE_TOOL` (+ code executor when enabled).

- `src/codemie_tools/data_management/file_system/tools.py`
  - `ReadFileTool` (L51-75), `ListDirectoryTool` (L84-107) — read-only, `root_dir` only, `is_safe() -> True`.
  - `WriteFileTool` (L115-135): fields are `name`, `args_schema`, `description`, `root_dir` only. `execute(file_path, text)` (L121-135) resolves via `get_relative_path`, `create_folders`, opens with `mode = "w"` and `encoding="utf-8"` (L129-131) — **text-only write path** — and returns the bare string `f"File written successfully to {file_path}"` (L133).
  - `CommandLineTool` (L159-200): fields `root_dir`, `activate_command`, `timeout: int = DEFAULT_TIMEOUT` (60, L44). `execute()` (L172-200) runs `subprocess.run(["/bin/bash", "-c", full_command], cwd=work_dir, shell=False, text=True, capture_output=True, timeout=…)` and returns the **tuple** `(result.stdout, result.stderr, result.returncode, command_start_time)` (L200) — note it returns a tuple, not a string; `CodeMieTool._post_process_output_content` JSON-serializes it.
  - `CommandLineTool.sanitize_command()` (L202-240): static denylist of 12 regex patterns (rm -rf, dd, backticks, `$(`, pipes into curl/wget/nc, chaining into interpreters, fork bombs).
  - `DiffUpdateFileTool` (L243-267), `ReplaceStringTool` (L315-349, uses `OHEditor`) — also `root_dir`-only.
  - `utils.py`: `get_relative_path` (L25-29), `get_validated_relative_path` (L32-41, raises `FileValidationError` if outside root; explicitly permits symlinks pointing back inside root), `is_relative_to` (L44-53), `create_folders` (L56-65).

**The already-implemented MCP half (the fixed path)**

- `src/codemie/service/mcp/toolkit.py`
  - `MIME_TO_EXTENSION` (L56-63) — image-only map.
  - `_save_screenshot_to_storage(image_data, mime_type)` (L352-379) — `FileRepositoryFactory().get_current_repository()`, `owner = MCP_IMAGES_SUBDIR`, `uuid4().hex + ext`, returns `file_obj.to_encoded_url()`.
  - `_save_artifact_to_storage(data_b64, mime_type)` (L382-406) — `base64.b64decode(data_b64, validate=True)`, `mimetypes.guess_extension(mime_type)` falling back to `.bin`, name `f"{uuid4()}.{ext}"`, `owner=MCP_ARTIFACTS_SUBDIR`, returns `file_obj.to_encoded_url()`. No size cap.
  - `_convert_image_to_url(item)` (L409-436) — sets `type='image_url'`, falls back to inline `data:` URI on error.
  - `_convert_resource_to_url(item)` (L438-471) — returns early if `item.type != "resource"`; reroutes `image/*` MIME to the image pipeline; if `not item.data` logs a warning and leaves the item untouched; on success sets `item.type = "resource_url"` and `item.text = f"[Download](sandbox:/v1/files/{url})"`; on `IOError/OSError/ValueError/binascii.Error` sets `item.text = "[File could not be saved]"` (AC #4 behavior).
  - `_post_process_tool_result(tool_result)` (L473-488) — dispatches `image` → `_convert_image_to_url`, `resource` → `_convert_resource_to_url`.
  - `_format_content_item` (L491-518) handles `resource_url` by returning `item.text` (L513-514).
  - `_convert_mcp_response_to_tool_message` (L521-542) calls `_post_process_tool_result` then joins formatted parts. Reached from `MCPTool._post_process_output_content` (L301-305), which overrides the `CodeMieTool` hook.

**The shared 3-step primitive (the reference pattern)**

- `src/codemie_tools/data_management/code_executor/file_export_service.py`
  - `FileExportService.__init__(file_repository=None, user_id="")` (L46-55).
  - `store_exported_bytes(filename, content: bytes) -> Optional[str]` (L103-125) — documented as the "shared kernel for any execution backend that delivers file bytes itself". Returns `None` and logs a warning when `file_repository` is falsy (L109-111). Derives extension via `Path(filename).suffix.lower().lstrip(".")`, MIME via `_determine_mime_type`, name `f"{uuid4()}_{os.path.basename(filename)}"`, calls `write_file(name=…, mime_type=…, content=…, owner=self.user_id)`, returns the **pre-formatted sentence** `f" File '{basename}', URL \`sandbox:/v1/files/{stored_file.to_encoded_url()}\`"` (L122). Catches bare `Exception` → logs error, returns `None` (L123-125). **No size cap anywhere in this method.**
  - `_determine_mime_type(extension)` (L190-203) — `mimetypes.guess_type(f"file.{ext}")`, default `application/octet-stream`.
  - `export_files_from_execution` (L57-85), `collect_files_from_execution` (L87-101), `_export_single_file` (L127-155, pulls file to a `tempfile.TemporaryDirectory`, reads bytes, delegates to `store_exported_bytes`).
- `src/codemie_tools/data_management/file_system/generate_image_tool.py`
  - `GenerateImageTool` (L32-38) declares `image_generator`, `file_repository: Optional[Any] = Field(exclude=True, default=None)` (L37), `user_id: str = ""` (L38).
  - `_store_b64_image(b64_data)` (L55-65) — the same primitive inline: `write_file(name=f"{uuid4()}.png", mime_type="image/png", content=base64.b64decode(...), owner=self.user_id)` then `return f"sandbox:/v1/files/{stored_file.to_encoded_url()}"` (L65).

**Repository layer**

- `src/codemie/repository/base_file_repository.py` — `FileRepository` ABC: `write_file(self, name: str, mime_type: str, owner: str, content: Any = None) -> FileObject` (L52-54), plus `read_file` (L56-69), `delete_file` (L71-79), `create_directory` (L81-93).
- `src/codemie/repository/repository_factory.py` — `FileStorageType` enum (L25-29: gcp/aws/filesystem/azure); `FileRepositoryFactory.get_current_repository()` (L33-38) → `_get_repository()` (L40-60) → one of `GCPFileRepository`, `AzureFileRepository`, `FileSystemRepository`, `AWSFileRepository`; `get_current_storage_type()` (L62-69) reads `config.FILES_STORAGE_TYPE`, defaulting to `FILE_SYSTEM`; `build_subdir(name)` (L71-80) replaces `_` with `-` for Azure container-name rules. Module constants `MCP_IMAGES_SUBDIR` (L83) and `MCP_ARTIFACTS_SUBDIR` (L84) derive from `MCP_IMAGES_BASE` / `MCP_ARTIFACTS_BASE` in `codemie/core/constants`.
- Implementations, all with `owner` as the top-level isolation boundary:
  - `AWSFileRepository.write_file` (aws_file_repository.py:37-46) — key is `f"{owner}/{name}"` (`_get_full_key`, L34-35) inside a single root bucket.
  - `AzureFileRepository.write_file` (azure_file_repository.py:52-…) — `owner` is the **container name**, auto-created via `_get_container` (L37-50).
  - `GCPFileRepository.write_file` (gcp_file_repository.py:44-49) — `owner` is the **bucket name**, auto-created via `_get_bucket` (L30-42).
  - `FileSystemRepository.write_file` (file_system_repository.py:45-56) — `_resolve_safe_path(config.FILES_STORAGE_DIR, owner, name)` (L31-37) raises `ValidationException` on escape; picks `'wb'` vs `'w'` by `isinstance(content, bytes)` (L50).
- `src/codemie_tools/base/file_object.py` — `FileObject.to_encoded_url()` (L147-154) is `StringSerializer.serialize([self.mime_type, self.owner, self.name])`; `from_encoded_url` (L156-175) round-trips the same three values. Also `is_image()` (L177-184), `is_text_based()` (L186-193), `bytes_content()` (L214-231), `string_content()` (L233-…). There is no separate `StoredFile` type — `write_file` returns a `FileObject`.
- `src/codemie/rest_api/routers/files.py` — `write_file` endpoint (L246-269) is the authenticated upload path: enforces `config.FILES_STORAGE_MAX_UPLOAD_SIZE` (L253-259), guesses MIME server-side, writes with `owner=user.id`, returns `result.to_encoded_url()`. This is the only place in the surveyed code where an upload size cap is enforced.
- `src/codemie/service/skill_file_storage.py` — `SkillFileStorage` (L34-45) is an existing precedent for a thin owner-scoping wrapper: `self._owner = f"skill-{skill_id}"` and `write_file(name, mime_type, content)` delegating to the repo.

**Runtime wiring (RQ8)**

- `src/codemie/service/tools/toolkit_service.py` — `get_toolkit_methods()` (L160-…) maps `ToolSet.FILE_SYSTEM` to a lambda calling `ToolkitSettingService.get_file_system_toolkit(assistant, assistant.project, user, llm_model, request_uuid, request.tools_config …)` (L185-192).
- `src/codemie/service/tools/toolkit_settings_service.py` — `get_file_system_toolkit()` (L190-231): builds `configs = {"user_id": user.id}` (L202-204), optionally adds `code_fields`, reads `SettingsService.get_file_system_config(...)` (L218-220) to set `root_directory` / `activate_command` when a FILE_SYSTEM setting exists, resolves `chat_model`, and calls
  `FileSystemToolkit.get_toolkit(configs=configs, file_repository=FileRepositoryFactory.get_current_repository(), chat_model=chat_model, image_generator=image_generator, input_files=input_files).get_tools()` (L225-231).
  **So at runtime `self.file_repository` is non-None and `self.user_id == user.id` for a FILE_SYSTEM-only assistant.** `FileRepositoryFactory.get_current_repository()` is called unconditionally, independent of any setting.
- `SettingsService.get_file_system_config` (settings.py:1668-1683) returns a `FileSystemConfig` from `CredentialTypes.FILE_SYSTEM` credentials, or `None` — when `None`, `root_directory` stays at the toolkit default `"."`.

#### Architecture and Layers Affected

- **Tool layer (`src/codemie_tools/`)** — `data_management/file_system/{toolkit.py,tools.py,generate_image_tool.py,utils.py}`, `data_management/code_executor/{code_executor_tool.py,file_export_service.py}`, `base/codemie_tool.py`, `base/file_object.py`.
- **Service / wiring layer (`src/codemie/service/`)** — `tools/toolkit_service.py` (ToolSet dispatch), `tools/toolkit_settings_service.py` (constructs the toolkit with the repository), `settings/settings.py` (FILE_SYSTEM credentials), `mcp/toolkit.py` (the MCP artifact pipeline).
- **Repository layer (`src/codemie/repository/`)** — `base_file_repository.py`, `repository_factory.py`, and the four backend implementations.
- **REST layer (`src/codemie/rest_api/routers/files.py`)** — serves and accepts `/v1/files/...`; the `sandbox:/v1/files/<encoded>` URL the UI rewrites resolves here.
- **UI (separate repo, `codemie-ui`)** — a single `replaceAll('sandbox:/v1/files/', …)` in `Markdown.utils.ts`; not in this repository and not indexed here.

#### Integration Points

- Tool output → LLM message: `CodeMieTool._run` (codemie_tool.py:77-94) → `_limit_output_content` → `_post_process_output_content`.
- Toolkit construction: `ToolkitService.get_toolkit_methods` → `ToolkitSettingService.get_file_system_toolkit` → `FileSystemToolkit.get_toolkit` → `get_tools`.
- Storage: any tool → `FileRepository.write_file(name, mime_type, owner, content)` → backend (S3 key prefix / Azure container / GCS bucket / local dir under `FILES_STORAGE_DIR`).
- URL round-trip: `FileObject.to_encoded_url()` ⇄ `FileObject.from_encoded_url()` via `StringSerializer` (mime_type, owner, name) — the owner is *embedded in the URL*, which is how `/v1/files/` locates the blob.
- External: LiteLLM / Azure OpenAI image generation (`_build_image_generator`, toolkit_settings_service.py:53-94); sandbox pods / jobs for the code executor.

#### Patterns and Conventions

- **The 3-step artifact primitive**, repeated verbatim in three places today: get bytes → `file_repository.write_file(name, mime_type, content, owner)` → `f"sandbox:/v1/files/{file_obj.to_encoded_url()}"`. Implementations: `FileExportService.store_exported_bytes` (file_export_service.py:103-125), `GenerateImageTool._store_b64_image` (generate_image_tool.py:55-65), `_save_artifact_to_storage` + `_convert_resource_to_url` (mcp/toolkit.py:382-471). They differ in name generation (`uuid4()_basename` vs `uuid4().png` vs `uuid4().ext`), in owner (`self.user_id` vs `MCP_ARTIFACTS_SUBDIR`), and in return shape (sentence vs bare URL vs markdown link). There is **no single shared helper** all three call.
- Tools subclass `CodeMieTool` (pydantic `BaseTool`); optional collaborators are declared as `Optional[Any] = Field(exclude=True, default=None)` (see `GenerateImageTool` L36-37) so they are excluded from schema serialization.
- `is_safe(args)` (codemie_tool.py:48-55) defaults to `False`; read-only tools override it to `True` (`ReadFileTool` L57-58, `ListDirectoryTool` L92-93).
- Per-tool output post-processing is done by overriding `_post_process_output_content` (the MCP path does exactly this at mcp/toolkit.py:301-305).
- Owner-scoping wrapper precedent: `SkillFileStorage` (skill_file_storage.py:34-45).
- Env-gated tool exposure via `os.getenv(...) == "true"` static helpers on the toolkit class.
- Security validation lives as `@staticmethod` on the tool (`_validate_export_paths`, `sanitize_command`) rather than in a shared security module.

---

### 3. Documentation Findings

#### Guides and Architecture Docs

Guides exist under `.ai-run/guides/` (41 files). Directly relevant:

- `.ai-run/guides/agents/custom-tool-creation.md` — short. Two rules: put reusable external capabilities in `codemie_tools`, platform-facing adapters in `codemie/agents/tools`; extend the nearest existing toolkit package rather than duplicating; add narrow unit tests near the tool package under `tests/codemie_tools/` when tool schema, serialization, or provider behavior changes.
- Also nominally in scope but not read in this pass: `.ai-run/guides/agents/agent-tools.md`, `.ai-run/guides/data/repository-patterns.md`, `.ai-run/guides/development/security-patterns.md`, `.ai-run/guides/testing/testing-patterns.md`, `.ai-run/guides/quality-gates.md`.

No guide was found that documents the `sandbox:/v1/files/` URL scheme, the artifact-storage primitive, or owner-scoping rules for generated artifacts.

#### Architectural Decisions

- No ADR directory surfaced. Decisions are recorded as inline comments:
  - `file_export_service.py:106-107` explicitly frames `store_exported_bytes` as a "shared kernel for any execution backend that delivers file bytes itself" — i.e. it was already designed for reuse beyond the code executor.
  - `mcp/toolkit.py:443-446` documents the deliberate choice to reroute `image/*` resources into the image pipeline and to leave data-less resource items untouched.
  - `tools.py:188-190` records the `shell=False` + explicit `/bin/bash -c` decision with `sanitize_command` as defense in depth.
  - `repository_factory.py:74-76` records the Azure container-name underscore constraint.
  - `code_executor_tool.py:270-275` records that the workdir is interpolated unquoted into a bash wrapper, hence the restricted charset in `_get_user_workdir`.

#### Derived Conventions

- The UI recognizes exactly one artifact scheme: the literal prefix `sandbox:/v1/files/`. Any backend-produced download must emit that prefix followed by `FileObject.to_encoded_url()`.
- User isolation is carried entirely by the `owner` argument to `write_file`, which becomes the bucket/container/key-prefix/subdirectory and is also embedded in the encoded URL.
- Existing non-MCP artifact producers use `owner=self.user_id`; the MCP path instead uses a shared constant subdir (`MCP_ARTIFACTS_SUBDIR` / `MCP_IMAGES_SUBDIR`), i.e. the two halves of the ticket currently scope artifacts differently.

---

### 4. Testing Landscape

#### Existing Coverage

- `tests/codemie_tools/data_management/test_file_system_toolkit.py` — `TestFileSystemToolkit`, fixture `toolkit = FileSystemToolkit.get_toolkit(configs={})` (L32-34), i.e. **constructed with `file_repository=None`**. Tests assert tool *counts and types* only: `test_get_tools_ui_info_admin_without_env_var` (L36, expects 1), `..._admin_with_env_var` (L42, expects 7), `..._non_admin` (L49), `..._non_admin_with_env_var` (L55), `test_get_tools_non_admin` (L62, expects exactly 1 tool and asserts each admin tool absent), `test_get_tools_without_env_var` (L78), `test_get_tools_with_env_var` (L88, expects exactly 7 and asserts each admin tool present), `test_get_tools_with_root_directory` (L103, asserts `tool.root_dir == root_dir` for every tool having that attribute), `test_get_tools_code_executor_enabled` (L113), `test_get_tools_ui_info_code_executor_enabled_non_admin` (L121). The hard-coded counts 1 and 7 mean any change to the tool list breaks these tests.
- `tests/codemie_tools/data_management/file_system/test_file_system_tools.py` — `test_read_file_tool` (L34, parametrized against `tests/test_file.txt`), `TestFileSystemTools.setUp` (L41-42), `test_list_directory_tool` (L44), `test_write_file_tool` (L50-55, asserts the literal `'File written successfully to test_file.txt' in result` — a substring assertion, so an appended download link would not break it), `test_command_line_tool` (L57-63, unpacks the 4-tuple return), then ~14 `sanitize_command` denylist tests (L65-146) and `subprocess.run` patch tests `test_command_line_tool_uses_shell_false` (L148) and `test_command_line_tool_uses_bash_c_list` (L157). These tests write to the real `tests/` directory.
- `tests/codemie_tools/data_management/code_executor/test_export_path_validation.py` — covers `_validate_export_paths`: `test_validate_export_paths_falsy_input_passes`, `..._allowed_relative_passes`, `..._normalizes_to_inside_workdir`, `..._absolute_path_raises`, `..._traversal_raises`, using a `WORKDIR` constant.
- `tests/codemie/service/mcp/test_toolkit_post_processing.py` — covers the MCP artifact path: `test_happy_path_mutates_item` (L108), `test_storage_error_sets_error_text` (L122), `test_image_mime_type_routed_to_image_pipeline` (L133), `test_resource_item_dispatched` (L156), `test_image_item_routes_to_image_converter` (L169), `test_text_item_unchanged` (L176), constructing `MCPToolContentItem` / `MCPToolInvocationResponse` directly.
- Other code-executor suites: `test_code_executor_tool.py`, `test_execution_modes.py`, `test_sandbox_dispatch.py` (+3 more per the blast radius).
- Repository-layer tests: `tests/codemie/repository/test_azure_file_repository.py` (fixture `setup_repository`, constants `FILE_NAME_NEW`, `FILE_CONTENT`, `MIME_TEXT_PLAIN`, `BUCKET_NAME_NEW`; cases `test_write_file`, `test_write_csv_file`, `test_write_pdf_file`, `test_bucket_creation`), `tests/codemie/repository/test_repository_factory.py`, `tests/codemie/service/test_skill_file_storage.py` (`test_delegates_to_repo_with_correct_owner`, `test_owner_prefix_is_skill_id`, `test_mime_type_optional`).
- Service-layer: `tests/codemie/service/tools/test_toolkit_settings_service.py`, `tests/codemie/service/tools/test_image_generator_token_tracking.py` (both exercise `get_file_system_toolkit`).

#### Testing Framework and Patterns

- pytest, with a mix of plain functions + `@pytest.mark.parametrize`, pytest classes with `@pytest.fixture`, and `unittest.TestCase` classes (`TestFileSystemTools`). `monkeypatch.setenv` is the standard way to flip `FILE_SYSTEM_TOOLS_ENABLED` / `CODE_EXECUTOR_ENABLED`. `unittest.mock.patch` is used for `subprocess.run` and loggers.
- No shared `file_repository` fixture surfaced anywhere. The existing precedents build ad-hoc mocks per test (`test_skill_file_storage.py` asserts on the `owner` kwarg passed to a mock repo; `test_toolkit_post_processing.py` patches `FileRepositoryFactory`). A new test would follow one of those two shapes.
- Test placement convention (matching the guide): tool-layer tests under `tests/codemie_tools/data_management/file_system/`, service/MCP tests under `tests/codemie/service/...`.

#### Coverage Gaps

- No test constructs `FileSystemToolkit` **with** a `file_repository` and asserts what the admin tools receive — the toolkit fixture always passes `configs={}` with no repository.
- No test covers `FileExportService.store_exported_bytes` directly (only `_validate_export_paths` and the higher-level executor flows surfaced); no `tests/.../test_file_export_service.py` appeared in the index.
- No test covers binary content through `WriteFileTool` (it only writes text) or through `CommandLineTool` (which never touches storage).
- No test asserts owner/user isolation for artifacts produced by the FileSystem toolkit.
- No repository-layer test for a size cap — because no size cap exists below the REST upload endpoint.

---

### 5. Configuration and Environment

#### Environment Variables

- `FILE_SYSTEM_TOOLS_ENABLED` — gates the six admin tools (toolkit.py:82). Default `"false"`. Must be `"true"` for the repro's `WriteFileTool` / `CommandLineTool` to exist at all.
- `CODE_EXECUTOR_ENABLED` — gates `CodeExecutorTool` (toolkit.py:94). Default `"false"`.
- `FILES_STORAGE_TYPE` — selects the backend; defaults to `filesystem` (repository_factory.py:69).
- `FILES_STORAGE_DIR` — root for `FileSystemRepository` (file_system_repository.py:46).
- `FILES_STORAGE_MAX_UPLOAD_SIZE` — enforced only in the REST upload endpoint (files.py:253).
- `FILES_STORAGE_GCP_REGION` (gcp_file_repository.py:35), `AZURE_STORAGE_CONNECTION_STRING` / `AZURE_STORAGE_ACCOUNT_NAME` (repository_factory.py:54-55), `AWS_S3_REGION` / `AWS_S3_BUCKET_NAME` (repository_factory.py:60).
- Related but not in the FileSystem path: `MCP_CONNECT_ENABLED`, `MCP_SERVER_INIT_TIMEOUT`, `TOOL_TOKENS_SIZE_LIMIT` (`CodeMieTool.tokens_size_limit`, codemie_tool.py:34), `CODE_EXECUTOR_EXECUTION_MODE`, `IMAGE_GENERATION_MODEL`, `LLM_PROXY_ENABLED` / `LITE_LLM_URL`.

#### Configuration Files

- `codemie.configs.config` is the central settings object consumed by the repository factory, the files router, and `CodeMieTool`.
- Per-user/project `FILE_SYSTEM` credentials (`CredentialTypes.FILE_SYSTEM` → `FileSystemConfig`, settings.py:1668-1683) supply `root_directory` and `activate_command` for the toolkit; absent settings leave `root_directory = "."`.
- `codemie/core/constants` holds `MCP_ARTIFACTS_BASE` / `MCP_IMAGES_BASE`, normalized into subdir names by `FileRepositoryFactory.build_subdir`.

#### Feature Flags and Deployment Concerns

- The two `*_ENABLED` env flags are the deployment-level switches for this whole tool family; `FILE_SYSTEM_TOOLS_ENABLED` also interacts with the `is_admin` check in `get_tools_ui_info` (admin + flag to see the tools in UI, but `get_tools()` gates on the flag alone).
- Storage backend varies per environment (local filesystem vs S3 vs Azure Blob vs GCS); Azure forbids `_` in container names and both Azure and GCP **auto-create** a container/bucket per distinct `owner` value (azure_file_repository.py:44-49, gcp_file_repository.py:32-39) — so the choice of `owner` string has a direct infrastructure cost.
- Retention is explicitly out of scope per the ticket (cloud lifecycle policies).

---

### 6. Risk Indicators

- **Verification result — all research-doc claims hold against current source.** `file_repository` declared at toolkit.py:68 and populated by `get_toolkit` (L148-169); forwarded only to `GenerateImageTool` (L118-122) and `CodeExecutorTool` (L126-132); the six admin tools (L136-143) get neither `file_repository` nor `user_id`, and none of `ReadFileTool`/`ListDirectoryTool`/`WriteFileTool`/`CommandLineTool`/`DiffUpdateFileTool`/`ReplaceStringTool` declare such a field. `WriteFileTool.execute` returns the bare string at tools.py:133; `CommandLineTool.execute` returns raw subprocess output at tools.py:200. The 3-step primitive is confirmed at file_export_service.py:103-125 and generate_image_tool.py:55-65. `_validate_export_paths` is confirmed security-only at code_executor_tool.py:625-637.
- **RQ8 answered — `file_repository` IS non-None at runtime.** `toolkit_settings_service.py:227` passes `FileRepositoryFactory.get_current_repository()` unconditionally and `configs["user_id"] = user.id` at L203. So the wiring point exists; nothing needs a new plumbing route. Caveat: the one existing toolkit test fixture builds the toolkit with `configs={}` and no repository, so tool-layer code must tolerate `file_repository=None` or those tests break.
- **No generic agent-level post-processing hook exists (RQ6).** The only hook is per-tool `CodeMieTool._post_process_output_content` (codemie_tool.py:204-218), overridden per subclass — the MCP path uses exactly this (mcp/toolkit.py:301-305). `ToolsPreprocessor` / `ToolsPreprocessorFactory` (tools_preprocessing.py) operate on tool *descriptions before* invocation, not on results. So a single cross-toolkit URL-minting step has no existing seam; it would be a new one.
- **`CommandLineTool` has no structural signal for "which file is the deliverable".** `CommandLineInput` (tools.py:138-147) carries only `command` and `timeout`. The binary xlsx in the ticket's repro is produced by a shell command, so nothing in the current input schema names it.
- **Binary vs text asymmetry.** `WriteFileTool` opens in `"w"` with utf-8 (tools.py:129-131) — text only. `FileSystemRepository.write_file` branches on `isinstance(content, bytes)` (file_system_repository.py:50); AWS/Azure/GCP pass content straight through to SDKs that expect bytes or str. Any byte-path handling must decide the type explicitly.
- **No size cap on the artifact primitive.** `store_exported_bytes` (file_export_service.py:103-125) and `_save_artifact_to_storage` (mcp/toolkit.py:382-406) have none; `FILES_STORAGE_MAX_UPLOAD_SIZE` is checked only in the REST upload endpoint (files.py:253). A large file reaching storage through a tool path is unbounded.
- **Token-limit interaction.** `CodeMieTool._limit_output_content` (codemie_tool.py:163-202) truncates tool output above `tokens_size_limit` *before* `_post_process_output_content` runs. A URL appended to a large `CommandLineTool` stdout could be truncated away.
- **Owner-scoping inconsistency between the two halves (AC #6).** The MCP half writes with `owner=MCP_ARTIFACTS_SUBDIR` — a single shared subdir, not the user id (mcp/toolkit.py:405) — while `GenerateImageTool` and `FileExportService` use `owner=self.user_id`. Since `owner` is embedded in the encoded URL and *is* the bucket/container/prefix, the MCP artifacts currently share one namespace across users. Worth confirming against AC #6 before treating the MCP half as a template.
- **Path traversal surfaces.** `get_validated_relative_path` (utils.py:32-41) explicitly permits symlinks from outside pointing back inside root (documented at L34-35). `ReplaceStringTool.execute` builds `Path(self.root_dir) / path` (tools.py:334) **without** calling `get_relative_path` at all. `_resolve_safe_path` (file_system_repository.py:31-37) is the repository-side guard.
- **Command-injection denylist is best-effort.** `sanitize_command` (tools.py:202-240) is a regex denylist, with the commands running as `/bin/bash -c` on the host container — `root_dir` defaults to `"."` (the process CWD) when no FILE_SYSTEM setting exists, so an unconfigured assistant runs shell commands in the backend's working directory.
- **Brittle test counts.** `test_file_system_toolkit.py` asserts exact tool counts (1 and 7 at L40/L47/L65/L82/L93) and `test_get_tools_with_root_directory` (L103-111) asserts `tool.root_dir == root_dir` for every tool exposing that attribute.
- **Missing coverage listed in §4** — no direct `store_exported_bytes` test, no toolkit test with a live `file_repository`, no binary-content test, no artifact isolation test.
- **UI side is in another repository** (`codemie-ui/src/components/markdown/Markdown.utils.ts`), not indexed here — the `sandbox:/v1/files/` contract cannot be verified from this codebase, only inferred from the three producers that emit it.
- **Speculative:** the research doc at `/home/aliaksei_chumakou/Downloads/RealFileProblem_EPMCDME-14735.md` proposes two fix shapes (add `file_repository`/`user_id` fields to `WriteFileTool`; add an explicit attach-file tool mirroring `export_files`). These are that document's proposals, not constraints discovered in source. Neither the fields nor the tool exist today; whether either is the right shape is a spec/plan decision.
- **Speculative:** the doc notes an advertised code-executor "Approach 2 (automatic capture)" for plots whose implementation was not found in this repository (likely inside the sandbox docker image). If artifact auto-capture is considered, that unverified out-of-repo behavior is a relevant unknown.

---

### 7. Summary for Complexity Assessment

The change sits across three layers that are all already in place and connected. The tool layer (`src/codemie_tools/data_management/file_system/toolkit.py` and `tools.py`) is where the gap is: `FileSystemToolkit.get_tools()` forwards `self.file_repository` and `self.user_id` to `GenerateImageTool` and `CodeExecutorTool` but not to the six env-gated admin tools, and those tool classes declare no such fields, so `WriteFileTool` returns a bare local-path string and `CommandLineTool` returns raw subprocess output. The service layer above it needs nothing new: `ToolkitSettingService.get_file_system_toolkit` (toolkit_settings_service.py:190-231) already calls `FileRepositoryFactory.get_current_repository()` unconditionally and sets `configs["user_id"] = user.id`, so the repository handle is genuinely live at runtime for a FILE_SYSTEM-only assistant — the critical open question going in is now closed, and no new plumbing route is required. The repository layer (`FileRepository.write_file(name, mime_type, owner, content) -> FileObject`, four backends, `FileObject.to_encoded_url()` serializing `[mime_type, owner, name]`) and the `sandbox:/v1/files/` URL contract are established and used by three independent producers. Expected file-change surface is therefore concentrated in two or three tool-layer files plus their tests; the UI needs nothing, since its rewrite already matches any occurrence of the prefix.

Technical novelty is low: the required mechanism already exists three times over as an identical 3-step primitive (`FileExportService.store_exported_bytes`, `GenerateImageTool._store_b64_image`, `_save_artifact_to_storage`), and `store_exported_bytes` is explicitly documented in-source as a reusable "shared kernel". What is genuinely absent is any single shared helper the three call — they differ in filename generation, owner value, and return formatting — and any generic agent-level post-processing seam: the only hook is per-tool `CodeMieTool._post_process_output_content`, so a cross-toolkit solution would introduce a new seam rather than extend one. A second real unknown is `CommandLineTool`: its input schema carries only `command` and `timeout`, giving the backend no structural signal about which produced file matters, which is precisely the ticket's own AC #8 territory.

Test coverage posture is uneven. The FileSystem toolkit and tools have real suites (`test_file_system_toolkit.py`, `test_file_system_tools.py`), but they assert tool counts and types with hard-coded numbers (1 and 7) and build the toolkit with `configs={}` and no repository — so the wired-repository case is untested and count assertions are brittle against any tool-list change. `store_exported_bytes` has no direct test; `_validate_export_paths` and the MCP artifact path do (`test_export_path_validation.py`, `test_toolkit_post_processing.py`). There is no shared `file_repository` fixture — the two precedents are ad-hoc mocks (`test_skill_file_storage.py` asserting on the `owner` kwarg) and patching `FileRepositoryFactory`. Key risk factors for scoring: no size cap on any tool-path write (the cap lives only in the REST upload endpoint), a text-only write path versus binary xlsx artifacts, `CodeMieTool._limit_output_content` truncating output before post-processing, an owner-scoping inconsistency where the already-merged MCP half writes to a shared `MCP_ARTIFACTS_SUBDIR` rather than the user id (directly relevant to AC #6), and a shell tool running `/bin/bash -c` with a regex denylist and a `"."` default root directory.

---

### 8. External References

**`/home/aliaksei_chumakou/Downloads/RealFileProblem_EPMCDME-14735.md`** — resolved and read (205 lines). Facts a spec needs from it:

- **Its central claim is correct.** Every code assertion in the document was re-verified against current source and none had drifted: `file_repository` at toolkit.py:68 populated by `get_toolkit` (L148-169); forwarded only to `GenerateImageTool` (L118-122) and `CodeExecutorTool` (L126-132); six admin tools built with `root_dir`-only at L136-143; `WriteFileTool.execute` returning `f"File written successfully to {file_path}"` at tools.py:133; `CommandLineTool.execute` returning raw `subprocess.run` output at tools.py:200; `FileExportService.store_exported_bytes` at file_export_service.py:103-125; `_validate_export_paths` at code_executor_tool.py:625-637; MCP functions at mcp/toolkit.py:352-488.
- **The UI contract it documents** (not verifiable from this repo): `codemie-ui/src/components/markdown/Markdown.utils.ts:114` does `sanitizeMessage(message).replaceAll('sandbox:/v1/files/', \`${api.BASE_URL}/v1/files/\`)` inside `getMarkdownTokens`; `renderer.link` at Markdown.utils.ts:131-134 then renders a plain `<a href>`. There is no dedicated React download-chip component — this single string replace is the entire mechanism, and it is generic over any producer of the prefix, so a backend-only fix needs no UI change.
- **Its "Bottom line"**: MCP `resource` items are fixed; `FileSystemToolkit`'s `WriteFileTool` / `CommandLineTool` are not.
- **Its proposed fix shapes (proposals, not findings)**: Fix 1 — add `file_repository`/`user_id` fields to `WriteFileTool` and store bytes after the existing write, returning the existing message plus a `[Download](sandbox:/v1/files/…)` link; Fix 2 for `CommandLineTool` — Option A auto-diff the working directory (the doc argues against: sweeps temp/cache files, needs size caps and allow/blocklists), Option B an explicit attach tool/parameter mirroring `CodeExecutorTool.export_files`, which the doc notes matches the ticket's own AC #8.
- **Reliability caveat it raises**: `export_files` is purely an LLM-supplied argument with no server-side detection or correction layer; the only steering is prompt text embedded in the schema description (`code_description`, code_executor_tool.py:106-145). An attach-tool for `CommandLineTool` inherits that dependency and arguably worsens it, since the model must parse a filename out of subprocess stdout and echo it back on a follow-up call.
- **Unverified item it flags**: the code executor's advertised "Approach 2 (automatic capture)" for plots has no implementation in this repository; it presumably lives in the sandbox docker image (`self.config.docker_image`), outside this repo.
- **Related tickets it names**: EPMCDME-8709 (download files generated during agent/workflow work), EPMCDME-10708 (workflow output downloadable links), EPMCDME-10854 (code_executor local mode file URL — closed by removing local execution mode entirely; `ExecutionMode` now supports only `SANDBOX`).
- **Status note**: the document states it is findings-only; no code changes or Jira updates were made from it.

**Chat log URL** `http://localhost:5173/share/conversations/jtoFunayxT65` — a local dev server address, not fetchable from this environment; not resolved.

---

# Run 3 — `2026-10-02-epmcdme-14735-artifact-auth-gaps`

## Technical Research

**Task**: mcp artifacts file-authorization share-conversation
**Generated**: 2026-10-02
**Research path**: codegraph

---

### 1. Original Context

EPMCDME-14735 follow-up run on the current branch (EPMCDME-14735_downloadable-mcp-artifacts). Do not create a new branch. This is a new, separate run; earlier runs are context only and must not be resumed.

### Background
EPMCDME-14735 adds downloadable non-image MCP/filesystem artifacts. EPMCDME-12708 (already merged to main and present in this branch) added authorization to GET /v1/files/{file_name} in src/codemie/rest_api/routers/files.py (_authorize_file_access, Rules A–E). A blob is served only to its owner, to a workspace owner, or to a share recipient holding a share grant. Commit 6da1b5556 already fixed the main conflict: MCP artifacts are now stored under the invoking user's id (taken from user_context) and not under the shared MCP_ARTIFACTS_SUBDIR owner. Treat that commit as the baseline.

### Scope: three gaps between 14735 and 12708

1. Missing-user fallback violates 14735 AC4.
   In src/codemie/service/mcp/toolkit.py, _save_artifact_to_storage falls back to owner=MCP_ARTIFACTS_SUBDIR when no invoking user is known. No 12708 rule serves that owner, so the chat shows a Download link that always 404s. AC4 forbids that ("must not show a fake or non-working download link").
   Expected: with no invoking user, do not store the artifact. Render the existing "[File could not be saved]" outcome in _convert_resource_to_url, log a warning, and stop using MCP_ARTIFACTS_SUBDIR as a write owner. Do not change the MCP screenshot fallback to MCP_IMAGES_SUBDIR: 12708 deliberately keeps it for legacy blobs.

2. Empty user_id produces unreadable artifacts.
   AttachFileTool, WriteFileTool, GenerateImageTool (src/codemie_tools/data_management/file_system/) and FileExportService default user_id to "". store_artifact (src/codemie_tools/base/artifact_storage.py) accepts an empty owner, and 12708's _points_outside_owner then rejects every download of that blob.
   Expected: store_artifact fails closed on an empty owner, and callers surface "could not be saved" instead of a dead link. Production toolkits are built with user.id (toolkit_settings_service.py), so this hardens new callers and tests. It must not change behavior for a real user.

3. Shared-conversation access to artifacts (agreed with the ticket owner).
   12708's share grant (Rule E, _has_share_grant + collect_message_file_tokens in src/codemie/service/share_conversation_service.py) covers tokens in a message's file_names or inline sandbox:/v1/files/ links in the message text, plus blobs registered in the sharer's workspace. This branch emits the artifact link `[Download](sandbox:/v1/files/<token>)` inside the MCP tool output. If the assistant's final answer doesn't repeat it, a shared-chat recipient gets 404.
   Expected: a share recipient can download MCP and filesystem-toolkit artifacts produced in the shared conversation, and only those. Solve it the same way 12708 solves it for MCP screenshot images (image_url items with sandbox:/v1/files/ URLs produced by _convert_image_to_url). First research how 12708 makes those screenshots render for share recipients: which carrier puts the token into the share grant, whether tool output or thoughts are persisted and scanned. Then reuse that exact mechanism for artifacts. Don't invent a parallel one. If screenshots turn out not to be covered either, stop and report it as a finding at the spec gate before designing anything.

### Out of scope
- The plugin-engine MCP path (src/codemie/enterprise/plugin/dependencies.py, call to _convert_mcp_response_to_tool_message without an owner). Leave it untouched.
- Editing the previous run's plan/spec docs.
- Byte size in the download chip (already dropped from AC2 by ticket comment).
- Signed or unforgeable file URLs (open 12708 item; not this ticket).
- Any UI-repo change.

### Constraints
- 14735 AC7: existing image artifact behavior stays unchanged.
- No new authorization rule in files.py that grants a shared namespace (that would reopen 12708's IDOR). Any widening of access must be a scoped grant like Rule E.

Research emphasis (from the orchestrator): for gap 3, trace end to end how an MCP screenshot's sandbox:/v1/files/<token> URL reaches a share recipient's grant — is the tool output / thoughts persisted on the conversation message (which field), does collect_message_file_tokens (or anything feeding _has_share_grant) scan that field, and is the image blob owner served via Rule E or via the legacy MCP_IMAGES_SUBDIR rule. State plainly whether screenshots under a real user owner are covered for share recipients. Also enumerate every store_artifact caller and how each handles a None/failed return today.

---

### 2. Codebase Findings

#### Existing Implementations

**MCP post-processing** — `src/codemie/service/mcp/toolkit.py`
- `MCPTool._post_process_output_content` (L303-315): owner = `_execution_context.user_context.id`; plain `MCPTool` has no context → owner `None`.
- `_save_screenshot_to_storage` (L362-399): `if not owner: owner = MCP_IMAGES_SUBDIR` + warning; writes via `file_repo.write_file` directly (does NOT use `store_artifact`).
- `_save_artifact_to_storage` (L402-432): `base64.b64decode(validate=True)`; `if not owner: owner = MCP_ARTIFACTS_SUBDIR` + warning; `url = store_artifact(...)`; `return url.removeprefix("sandbox:/v1/files/")`.
- `_convert_image_to_url` (L435-461): sets `item.image_url = {"url": "sandbox:/v1/files/<t>"}`; on `(IOError, OSError, ValueError, binascii.Error)` falls back to a `data:` URL.
- `_convert_resource_to_url` (L464-496): image MIME rerouted to image path; on success `item.text = "[Download](sandbox:/v1/files/<t>)"`; on the same 4 exception types `item.text = "[File could not be saved]"`.
- `_format_content_item` (L519-546): image_url → `"Screenshot captured. URL: <url>\n\n![Screenshot](<url>)"`; resource_url → `item.text`. Output is a single tool-output string (`_convert_mcp_response_to_tool_message`, L549).
- `MCP_IMAGES_SUBDIR`, `MCP_ARTIFACTS_SUBDIR` — `src/codemie/repository/repository_factory.py:83-84`. `MCP_ARTIFACTS_SUBDIR` is used only in toolkit.py (2 refs).

**Authorization** — `src/codemie/rest_api/routers/files.py`
- `_points_outside_owner` (L245-257): denies when owner or name is empty, absolute, or contains `..`.
- `_authorize_file_access` (L326-368): Rule A `owner == user.id`; B workflow schema; **C `owner == MCP_IMAGES_SUBDIR` → served to any authenticated user**; D workspace owner; E `_has_share_grant`.
- `_has_share_grant` (L289-323): carrier 1 `_conversation_refers_to_file` → `collect_message_file_tokens` over `conversation.history`; carrier 2 sharer's workspace `find_by_blob(workspace.id, owner, name, mime)`.

**Share service** — `src/codemie/service/share_conversation_service.py`
- `collect_message_file_tokens` (L37-52): scans `message.file_names` and `message.message` (regex `sandbox:/v1/files/[^\s)\]>"']+`). **Does not read `thoughts`.**
- `_build_shared_file_urls` (L173-189): the share_token URL map handed to recipients is built from the same collector.

**Where tool output is persisted**
- `AgentStreamingCallback.on_tool_end` (`src/codemie/agents/callbacks/agent_streaming_callback.py:348-384`): tool output → `Thought.message`.
- `AssistantRequestHandler._filter_thoughts` (`src/codemie/rest_api/handlers/assistant_handlers.py:478-524`) → `UpsertChatHistoryParams.thoughts` → `ChatTurnData.thoughts` → `GeneratedMessage.thoughts` (`conversation.py:135`). `file_names` is filled from `request.file_names` (user uploads) only (`conversation_service.py:448`).

**Workspace carrier (`register_generated_files`)**
- `WorkspaceAwareAgent._persist_generated_workspace_files` (`src/codemie/agents/tools/agent.py:118-140`) scans with `SANDBOX_FILE_RE` and registers blobs in the conversation workspace.
- Called only from: `AIToolsAgent.generate` (final `output` only, `assistant_agent.py:553`), `AIToolsAgent.invoke` (whole response dict, L430), `LangGraphAgent.generate` (final `output` only, `langgraph_agent.py:708`).
- **Not called from `AIToolsAgent.stream` (L588-630) or `LangGraphAgent.stream` (L743-813)**, the streaming chat path.

**store_artifact** — `src/codemie_tools/base/artifact_storage.py:32-67`: returns `None` when the repo is falsy; raises `ValueError` over the size cap; passes `owner` through unchecked (empty string accepted).

#### store_artifact callers and current failure handling

| Caller | None return | Exception |
|---|---|---|
| `toolkit._save_artifact_to_storage` | `None.removeprefix` → `AttributeError`, not in the caught tuple of `_convert_resource_to_url`, so it propagates | ValueError/IOError/OSError → `"[File could not be saved]"` |
| `AttachFileTool.execute` (attach_file_tool.py:56) | returns "could not be stored for download: no file storage is configured" | propagates (tool error) |
| `WriteFileTool._store_artifact` (tools.py:281-287) | `last_download_url=None`, so no link and no warning | any Exception → `last_storage_error`, then "Warning: file was written but not stored for download" (L293) |
| `GenerateImageTool._store_b64_image` (generate_image_tool.py:56-60) | unreachable (repo checked first; raises ValueError) | propagates |
| `FileExportService.store_exported_bytes` (file_export_service.py:104-123), used by `_export_single_file` and `CodeExecutorTool._export_files_from_execution` / `_store_exported_bytes` | unreachable (repo guard returns None first) | ValueError → "' was not stored: {e}"; other → None (dropped silently) |

Also: `FileExportService._collect_single_file` (L177-183) builds `FileObject(owner=self.user_id)` without `store_artifact` (used by `execute_workspace_script_tool.py:154`).

#### Architecture and Layers Affected
- Tools layer: `codemie_tools/base/artifact_storage.py`, `codemie_tools/data_management/file_system/*`, `code_executor/file_export_service.py`.
- Service layer: `service/mcp/toolkit.py`, `service/share_conversation_service.py`.
- API layer: `rest_api/routers/files.py` (Rule E).
- Agent runtime (read-only context): `agents/tools/agent.py`, `agents/assistant_agent.py`, `agents/langgraph_agent.py`, `agents/callbacks/agent_streaming_callback.py`.

#### Integration Points
- `files.py` ↔ `share_conversation_service.collect_message_file_tokens` (one shared collector for both the grant and the URL map).
- `files.py` ↔ `AgentWorkspaceRepository.find_by_blob` / `get_by_conversation_for_user`.
- Plugin path `enterprise/plugin/dependencies.py` calls `_convert_mcp_response_to_tool_message` without an owner (out of scope).

#### Patterns and Conventions
- One collector is shared by both sides of a grant (the docstring requires that "both sides must derive it from this one function").
- Uniform 404 via `_raise_file_not_found` for every denial.
- Artifact link format: `[Download](sandbox:/v1/files/<encoded>)`.

---

### 3. Documentation Findings

#### Guides and Architecture Docs
`.ai-run/guides/` exists (security-patterns, testing-patterns, error-handling, mcp-integration). None are specific to file authorization or share grants.

#### Architectural Decisions
- Inline comments on Rule C (files.py:349-357): legacy and no-user screenshots stay world-readable on purpose. The rule can be tightened once the unattributed-write log stays silent.
- `find_by_blob` deliberately skips the `deleted_at` filter ("see D-4").

#### Derived Conventions
Owner = authenticated initiator (`user_context.id`), never `execution_context.user_id` (toolkit.py:305-310 comment).

---

### 4. Testing Landscape

#### Existing Coverage
- `tests/codemie/service/mcp/test_toolkit_post_processing.py`: `_post_process_tool_result`, `_save_artifact_to_storage`, `MCP_ARTIFACTS_SUBDIR` fallback.
- `tests/codemie_tools/base/test_artifact_storage.py`: `store_artifact`.
- `tests/codemie/rest_api/routers/test_files.py`: Rules incl. share grant (`_shared_conversation_with_history`, `find_by_blob`, `get_shared_conversation`).
- `tests/codemie/rest_api/routers/test_share.py`: share router.
- `tests/codemie_tools/data_management/file_system/test_attach_file_tool.py`, `test_file_system_tools.py`, `test_generate_image_tool.py`; `tests/codemie_tools/data_management/test_file_system_toolkit.py`.
- `tests/codemie/service/mcp/test_execution_context_integration.py`: owner via context.

#### Testing Framework and Patterns
pytest + anyio, `AsyncClient(ASGITransport)` for routers, `app.dependency_overrides[authenticate]`, `mocker.patch` on `FileRepositoryFactory.get_current_repository`.

#### Coverage Gaps
- No test found that asserts that thoughts-borne screenshot/artifact URLs are (or are not) granted to share recipients.
- `_convert_mcp_response_to_tool_message` has no tests within 3 caller hops (codegraph).
- No dedicated FileExportService test file located by codegraph.

---

### 5. Configuration and Environment

#### Environment Variables
- `FILES_STORAGE_MAX_UPLOAD_SIZE`: size cap in `store_artifact`.
- `CODEMIE_STORAGE_BUCKET_NAME`: Rule B owner.
- `MCP_TOOL_TOKENS_SIZE_LIMIT`, `MCP_CLIENT_TIMEOUT`: MCP tool.

#### Configuration Files
- `MCP_IMAGES_BASE` / `MCP_ARTIFACTS_BASE` → `FileRepositoryFactory.build_subdir` (repository_factory.py).

#### Feature Flags and Deployment Concerns
- Conversation replay v2 (`_is_conversation_replay_v2_enabled`) changes which thought fields `_filter_thoughts` keeps. `message` is kept either way.

---

### 6. Risk Indicators

- **STOP-GATE FINDING: MCP screenshots under a real user owner are NOT covered for share recipients on the streaming chat path.** The URL lives only in `Thought.message` (`GeneratedMessage.thoughts`). `collect_message_file_tokens` scans `file_names` and `message`, not `thoughts`. The workspace carrier is populated only by `generate()`/`invoke()`, never by `stream()`, and `generate()` scans only the final answer. A recipient therefore gets the screenshot only if the final answer repeats the URL. Legacy and no-user screenshots (owner `MCP_IMAGES_SUBDIR`) render through Rule C (any authenticated user), not through Rule E. So 12708 has no screenshot mechanism that artifacts could reuse.
- The recipient's `shared_file_urls` map is built from the same collector, so thought-borne tokens get no `?share_token=` URL either.
- Speculative: making thoughts a carrier would widen the grant set to every URL in tool output, including inputs echoed back by tools. The spec must decide scope.
- `_save_artifact_to_storage` will raise `AttributeError` on a `None` return from `store_artifact`, and the `_convert_resource_to_url` except tuple does not catch it.
- `WriteFileTool` silently shows no link when `store_artifact` returns None (no repo). The behaviour differs from AttachFileTool.
- Caller exception handling differs: AttachFileTool and GenerateImageTool let `ValueError` propagate; WriteFileTool, FileExportService and the MCP path convert it to text.
- `FileExportService._collect_single_file` also uses `owner=self.user_id` (possibly "") outside `store_artifact`.
- Speculative: removing `MCP_ARTIFACTS_SUBDIR` as a write owner leaves the constant with no users; tests in `test_toolkit_post_processing.py` assert the current fallback.

---

### 7. Summary for Complexity Assessment

Gaps 1 and 2 are small and local. Gap 1 touches `_save_artifact_to_storage`/`_convert_resource_to_url` in `service/mcp/toolkit.py`. Gap 2 centres on one function, `store_artifact` in `codemie_tools/base/artifact_storage.py`, which five production callers depend on (MCP toolkit, AttachFileTool, WriteFileTool, GenerateImageTool, FileExportService). Each caller already handles failure differently: some convert ValueError to text, others propagate it. Current tests already exist for each caller (test_artifact_storage, test_toolkit_post_processing, file_system tool tests).

Gap 3 hits the stop condition the task defines. The task assumes 12708 has a working mechanism for screenshots. It has none for real-user-owned screenshots on the streaming chat path. The screenshot URL is persisted only in `GeneratedMessage.thoughts[].message`. Neither Rule E carrier reads that: `collect_message_file_tokens` scans `file_names` and `message`, and workspace registration runs only in non-streaming `generate()`/`invoke()`, scanning the final answer. Screenshots under `MCP_IMAGES_SUBDIR` are reachable only through the world-readable Rule C. That is the namespace-grant pattern the constraints forbid for artifacts.

The orchestrator needs a decision at the spec gate before gap 3 is designed. Any fix would touch the authorization layer (`files.py` / `share_conversation_service.py`), the agent runtime, or both. That is a security-sensitive change with no existing test for thought-borne grants. Gaps 1 and 2 can proceed independently.

---

### 8. External References

None named by the task.

---

# Run 4 — `2026-10-22-backend-artifact-publisher`

## Technical Research

**Task**: mcp artifact publisher download
**Generated**: 2026-10-22 (second pass: external references opened and verified)
**Research path**: codegraph

---

### 1. Original Context

/home/aliaksei_chumakou/codemie-dev/docs/stories/2026-10-22-mcp-artifact-publishing/children/backend-artifact-publisher/story.md
(Read this story file for the requirements. It is the back-end child of the root story /home/aliaksei_chumakou/codemie-dev/docs/stories/2026-10-22-mcp-artifact-publishing/story.md, which covers back-end and front-end review comments for ticket EPMCDME-14735; this run is the back-end part only. The branch EPMCDME-14735_downloadable-mcp-artifacts already carries earlier implementation commits for this ticket. Prior, non-conforming analysis you may use as a starting point, but verify against current code: /home/aliaksei_chumakou/codemie-dev/docs/stories/2026-10-22-mcp-artifact-publishing/children/backend-artifact-publisher/technical-analysis.md)

feature_area: mcp artifact publisher download
run_dir: /home/aliaksei_chumakou/codemie-dev/codemie/docs/superpowers/tasks/2026-10-22-backend-artifact-publisher

---

### 2. Codebase Findings

Line numbers verified against current source on branch EPMCDME-14735_downloadable-mcp-artifacts (HEAD ae35b19c3). No "publisher" module exists yet (the term only matches unrelated marketplace/kata publish code). Paths are under `/home/aliaksei_chumakou/codemie-dev/codemie/` unless stated.

#### Existing Implementations
- `src/codemie/service/mcp/models.py:392-468` `MCPToolContentItem`: fields `type,text,data,image_url,mimeType` only; no `resource`, `uri`, `name`. `is_resource()` (:447) is called only from `tests/codemie/service/mcp/test_toolkit_post_processing.py`. `MCPToolInvocationResponse` :471.
- `src/codemie/service/mcp/client.py:268-275`: `MCPToolInvocationResponse.model_validate(response.json())`; a `ValidationError` becomes `ValueError("Invalid response from MCP-Connect")`. Unknown keys of a content item (`resource`, `uri`) are dropped silently by pydantic (no `extra` config on the model).
- `src/codemie/service/mcp/toolkit.py`:
  - `_post_process_output_content` :305-317: owner = `execution_context.user_context.id`; plain `MCPTool` has no context, owner None.
  - `_save_screenshot_to_storage` :364-401: with no owner uses `MCP_IMAGES_SUBDIR` (warning), name `<uuid4.hex><ext>`, `b64decode` without `validate`, calls `file_repo.write_file` directly.
  - `_save_artifact_to_storage` :404-435: `b64decode(validate=True)`, name `f"{uuid.uuid4()}.{ext}"` (:427), owner required via `store_artifact`.
  - `_convert_image_to_url` :438-464 (falls back to inline `data:` URL on error); `_convert_resource_to_url` :467-499 (only `type=="resource"` with flat `item.data`; no data -> warning, item untouched; failure -> `[File could not be saved]`); `_post_process_tool_result` :502; `_format_content_item` :522; `_convert_mcp_response_to_tool_message(tool_result, owner=None)` :552.
- `src/codemie/enterprise/plugin/dependencies.py:368-389` `mcp_converter_adapter`: `enterprise_mcp_response.model_dump()` -> `CoreMCPToolInvocationResponse(**data)` -> `_convert_mcp_response_to_tool_message(core)` with no owner. The enclosing function receives `user_id` (:348-356) and the toolkit is built from `assistant, user, request` (`ToolkitService._get_plugin_tools_delegate` -> `PluginToolsDelegate.get_plugin_tools(assistant,user,request)`).
- `src/codemie_tools/base/artifact_storage.py:32-71` `store_artifact(repo,name,content,owner)`: empty owner -> ValueError, no repo -> None, size check on `len(content)`, MIME from extension. Five modules use it.
- Producers (each names its own file): `file_system/tools.py` `WriteFileTool` :222-304 (`_store_artifact` :293 stores `<uuid>_<basename>`, notice in `_write_notice` ContextVar :219; `ZIP_CONTAINER_EXTENSIONS` :104; `_zip_payload_error` :107 = `zipfile.is_zipfile` only); `file_system/attach_file_tool.py:44-60` (private `CodeExecutorTool._validate_export_paths`, `open().read()` before any size check, `<uuid4>_<basename>`); `code_executor/file_export_service.py:104-123` (`<uuid>_<basename>`; ValueError reason returned as text, other exceptions swallowed to None); `file_system/generate_image_tool.py` (uses `store_artifact`; not read in detail).
- Workspace: `src/codemie/service/agent_workspace_service.py` `upsert_binary_file` :113, `get_file_sandbox_url` :127 (token name = `blob_name`, owner = `blob_owner`, path = `path`), `register_generated_files` :279-322 (dedupe on `(owner,name,mime)`), `_get_or_create_workspace` :840, `download_file` :449, `edit_file` :458, `upsert_text_file` :365, `_skip_reserved` :909 / `is_reserved_workspace_path`. Precedent for a producer publishing into the workspace: `codemie_tools/data_management/workspace/generate_image_tool_v2.py:134-147`.
- Post-turn registration: `src/codemie/agents/tools/agent.py:36` `SANDBOX_FILE_RE`, `_persist_generated_workspace_files` :118-140 (errors only logged).
- Authorization: `src/codemie/service/file_service/download_rules.py` `can_download` :138-181 (pre-check `is_safe_blob_ref`; A owner==user; B workflow schema; **C** `MCP_IMAGES_SUBDIR` -> True for any authenticated user; D workspace owned by requester; D2; E share grant). `_conversation_refers_to_file` :91-98 (exact token in any history message via `collect_message_file_tokens`, no ownership check); `_has_share_grant` :101-135 (history match first, then sharer's workspace `find_by_blob`).
- Share collector `src/codemie/service/share_conversation_service.py:30-69`: own `_SANDBOX_FILE_RE` :34 (agent's pattern plus a backtick exclusion), `_thought_tokens` :43-51 (recurses `thoughts[].message` and `children`), `collect_message_file_tokens` :54-69.
- `/v1/files/{file_name}` route `src/codemie/rest_api/routers/files.py:222-287` (verified): decodes the token, authorizes, then `display_name = _strip_uuid_prefix(file_object.name) or file_object.name` (:261) and passes it to `_safe_disposition` (:52-85), which deletes `/` and `\` from the name (:68). A name `artifacts/<turn>/report.xlsx` would therefore be sent as `artifacts<turn>report.xlsx` (flattened, not shortened). `_strip_uuid_prefix` :94 strips only a leading `<uuid>_`.
- Workspace download route `src/codemie/rest_api/routers/agent_workspace.py:181-195` already names the file `PurePosixPath(file_path).name`.

#### Architecture and Layers Affected
- Tool layer (`src/codemie_tools/**`; no module-level `codemie.*` imports, `store_artifact` imports config lazily): `base/artifact_storage.py`, `file_system/*`, `code_executor/file_export_service.py`, `workspace/*`.
- MCP service: `src/codemie/service/mcp/{models,client,toolkit,toolkit_service}.py`. Enterprise adapter: `enterprise/plugin/dependencies.py`.
- Service layer: `agent_workspace_service.py`, `file_service/download_rules.py`, `share_conversation_service.py`. Agent runtime: `agents/tools/agent.py`. REST: `routers/files.py`.
- Guides: `.ai-run/guides/architecture/layered-architecture.md`, `service-layer-patterns.md`, `agents/agent-tools.md`.

#### Integration Points
- **MCP-Connect `/bridge` (verified, `codemie-mcp-connect-service/`)**: `tools/call` returns the SDK result untouched: `methods.py:102` `return await session.call_tool(name, clean_arguments)` (only `arguments` get `None` values stripped, :237-245). `routes.py:301-314` serialises with `result.model_dump(mode="json", exclude_none=True)` and returns it as `JSONResponse` (both single-usage `:122-159` and cached `:162-237` paths hit the same line). No content flattening anywhere in the service. A protocol error becomes `CallToolResult(isError=True, content=[TextContent])` (`methods.py:117,184-192`). SDK pinned `mcp = "1.30.0"` (`pyproject.toml:18`). The SDK is not installed in this checkout, so the exact field names of `EmbeddedResource`/`ResourceLink` after `model_dump` are inferred from the MCP spec (`resource:{uri,mimeType,blob|text}`, `resource_link:{uri,name,mimeType}`, camelCase), not read from `mcp/types.py`.
- **mcp-proxy (verified, `/home/aliaksei_chumakou/codemie-dev/mcp-proxy/`)**: a local `/bridge` stand-in that forwards to the real EPAM radar MCP server (`config.py:9` `RADAR_MCP_URL=https://mcp.epam.com/mcp/radar`), with its own OAuth login (`authorize.py`, `auth_storage.py`). It IS on the tool-result path and DOES alter content: `radar_client.py:93-119` `_translate_content` flattens (`EmbeddedResource` blob -> `{type:"resource", data, mimeType}` with uri/filename dropped; embedded text -> `{type:"text"}`; `AudioContent` -> `{type:"resource"}`; `ResourceLink` -> text `"[resource link] name: uri"`), applied at :135-138. `README.md` and `tests/test_translate.py` pin this flattening on purpose ("what CodeMie's MCPToolContentItem parser on this branch accepts"). It uses `mcp_types`/`httpx2` (mcp 2.x types, snake_case `mime_type`), not MCP-Connect's 1.30. The coordinator's description ("mocking proxy for the EPAM time proxy, auth only") does not match what is on disk; I found nothing about a time proxy. The design doc it cites, `MyDocs/McpProxyDesign.md`, was not found under `/home/aliaksei_chumakou/codemie-dev`.
- Context carriers (verified): `MCPToolkitService.get_mcp_server_tools(...)` has `conversation_id`, `workflow_execution_id`, `assistant_id`, `project_name`, `marketplace_scope` params (`toolkit_service.py:185-199`) and builds `MCPExecutionContext(user_id, assistant_id, project_name, marketplace_scope, workflow_execution_id, conversation_id, session_binding_hash, request_headers, user_context=UserContext.from_user(get_current_user()))` (:233-244). So conversation id and the requesting user already travel with a `ContextAwareMCPTool` (`toolkit.py:320-361`, `_execution_context`). **`request_uuid` is not in that context or in the `get_mcp_server_tools` signature.**
- Chat path: `AssistantService.build_agent(assistant, request, user, request_uuid, ...)` (`assistant_service.py:513-523`) passes `request` (has `conversation_id`) and `request_uuid` to `ToolkitService.get_tools(...)` (:591-602). Workflow path: `build_agent_for_workflow` (:800; :830 is only a log line) builds the request with `conversation_id=execution_id, workflow_execution_id=execution_id` (:845-855).
- Producers' wiring: `ToolkitSettingService.get_file_system_toolkit(... request_uuid ...)` (`toolkit_settings_service.py:190-231`) receives `request_uuid` but uses it only for the image generator and chat model; the tools get `configs={"user_id": user.id}`, `file_repository`, no conversation id. `get_agent_workspace_toolkit` (:234-254) does get `request.conversation_id`, `user`, `request_uuid`. How the chat caller passes `conversation_id` into `get_mcp_server_tools` (call site in `tools/toolkit_service.py`) was not read.
- Workflow tool node (verified, `workflows/nodes/tool_node.py:128-171`): calls `get_mcp_server_tools(user_id=owner_or_user, conversation_id=self.execution_id, workflow_execution_id=...)`, executes, then joins `str(item)` of raw content items (:167-171), bypassing `_post_process_output_content`. This matches review B6 (out of scope).
- Enterprise `codemie_enterprise` package: not installed here; its `MCPToolInvocationResponse` model is unverifiable.

#### Patterns and Conventions
- `CodeMieTool` subclasses with `execute()` / `_post_process_output_content()`; per-call state via ContextVar (`_write_notice`).
- Token = `sandbox:/v1/files/` + `FileObject.to_encoded_url()` (base64 of mime, owner, name); workspace blobs use owner `workspace-<id>`.
- File tools return failure text rather than raising; REST edge raises `ExtendedHTTPException`.
- Ruff via `make ruff` (`.ai-run/guides/quality-gates.md`); tests only when requested (AGENTS.md).

---

### 3. Documentation Findings

#### Guides and Architecture Docs
- `.ai-run/guides/integration/mcp-integration.md`, `agents/agent-tools.md`, `agents/custom-tool-creation.md`, `architecture/layered-architecture.md`, `development/security-patterns.md`, `development/error-handling.md`, `testing/testing-patterns.md`, `quality-gates.md`, `security/README.md` exist; not read beyond existence.
- Front end side (read): `codemie-ui/` story `children/ui-workspace-artifact-links/story.md` and its `technical-analysis.md` describe the consumer contract (Section 8).

#### Architectural Decisions
- Inline comments: `download_rules.py:161-167` (Rule C accepted residual risk), `toolkit.py:307-312` (owner from `user_context`), `tools.py:102-103` (zip guard). Story decisions D1-D5 (child) and D1-D5 (root; root D5 also puts the share-grant ownership bound in this MR).

#### Derived Conventions
- `<uuid>_<name>` is what both `files.py:_strip_uuid_prefix` and the UI's `stripUuidPrefix` remove; `<uuid>.<ext>` (MCP artifacts today) is stripped by neither.

---

### 4. Testing Landscape

#### Existing Coverage
- MCP: `tests/codemie/service/mcp/test_toolkit_post_processing.py`, `test_toolkit_mcp_tool.py`, `test_execution_context_integration.py` (`test_owner_is_the_request_initiator`).
- Tools: `tests/codemie_tools/base/test_artifact_storage.py`; `.../file_system/{test_attach_file_tool,test_file_system_tools,test_generate_image_tool}.py`; `tests/codemie_tools/data_management/test_file_system_toolkit.py` (`test_write_and_attach_tools_receive_repository`); `.../code_executor/test_file_export_service.py`; `.../workspace/test_generate_image_tool_v2.py`, `test_agent_workspace_toolkit.py`.
- Authorization/files: `tests/codemie/service/file_service/test_download_rules.py`, `tests/codemie/service/test_share_conversation_service.py`, `test_share_conversation_hide_tool_outputs.py`, `tests/codemie/rest_api/routers/test_files.py`.
- Workspace: `tests/codemie/service/test_agent_workspace_service_*.py`, `test_agent_workspace_glob.py`, `tests/codemie/repository/test_agent_workspace_repository.py`.
- Other repos (verified to exist): `mcp-proxy/tests/test_translate.py` (pins the flattening); `codemie-mcp-connect-service/tests/test_mcp_methods.py`, `test_bridge_validation.py` (not read); UI `src/components/markdown/__tests__/{artifactLink,Markdown.artifactLinks,MarkdownTokens.artifactChip}.test.ts(x)`, `src/utils/__tests__/api.downloadFileStream.test.ts`, `src/store/__tests__/files.test.ts`.

#### Testing Framework and Patterns
- pytest; MCP toolkit tests mix `unittest.TestCase` and `MagicMock(spec=...)`. MCP tests build `MCPToolContentItem` directly in the flat shape; none parses wire JSON. Policy: `.ai-run/guides/testing/testing-patterns.md`. Versions not read.

#### Coverage Gaps
- No test parses real MCP-Connect JSON (embedded resource / resource_link). Because mcp-proxy flattens, running through it cannot produce such JSON either.
- `mcp_converter_adapter` / `_convert_mcp_response_to_tool_message` via the plugin path, and `download_workspace_file` / `AgentWorkspaceService.download_file`: no tests within 3 caller hops.
- `read_file` Content-Disposition for a path-shaped name: not covered (nothing produces such names yet).
- No test for the share rule refusing a foreign-workspace or foreign-user token. Three copies of the link regex are untested for drift.

---

### 5. Configuration and Environment

#### Environment Variables
- `FILES_STORAGE_MAX_UPLOAD_SIZE` (`artifact_storage.py:57`; also `files.py:297` for uploads) - cap, checked after bytes exist today.
- `MCP_TOOL_TOKENS_SIZE_LIMIT`, `MCP_CLIENT_TIMEOUT`; `CODEMIE_STORAGE_BUCKET_NAME` (Rule B owner); `FILE_SYSTEM_TOOLS_ENABLED`, `CODE_EXECUTOR_ENABLED` (gate the file tools, `file_system/toolkit.py:77-97`).
- `MCP_IMAGES_SUBDIR` in `src/codemie/repository/repository_factory.py` (Rule C).
- mcp-proxy only: `RADAR_MCP_URL`, `RADAR_TOKEN_FILE`, `RADAR_OAUTH_CALLBACK_PORT`, `PROXY_PORT` (`mcp-proxy/config.py`, `.env.example`).

#### Configuration Files
- No config file specific to artifact publishing. `config/mcp/mcp-commands-config.yaml` and `managed_mcp_config.py` are unrelated.

#### Feature Flags and Deployment Concerns
- File backend via `FileRepositoryFactory.get_current_repository()`; `store_artifact` returns None when none is configured.
- Security-relevant, no pipeline: MR description must ask a reviewer to post `/sanity` (AGENTS.md).

---

### 6. Risk Indicators

- Authorization: `_conversation_refers_to_file` / Rule E grants any token present in history (`download_rules.py:91-135`); tightening can cut access to links that work today.
- Rule C (`MCP_IMAGES_SUBDIR` readable by every authenticated user) must stay for pre-existing images while new writes stop using it (`toolkit.py:384-390`).
- Share grants flow to the UI through the share endpoint's `shared_file_urls` map keyed by the tokens `collect_message_file_tokens` returns (UI `sharedFileGrants.ts:18-22`); removing thought scanning (D5) also removes grants for links that appear only in thoughts. Whether the UI renders such links was not checked.
- Three separate copies of the link regex: `agent.py:36`, `share_conversation_service.py:34` (adds a backtick), UI `sharedFileGrants.ts:28` (no backtick). A shared pattern must stay compatible with UI token handling; `download_rules` imports from the share service, so consolidation must avoid a circular import.
- `/v1/files` flattens path-shaped names (`files.py:261`, `:68`) and the UI ignores Content-Disposition when the token carries a name (`store/files.ts:97-108`); a back-end-only fix covers non-UI clients, the UI fix is its own child.
- Enterprise plugin path passes no owner (`dependencies.py:389`); the enterprise model (`codemie_enterprise`) cannot be inspected here.
- `request_uuid` has no existing route to MCP tools, the file-system tools or `FileExportService`; conversation id reaches MCP (execution context) and the workspace toolkit, not the file-system toolkit (`configs={"user_id"}` only).
- `codemie_tools` avoids module-level `codemie.*` imports; where a shared publisher lives is open (V2 image tool already imports the workspace service from `codemie_tools`).
- `artifacts/` immutability touches `edit_file`, delete, `upsert_text_file` and script sync; none currently blocks the prefix (read from the shown code, not exhaustively).
- `register_generated_files` dedupes on `(owner,name,mime)`; a published file must still register once when its URL is later extracted from the response text.
- Workflow tool nodes bypass post-processing (`tool_node.py:167-171`): artifacts from workflow MCP nodes will not be published (B6, out of scope), but the same tool object is shared.
- Local verification mismatch: mcp-proxy flattens, so it cannot reproduce the B1 bug or test the fix until it forwards the spec shape (root Constraints); its unit test pins the old behaviour.
- No size check before decode or before reading a file (`store_artifact` uses `len(content)`; `AttachFileTool` reads the whole file).
- Speculative: the content model will probably need new optional fields for embedded resources and resource links; spec and plan decide.
- Speculative: removing `is_resource()` and the flat shape forces edits across `test_toolkit_post_processing.py`.
- Speculative: D5 relies on workspace registration covering MCP image links; that registration swallows errors (`agent.py:139-140`).
- Security-relevant, no CI re-check: reviewer must post `/sanity`.

---

### 7. Summary for Complexity Assessment

The change reaches five layers: the MCP service (content model, conversion functions in `toolkit.py`), the enterprise plugin adapter, the shared tool-layer storage helper plus four file producers (`WriteFileTool`, `AttachFileTool`, `FileExportService`, image tool), the workspace service and post-turn registration, and share/download authorization (`download_rules.py`, `share_conversation_service.py`), with a REST naming touch in `files.py`. No single "publisher" exists; five modules each call `store_artifact` or `write_file` themselves. The workspace API and `GenerateWorkspaceImageToolV2` give a working precedent. Roughly 12-16 source files and a similar number of test files are in play. MCP-Connect passes the SDK result through unflattened, so the real wire carries the spec shapes; only the local mcp-proxy flattens.

Technical novelty is moderate: no new dependency, but it crosses the `codemie_tools`/`codemie` boundary, changes an authorization rule with no ownership bound today, and must keep legacy behaviours (shared-namespace images, `<uuid>_` tokens, idempotent post-turn registration). Conversation id and the requesting user already reach MCP tools through `MCPExecutionContext`; `request_uuid` does not reach MCP tools or the file-system tools, and the file-system tools get no conversation id. The enterprise model's shape and the chat-path call into `get_mcp_server_tools` remain unverified.

Test posture: producers, storage helper, download rules and share service have focused tests; MCP conversion tests use hand-built flat items and none parse wire JSON; the plugin adapter, workspace download path and the link regexes are untested. Key risks: authorization regression on shared links, share-grant coverage after thought scanning is removed, plugin-path owner propagation across an external package, `artifacts/` immutability across all workspace mutation paths, and the local proxy hiding the main bug.

---

### 8. External References

Status: VERIFIED (read, with location) or UNVERIFIED (not established).

- Child story: resolved and read. AC groups: MCP content (B1,B2), One publisher (B5: `artifacts/<turn-id>/<name>`, duplicate `<name> (2).<ext>`, refuse oversize before decode, zip validation in the publisher, logged per-user fallback with no conversation, no duplicate in "Browse files"), Ownership (B2,B4), Producers (B7,B8,B10,B11), Share downloads (B9), download name = last path segment.
- Root story `docs/stories/2026-10-22-mcp-artifact-publishing/story.md` (86 lines): VERIFIED read. Facts: children are `backend-artifact-publisher` then `ui-workspace-artifact-links`; AC1 end-to-end `closed_positions_analysis.xlsx` downloadable with that exact name and visible in "Browse files"; AC2 shared-conversation recipient downloads every artifact the sharer's conversation produced, foreign-owned pasted links refused; Constraints: back end lands first, the `artifacts/<turn>/<name>` token is the producer-consumer contract, local verification via `../mcp-proxy` "must forward the spec resource shape (no flattening)", back-end MR needs reviewer `/sanity`; Decisions D1-D5 as in the child (root D5: share-grant ownership bound is in this MR).
- MCP-Connect: VERIFIED. `methods.py:102`, `routes.py:301-314` (no flattening, `model_dump(mode="json", exclude_none=True)`), `methods.py:184-192` (error -> `isError` text result), `pyproject.toml:18` (mcp 1.30.0). UNVERIFIED: exact serialized field names of `EmbeddedResource`/`ResourceLink` (SDK not installed; spec-based).
- mcp-proxy: VERIFIED (see Section 2): auth to radar plus content flattening on the tool-result path (`radar_client.py:93-138`); `app.py:26-51` forwards `tools/list` and `tools/call` only. Discrepancy with the coordinator's description (time proxy, auth only): not supported by the files on disk. UNVERIFIED: `MyDocs/McpProxyDesign.md` (not found); whether a different version of the proxy exists elsewhere.
- UI `codemie-ui/` (VERIFIED, read): `src/components/markdown/artifactLink.ts:68-95` builds `{fileId,fileName}` from the base64 token, name = `sanitizeFileName(stripUuidPrefix(decodedName))`; `src/utils/api.ts:69-79` `sanitizeFileName` replaces `/` and `\` with `_` (so `artifacts/<t>/x.xlsx` -> `artifacts_<t>_x.xlsx`); `api.ts:81-88` `stripUuidPrefix` (mirrors `files.py`); `src/store/files.ts:87-114` `downloadFile` uses the decoded token name, not Content-Disposition, unless the id is a bare UUID or has no name; `api.ts:270-317` `downloadFileStream` falls back to Content-Disposition only when no `fileName` is given; `src/utils/sharedFileGrants.ts:18-50` maps each bare token to `?share_token=` form via the share endpoint's `shared_file_urls`; `src/components/markdown/Markdown.utils.ts:112-114` rewrites `sandbox:/v1/files/` to `${api.BASE_URL}/v1/files/`. UI child story expects from the back end: tokens `artifacts/<turn-id>/<name>` with owner `workspace-<id>`; duplicate names `report (2).csv`; share grant carried for workspace links; legacy `<uuid>_<name>` still working; "not delivered" and failure reasons as plain text with no link; "Browse files" showing nested `artifacts/<turn-id>/` folders (nested display UNVERIFIED on the UI side). UNVERIFIED: any `mcp_images` handling in the UI (no occurrence surfaced; search was not exhaustive).
- Prior analysis: re-verified. Changed or newly confirmed: share collector at `share_conversation_service.py:30-69` with `_thought_tokens`; `_conversation_refers_to_file` delegates to `collect_message_file_tokens`; `_has_share_grant` also checks the sharer's workspace; `files.py` uuid regex at :88-91 (UI comment cites :91); MCP-Connect routes.py:303-304 cited by root D1 is in fact :301-314.
- `client.py:269`: VERIFIED (:268-275). `assistant_service.py:830`: VERIFIED as a log line in `build_agent_for_workflow`; the context facts are at :845-855 and `build_agent` :513-602. `tool_node.py:138`: VERIFIED, the `conversation_id=self.execution_id` argument of `get_mcp_server_tools` (:135-146), and the post-processing bypass at :167-171.
- Enterprise `codemie_enterprise`: UNVERIFIED (not installed).
