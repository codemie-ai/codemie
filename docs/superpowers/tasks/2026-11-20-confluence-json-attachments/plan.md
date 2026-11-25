# Confluence generated-file attachments Implementation Plan (EPMCDME-16431)

> **For agentic workers:** use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** An agent can attach a file it generated in the current conversation to a Confluence page, and failures never imply success.

**Architecture:** A read-only Protocol in `codemie_tools/base` is injected into `GenericConfluenceTool` by `ToolkitService`, bound to the conversation and user at build time. A Confluence-independent matcher picks the candidate; the Confluence tool stops using `FileToolMixin` resolve/filter on the attachment path.

**Tech Stack:** Python, pydantic, atlassian `Confluence.attach_content`, pytest.

**Spec:** `spec.md` in this directory. Commit per task using the repository's existing convention (ticket EPMCDME-16431).

## Global Constraints
- `FileToolMixin` and `core/utils.py` collector are not modified.
- Tools never import `codemie.service`.
- Any file type; bare-name strict matching; no all-files fallback.

## Findings from code checks (spec open risks)
- **Conversation id at build time:** `_initialize_tool` / `_initialize_toolkit_tools` / `get_core_tools` do NOT receive it (toolkit_service.py:694-791). `collect_agent_tools` has `request.conversation_id` and `user` (l.588-598). Plumb an optional `conversation_id` through the three methods. Other callers of `get_core_tools` (tool_service.py:138,204; tool_execution_service.py:499) leave it `None`, so the tool reports "generated files are not available here" (rule 9).
- **attach_content same name:** installed lib (`atlassian/confluence/__init__.py:1548-1622`) GETs `.../child/attachment?filename=name`; if found it POSTs to `.../child/attachment/{id}/data`, which adds a new version and keeps the old one (rule 10 holds). That `/data` POST happens inside the library, not via our tool. If the model itself POSTs to `.../child/attachment/{id}/data`, it is the same upload, so rule 1 treats `/child/attachment` optionally followed by `/{id}/data` as the upload URL (GET/DELETE on it are still not uploads).
- **Timeout:** `rest_client.py` defaults to 75 s and `attach_content` uses `self.post`, so the default applies; no change, pinned by no test beyond not overriding it.
- **State:** listing/reading is local to each call; no instance state is set (script path safe).

## Review Focus
- Name from another conversation returns not found (Task 2, 4).
- Same bare name in turns 1 and 5: newest wins (Task 1).
- Upload and generated name clash is refused listing both (Task 1).
- Empty file and failed upload name the file and never say "Successfully" (Task 3).
- GET/DELETE on `.../child/attachment/{id}` stay plain requests (Task 3).

## Task 1: Generated-file Protocol and matcher
**Files:** Create `src/codemie_tools/base/generated_files.py`; Test `tests/codemie_tools/base/test_generated_files.py`.
**Interfaces — Produces:**
`GeneratedFileInfo(name: str, turn_id: str, size: int, updated: datetime | None)` (frozen dataclass);
`GeneratedFileSource(Protocol)`: `list_generated() -> list[GeneratedFileInfo]`, `read_generated(info) -> bytes`; includes the `__get_pydantic_core_schema__` any-schema hook as in `artifact_publishing.py:64-67`;
`AttachmentCandidate(source: Literal["generated","uploaded"], name: str, load: Callable[[], bytes], turn_id: str | None)`;
`match_attachment(name: str, candidates: list[AttachmentCandidate]) -> AttachmentCandidate` raising `ToolException`.
- Test-first: yes — `test_match_*`: exact bare name returns the candidate; no match raises and message lists available names; upload+generated same name raises listing both candidates; generated duplicates (turn 1 vs 5) return newest by `updated`; case/partial names do not match.
- [ ] Write tests, run (fail), implement matcher (pure, no I/O), run (pass).

## Task 2: Workspace adapter
**Files:** Create `src/codemie/service/generated_files_reader.py`; Test `tests/codemie/service/test_generated_files_reader.py`.
**Interfaces — Consumes:** Task 1 Protocol. **Produces:** `WorkspaceGeneratedFiles(conversation_id: str, user_id: str)` implementing `GeneratedFileSource`.
- Wraps `AgentWorkspaceRepository.get_by_conversation_for_user` and `list_files` (agent_workspace_repository.py:72,121), keeps only paths `artifacts/<turn_id>/<name>` (bare name = last segment, `AgentWorkspaceFile.size`, `update_date`); reads bytes via `file_repository.read_file(file_name=blob_name, owner=blob_owner, mime_type=...)` as in `agent_workspace_service.py:415-422`. Refuses with `ToolException` when `size` exceeds `config.FILES_STORAGE_MAX_UPLOAD_SIZE` before reading. Never uses `get_workspace_input_files`.
- Test-first: yes — `test_lists_only_artifact_files_of_bound_conversation`, `test_other_conversation_returns_empty`, `test_oversize_file_refused_without_read`, `test_read_returns_bytes`.
- [ ] Tests (fail), implement, pass.

## Task 3: Confluence attachment path
**Files:** Modify `src/codemie_tools/core/project_management/confluence/tools.py` (`_perform` l.154-189, `_is_attachment_operation` l.212-214, `_handle_file_attachments` l.216-265, new field); Test `tests/codemie_tools/core/project_management/confluence/test_attachments.py` (new; reuse `confluence_config` fixture and the `@patch` of `Confluence`/`validate_creds` from `test_tools.py`).
**Interfaces — Consumes:** Task 1. **Produces:** tool field `generated_files: Optional[GeneratedFileSource] = None`.
- `_is_attachment_operation(method, url)`: True only for POST/PUT and URL matching `/child/attachment(/\d+/data)?$`.
- On upload path: build candidates from `generated_files.list_generated()` (if the field is `None` and the name is not an upload: raise "generated files are not available here") plus `config.input_files` (uploaded; skip nothing silently); require a `file`/`files` param naming files, else raise an explicit error listing available names (rule 7); `match_attachment` per name; empty content raises naming the file; call `attach_content(content, name=<bare name>, content_type=mimetypes guess, page_id=_extract_page_id)`; falsy/exception raises `ToolException` naming the failed file and listing files already attached (retry only the failed one; a retry adds a version). Never returns "Successfully" for a failure. Never falls through to a plain request.
- Test-first: yes — tests: generated JSON attached with same name and bytes (AC1/2); earlier-turn file works; clash refused; not found lists names; no file param error; no available files error; `generated_files=None` message; empty file names file; upload failure names file; second-file failure lists first as attached; uploaded file still attaches (AC5); GET and DELETE on `.../child/attachment/123` and POST to `.../content/1` text go through plain request (AC4/6); PUT to `.../child/attachment/9/data` counts as upload.
- [ ] Write tests (fail), implement, run `pytest tests/codemie_tools/core/project_management/confluence -v` (pass).

## Task 4: ToolkitService wiring
**Files:** Modify `src/codemie/service/tools/toolkit_service.py` (`collect_agent_tools` l.588, `get_core_tools` l.694, `_initialize_toolkit_tools` l.726, `_initialize_tool` l.774/866-891); Test `tests/codemie/service/tools/test_toolkit_service_generated_files.py`.
- Add optional `conversation_id: str | None = None` through the three methods; `collect_agent_tools` passes `request.conversation_id if request else None`. In `_initialize_tool`, when `'generated_files' in tool_fields` and `conversation_id` set, add `extra['generated_files'] = WorkspaceGeneratedFiles(conversation_id, user_id)` (user id is the model-independent `user_id` argument already present). Nothing from params can alter it.
- Test-first: yes — `test_confluence_tool_gets_adapter_bound_to_request_conversation`, `test_no_conversation_leaves_generated_files_none`, `test_tool_without_field_unchanged`.
- [ ] Tests (fail), implement, pass.

## Task 5: Model-facing text
**Files:** Modify `src/codemie_tools/core/project_management/confluence/tools_vars.py:31,40,51` and `ConfluenceInput.params` (tools.py:65-66); Test: extend `test_attachments.py`.
- State: generated files are attachable by exact bare file name via `{"file": "report.json"}` on POST/PUT `.../child/attachment`; uploaded files likewise; names must match exactly.
- Test-first: yes — `test_description_mentions_generated_files_and_exact_name` asserts both strings appear.
- [ ] Test (fail), edit, pass.

## Negative-constraint pass
- No `FileToolMixin`/`core/utils.py` change: Tasks 1-5 touch neither.
- No all-files fallback: Task 1 strict match, Task 3 never calls `_filter_requested_files`.
- GET/DELETE unchanged: Task 3 tests.
- No cross-conversation access / path naming: Tasks 2, 4 (bound at construction; bare names).
- No partial attached/not-attached report: Task 3 only appends already-attached names to the single error (arch-review plan note on retry guidance), no separate report.
- No UI/SDK/REST change; no workflow-node support: Task 4 leaves `None`.
