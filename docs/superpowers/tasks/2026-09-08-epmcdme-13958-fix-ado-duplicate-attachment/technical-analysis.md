# Technical Research

**Task**: ADO azure-devops attachments work-item
**Generated**: 2026-09-08T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Bug fix: Same chat document is reattached to an ADO work item on every update. When a user provides a document in the assistant chat and uses the same chat to update an existing Azure DevOps work item, the same document is attached again with each update. As a result, the ADO work item accumulates multiple duplicate copies of the same document even though the user did not provide a new file for each update. Acceptance criteria: Duplicate attachment upload is prevented across repeated ADO work item updates in the same chat. New files provided later in the chat are still attached correctly. Attachment deduplication uses reliable identifiers such as file ID, attachment URL, content hash, or equivalent metadata.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie_tools/azure_devops/work_item/tools.py` — `BaseAzureDevOpsFileWorkItemTool._process_attachments` (lines 157–199): iterates every file in `config.input_files` and uploads + attaches each unconditionally — **primary bug site**
- `src/codemie_tools/azure_devops/work_item/tools.py` — `UpdateWorkItemTool`, `CreateWorkItemTool`: concrete tool classes that call `_process_attachments`
- `src/codemie_tools/base/file_tool_mixin.py` — `FileToolMixin._resolve_files()`: reads all files from `config.input_files` with no dedup guard against already-attached files
- `src/codemie_tools/azure_devops/attachment_mixin.py` — `AzureDevOpsAttachmentMixin._upload_attachment()`: blindly uploads any content passed; returns the new attachment URL; no idempotency check
- `src/codemie_tools/azure_devops/work_item/models.py` — `AzureDevOpsWorkItemConfig(FileConfigMixin)`: config holding `input_files`; no dedup field
- `src/codemie/service/tools/toolkit_service.py` — `ToolkitService._inject_runtime_dependencies()`: injects `file_objects` into `stored_config.input_files` on every request turn; `_get_file_objects_from_request()` calls `build_unique_file_objects_list` which collects ALL files from the entire conversation history
- `src/codemie/core/utils.py` — `build_unique_file_objects_list` / `build_unique_file_objects`: deduplicates files by `file_name` encoding key across conversation history but returns ALL accumulated files on each turn; no mechanism to filter out files already attached to a specific work item

### Architecture and Layers Affected
- **Tool execution layer** (`codemie_tools`): `_process_attachments` in `tools.py` — fix target
- **Service/factory layer** (`codemie.service.tools.toolkit_service`): `_inject_runtime_dependencies` — secondary cause; populates `input_files` from full conversation history on every turn
- **File management utility layer** (`codemie.core.utils`): `build_unique_file_objects_list` — accumulates files per turn with only name-based dedup, no work-item-state awareness

### Integration Points
- ADO SDK: `azure.devops.v7_1.work_item_tracking` — `get_work_item`, `update_work_item`; `_client.get_work_item(id=work_item_id, expand="Relations")` already used in `MoveWorkItemTool` and `GetWorkItemAttachmentContentTool`
- ADO REST: `/_apis/wit/attachments?fileName=<name>&api-version=7.1` — attachment upload endpoint (via `httpx`)
- `FileConfigMixin.input_files`: runtime-injected `FileObject` list — the vector through which the same files recur across turns

### Patterns and Conventions
- `FileConfigMixin.input_files` is injected fresh on every agent turn with the full accumulated file set from conversation history
- `_process_attachments` iterates every file in `input_files` unconditionally
- `_upload_attachment` always creates a new ADO attachment resource even for identical content
- No pre-flight check queries existing `AttachedFile` relations on the target work item before attaching
- The pattern for fetching work item relations already exists (`_client.get_work_item(id=work_item_id, expand="Relations")`)

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/agents/agent-tools.md` — covers tool ownership pattern: keep reusable integrations under `codemie_tools`

### Architectural Decisions
- No ADR found. Tool ownership convention is to fix at the `codemie_tools` layer (closest to the ADO API), not in the service layer.

### Derived Conventions
- Fix should be applied inside `_process_attachments` in `tools.py` — not in `toolkit_service.py` or `utils.py` — as per the tool ownership pattern. The service layer is not the right owner for ADO-specific deduplication logic.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie_tools/azure_devops/work_item/test_tools.py` — covers `UpdateWorkItemTool.execute` with a single-call success case only (lines 128–141); no test for repeated updates with same files
- `tests/codemie_tools/azure_devops/work_item/test_tools.py` — `GetWorkItemAttachmentContentTool`, `RemoveWorkItemRelationTool`, `MoveWorkItemTool` have comprehensive test cases
- `tests/codemie_tools/azure_devops/work_item/test_toolkit.py` — toolkit structure only; no behavioral tests

### Testing Framework and Patterns
- pytest with mocked ADO client (`patch`/`MagicMock`)
- Tests mock `_client.get_work_item` and `_client.update_work_item`; the pattern is established for injecting fake relation data

### Coverage Gaps
- No test for `UpdateWorkItemTool` called twice in a row with the same `input_files` — must be added
- No test verifying that a file already present in `AttachedFile` relations is NOT re-uploaded on the second call
- No test for new-file-on-second-call still being attached correctly

---

## 5. Configuration and Environment

### Environment Variables
- `ADO_ORGANIZATION_URL`, `ADO_PROJECT`, `ADO_TOKEN` — ADO credentials (via `AzureDevOpsWorkItemConfig`)

### Configuration Files
- `AzureDevOpsWorkItemConfig.input_files` — runtime-injected per turn from `FileConfigMixin`; no persistent dedup state field

### Feature Flags and Deployment Concerns
- No feature flags. Fix is purely behavioral in `_process_attachments`. Backward-compatible: new work item creation is unaffected (no existing relations to check against).

---

## 6. Risk Indicators

- **Root cause**: `_process_attachments` in `src/codemie_tools/azure_devops/work_item/tools.py` (lines 157–199) uploads and attaches every file in `config.input_files` unconditionally, with no guard against files already in the work item's `AttachedFile` relations.
- **Secondary accumulation cause**: `_inject_runtime_dependencies` in `src/codemie/service/tools/toolkit_service.py` re-injects the full conversation file history into `config.input_files` on every agent turn.
- **Dedup key misalignment**: `build_unique_file_objects_list` deduplicates by encoded `file_name` URL — this prevents duplicate `FileObject` entries in memory but does not prevent re-uploading files already attached to the ADO work item.
- **Test gap**: `TestUpdateWorkItemTool` has no multi-call scenario — the regression must be caught by a new parametrized test covering repeated updates.
- **API call cost**: fetching work item relations requires an extra `get_work_item(expand="Relations")` call per `_process_attachments` invocation. Acceptable (1 extra GET per update) but should be noted in the fix.
- **Filename collision risk**: dedup-by-filename could suppress a legitimately new file with the same name. Mitigated by documenting the behavior: if the filename already appears in relations, skip regardless. The AC allows filename as a valid dedup identifier.
- No codegraph results (codegraph unavailable — filesystem research used).

---

## 7. Summary for Complexity Assessment

The bug is well-localized: `BaseAzureDevOpsFileWorkItemTool._process_attachments` in `src/codemie_tools/azure_devops/work_item/tools.py` processes every file in `config.input_files` unconditionally on every invocation. Because `ToolkitService._inject_runtime_dependencies` rebuilds `input_files` from the full conversation history on each agent turn, files from earlier turns are always present and get re-uploaded and re-attached. The fix requires fetching existing `AttachedFile` relations from the target work item (using the already-established `get_work_item(expand="Relations")` pattern) before the upload loop, then skipping any file whose name already appears in those relations.

The change surface is small: one method in `tools.py`, one new helper or inline check, and a set of targeted unit tests. No service-layer or utility-layer changes are needed. The ADO SDK pattern for reading work item relations is already used in `MoveWorkItemTool` and `GetWorkItemAttachmentContentTool`, so no novel integration work is required. The fix is backward-compatible: work item creation (no prior relations) and new-file attachment (filename not in existing relations) are both unaffected.

The primary risk is the extra `get_work_item` API call added to every `_process_attachments` invocation (including creation paths where there are no relations to check). This is one additional GET and is acceptable. A secondary risk is that deduplication by filename could suppress a legitimately new file with the same name — this matches the AC's explicitly listed dedup identifiers and is the simplest correct approach.
