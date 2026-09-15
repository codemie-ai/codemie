# Technical Research

**Task**: chat file attachment jira tool conversation history
**Generated**: 2026-07-27T00:00:00Z
**Research path**: codegraph

---

## 1. Original Context

EPMCDME-12227 (Bug, Backend): Repeated attach request duplicates previous file.

## Description
When a user uploads a file in chat and asks the assistant to attach it to an existing Jira ticket, each attach request should only process the file(s) provided for that specific request. Currently, when a second file is uploaded in a later turn and the user asks to attach it, the assistant incorrectly reuses the previously uploaded file and attaches both files again. This causes duplicate attachments on the Jira ticket.

## Steps To Reproduce
1. Upload test1.png in chat and ask: "Attach file to EPMCDME-XXXXX". Assistant successfully attaches test1.png.
2. Upload test2.png in chat and ask: "Attach file to EPMCDME-XXXXX". Assistant attaches BOTH test2.png and test1.png.

## Expected result
- Only test2.png is attached during step 2.
- Previously uploaded files are not automatically re-included in later attachment requests unless explicitly requested by the user.
- Jira attachment tool calls contain only the file(s) requested in the current user turn.

## Acceptance criteria
- Files collected for attachment requests are limited to the most recent relevant user turn unless the user explicitly requests older files.
- A second attach request with a newly uploaded file does not reattach prior files by default.
- Jira tool invocation for attachment upload includes only the file(s) intended for the current request.
- No duplicate attachment is added to the Jira ticket due to conversation history accumulation.
- Regression coverage confirms sequential file attachment requests behave correctly across multiple chat turns.

## Root Cause (from ticket)
1. src/codemie/core/utils.py — _collect_files_from_conversation() accumulates files from all prior conversation turns, not just the current message.
2. src/codemie_tools/base/file_tool_mixin.py — _filter_requested_files() falls back to returning all collected files when the LLM does not specify exact filenames in tool params.

## Suggested Fix (from ticket)
Scope _collect_files_from_conversation() to only collect files from the most recent user turn, so previously uploaded files are not re-included in subsequent tool calls.

---

## 2. Codebase Findings

### Existing Implementations

**File collection pipeline (`src/codemie/core/utils.py`, lines 580–689):**

- `_get_unique_messages_from_history` (lines 580–604): deduplicates conversation messages by `(role, history_index)` key using last-write-wins semantics; this handles edited messages — if a user edits a message and removes a file, the edited version replaces the original in the dict; applies an optional `history_index` upper bound (only messages with `message.history_index < history_index` are included) but does NOT scope to the current turn only
- `_collect_files_from_conversation(conversation_id, history_index, unique_files_dict)` (lines 607–628): loads `Conversation` from DB via `find_by_id`, deduplicates history messages via `_get_unique_messages_from_history`, then calls `_process_file_names_to_objects` on every surviving message's `file_names`; mutates the passed-in `unique_files_dict` in-place with no turn-scoping; this is the **primary root-cause locus**
- `build_unique_file_objects(file_names, conversation_id, history_index)` (lines 631–664): aggregation entry point; (1) processes current-turn `file_names` via `_process_file_names_to_objects`, then (2) overlays all files from conversation history via `_collect_files_from_conversation`; history files overwrite current names on key collision
- `build_unique_file_objects_list(file_names, conversation_id, history_index)` (lines 667–689): thin list wrapper over `build_unique_file_objects`; the function called by `tool_node.py`

**FileToolMixin (`src/codemie_tools/base/file_tool_mixin.py`, lines 26–184):**

- `FileToolMixin`: plain Python mixin (no `__init__`, no state); reads `self.config.input_files` duck-typed; provides `_get_supported_mime_types()`, `_get_supported_extensions()`, `_is_supported_file()`, `_get_supported_files()`, `_resolve_files()`, and `_filter_requested_files()`
- `_get_supported_files()` (line 84): reads `self.config.input_files`, filters by `_is_supported_file`
- `_resolve_files()` (line 112): calls `_get_supported_files()`, loads bytes via `file_obj.bytes_content()`, returns `{name: (bytes, mime_type)}`
- `_filter_requested_files(self, all_files, params_dict)` (lines 143–184): looks for `"file"` or `"files"` key in `params_dict` (handles both single and list values); if found and non-empty, narrows `all_files` to matching names; at line 182, if `filtered_files` is empty (LLM passed no names, or names didn't match exactly), **falls back to returning `all_files` in full** — this is the **secondary root-cause locus**

**Jira tool (`src/codemie_tools/core/project_management/jira/tools.py`, lines 109–405):**

- `GenericJiraIssueTool(CodeMieTool, FileToolMixin)`: the only tool that calls `_filter_requested_files`; its `execute` method at lines 133–160 detects attachment operations via `_is_attachment_operation` (`"/attachments"` or `"/attachment"` in URL), calls `self._resolve_files()` → `_filter_requested_files(all_files, payload_params)` → `_handle_file_attachments`
- `_handle_file_attachments` (lines 233–268): iterates `files_content` dict and calls `self.jira.add_attachment_object(issue_key, file_content)` per file; no deduplication at the Jira API level
- `_is_attachment_operation` (lines 229–231): returns `True` if `"/attachments"` or `"/attachment"` in `relative_url`

**Callers of `build_unique_file_objects_list` and `build_unique_file_objects`:**

- `src/codemie/workflows/nodes/tool_node.py:205–229` — `_execute_regular_tool`: calls `build_unique_file_objects_list(file_names=self.file_names, conversation_id=self.execution_id)` — **does NOT pass `history_index`**, so the full conversation history is swept with no upper bound; assigns result to `tool.input_files` via duck-typing (`hasattr(tool, "input_files")`)
- `src/codemie/service/assistant_service.py:496–531` — `build_agent`: calls `build_unique_file_objects` directly (the dict-returning variant, not the list wrapper) with `request.file_names`, `request.conversation_id`, and `request.history_index`; this is the only caller that passes `history_index`, providing at least a temporal bound
- `src/codemie/service/tools/tool_execution_service.py` — calls `build_unique_file_objects_list`; exact `history_index` passing TBD
- `src/codemie/service/tools/toolkit_service.py` — calls `build_unique_file_objects_list`; exact `history_index` passing TBD

**Classes that extend `FileToolMixin` (12 confirmed):**
- `GenericJiraIssueTool` (`jira/tools.py`) — the only caller of `_filter_requested_files`
- `GenericConfluenceTool`
- `CreateWikiPageTool`, `AddWikiAttachmentTool`, `AddWikiCommentByIdTool`, `AddWikiCommentByPathTool` (AzureDevOps wiki)
- `BaseAzureDevOpsFileWorkItemTool` (AzureDevOps work item)
- `CreatePageAttachmentTool` (xWiki)
- `CSVTool`, `DocxTool`, `PDFTool`, `FileAnalysisTool`, `EmailAnalysisTool`

### Architecture and Layers Affected

- **Workflow/Orchestration** (`tool_node.py`): assembles `input_files` from conversation history and injects into tool instances before `execute()`; primary runtime path for tool file injection; missing `history_index` here is a direct contributor to the bug
- **Service** (`assistant_service.py`, `tool_execution_service.py`, `toolkit_service.py`): secondary callers of the file aggregation functions; `assistant_service.py` passes `history_index` (better); the other two need inspection
- **Core/Utils** (`src/codemie/core/utils.py`): conversation file aggregation logic — primary fix target
- **Tool/Agent** (`file_tool_mixin.py`, `jira/tools.py`): file filtering and attachment execution — secondary fix target

### Integration Points

- `Conversation` model is loaded from persistence inside `_collect_files_from_conversation`; `.history` list carries all messages; each message has `.role`, `.history_index`, and `.file_names` (encoded file URL strings)
- `_process_file_names_to_objects` decodes file URL strings into `FileObject` instances; used in both the current-turn path and the history-sweep path
- `tool.input_files` is set on the tool instance via duck-typing in `tool_node.py`; all 12 `FileToolMixin` subclasses expose this attribute
- `GenericJiraIssueTool._handle_file_attachments` calls `self.jira.add_attachment_object(issue_key, file_content)` per file in the dict; no deduplication at the Jira API level

### Patterns and Conventions

- **Mixin composition**: `FileToolMixin` is a pure Python mixin (no `__init__`); composed via multiple inheritance (`class GenericJiraIssueTool(CodeMieTool, FileToolMixin)`)
- **In-place dict mutation**: `_collect_files_from_conversation` mutates a passed-in dict rather than returning a new one; the pattern enables merging across multiple sources
- **Last-write-wins deduplication**: `_get_unique_messages_from_history` uses `(role, history_index)` key with last-write-wins to handle message editing; restricting to the latest turn removes this guard
- **Fallback-to-all behavior**: `_filter_requested_files` uses `filtered_files if filtered_files else all_files` at line 182 — intentional defensive fallback when LLM omits filenames, but unsafe when `all_files` spans multiple turns
- **Duck-typed injection**: `tool.input_files` is set only if the attribute exists; non-file tools safely ignore the field
- **Two-phase file resolution in Jira**: `_resolve_files()` filters by MIME type first, then `_filter_requested_files()` narrows to LLM-requested filenames — the assumption is that `_filter_requested_files` receives a clean, current-turn-only set

---

## 3. Documentation Findings

### Guides and Architecture Docs

No guides found — conventions derived from code exploration.

### Architectural Decisions

- The history accumulation in `_collect_files_from_conversation` appears to be a deliberate design decision to support "reference earlier files by context" use cases; no ADR or inline decision comment was found, but the `_filter_requested_files` fallback implies the original intent was that the LLM would narrow by filename
- The `_filter_requested_files` fallback at line 182 is explicit; no comment explains when this path is expected to trigger vs. when it represents a failure mode
- The `_get_unique_messages_from_history` last-write-wins strategy is designed for message editing: when a user edits a message and removes a file, the edited version wins; this is a valid design that scoping to "last turn only" would need to preserve

### Derived Conventions

- File handling is done at the tool instance level via `input_files` injection before `execute()` is called
- Filtering is the tool's own responsibility via `_filter_requested_files`, not the caller's
- Conversation history is the canonical source for files from prior turns; the current turn's `file_names` are passed separately and merged in `build_unique_file_objects`
- `history_index` is the intended scoping mechanism — callers that pass it (like `assistant_service.py`) get temporal bounds; callers that omit it (like `tool_node.py`) do not

### External Documentation Findings

Not applicable — this is a pure internal bug fix with no third-party API surface changes.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie_tools/core/project_management/jira/test_tools.py` — unit tests for `GenericJiraIssueTool`; covers general execution; no multi-turn file attachment scenarios confirmed
- `tests/codemie_tools/core/project_management/jira/test_image_attachments.py` — covers image attachment fetching and multimodal responses (GET, not POST/upload attachment path)
- `tests/codemie_tools/core/project_management/jira/test_jira_advanced_jql.py` — JQL/search path
- `tests/codemie_tools/core/project_management/jira/test_models.py` — model validation
- `tests/codemie_tools/file_analysis/test_csv_tool.py` — CSVTool (a `FileToolMixin` subclass); exercises `_resolve_files` indirectly
- `tests/codemie_tools/file_analysis/test_file_analysis.py` — general file analysis
- `tests/codemie_tools/file_analysis/pdf/test_pdf_toolkit.py` — PDFTool
- `tests/codemie/service/tools/test_toolkit_service_file_tools.py` — `add_file_tools` / `MarkdownCacheService` integration
- `tests/codemie/service/test_assistant_service_headers.py` — `build_agent` header propagation (not file accumulation behavior)

### Testing Framework and Patterns

- pytest (inferred from project conventions and test file naming)
- Subclass-level tests for `FileToolMixin` behavior (CSVTool, PDFTool) — no direct mixin-level tests
- No fixture or mock patterns for multi-turn conversation state were found for this domain

### Coverage Gaps

- **`FileToolMixin` class itself has no covering tests** — codegraph blast-radius explicitly reports `no covering tests found`; `_resolve_files`, `_filter_requested_files`, `_is_supported_file`, and `_get_supported_files` are exercised only indirectly through subclass tests
- **`build_unique_file_objects` / `build_unique_file_objects_list` have no covering tests** — codegraph blast-radius confirms `no covering tests found`
- **`_collect_files_from_conversation` / `_get_unique_messages_from_history` have no unit tests** — only implicitly exercised if an integration path exercises `build_unique_file_objects` with a `conversation_id`
- **`GenericJiraIssueTool._handle_file_attachments` (the upload/POST path) has no direct tests** — existing Jira tests cover image attachments on GET, not the upload code path
- **No multi-turn file attachment regression test exists** — the exact bug scenario (two sequential upload+attach turns) has never been exercised in the test suite; the acceptance criteria require this to be authored
- **`_filter_requested_files` fallback branch** (returning all files when requested name not found) — no test explicitly covers this path

---

## 5. Configuration and Environment

### Environment Variables

No environment variables specific to the file attachment or conversation history domain were identified.

### Configuration Files

No domain-specific configuration files affect file attachment behavior. `tool.input_files` is populated at runtime via `build_unique_file_objects_list`; the `Conversation` model is loaded from the database at invocation time.

### Feature Flags and Deployment Concerns

No feature flags govern this path. The fix is a pure behavioral change to two utility functions and requires no infrastructure changes.

---

## 6. Risk Indicators

- **No existing test coverage for `_collect_files_from_conversation`, `build_unique_file_objects`, or `FileToolMixin`** — any change to these functions is unguarded by the current test suite; new tests must be written as part of the fix
- **12 subclasses of `FileToolMixin`** — changing `_filter_requested_files` fallback logic affects all tools that inherit it (Confluence, AzureDevOps wiki/work-item, xWiki, CSV, PDF, Docx, FileAnalysis, EmailAnalysis); must verify none legitimately depend on "return all when no match"
- **4 callers of `build_unique_file_objects_list`** across service and workflow layers — `tool_node.py` omits `history_index` entirely; `assistant_service.py` passes it; `tool_execution_service.py` and `toolkit_service.py` are unconfirmed; all 4 need inspection before changing accumulation behavior
- **`_get_unique_messages_from_history` last-write-wins guard for edited messages** — this deduplication assumes access to the full history to identify the latest version of each `(role, history_index)` pair; scoping to the last turn only removes this guard and could reintroduce stale file references from earlier edited messages
- **`tool_node.py` does not pass `history_index`** to `build_unique_file_objects_list` — the full conversation is swept with no temporal bound; scoping the function to "last turn only" without fixing the caller would be brittle if future callers legitimately need `history_index`-bounded accumulation
- **`_filter_requested_files` is the only safety net for LLM name mismatches** — if the LLM specifies a filename that doesn't match exactly (encoding differences, extension casing), the fallback currently returns all files rather than erroring silently; removing or narrowing the fallback without a replacement strategy could cause tool silent failures (empty file sets)
- **No documentation or comments** explain the intended contract of `_collect_files_from_conversation` or when cross-turn accumulation is a feature vs. a bug — the fix must make this intent explicit
- **`GenericJiraIssueTool._handle_file_attachments` has no POST-path tests** — the upload code path is untested; any regression from the fix would go undetected without new tests

---

## 7. Summary for Complexity Assessment

The bug is precisely localized to two functions in two files, each with confirmed single callers. The primary fix is in `src/codemie/core/utils.py`: `_collect_files_from_conversation` (lines 607–628) sweeps the entire conversation history and accumulates files from all prior turns into a shared dict. The fix described in the ticket — scoping to the most recent user turn — is a small code change, likely 5–15 lines altered across `_collect_files_from_conversation` and its entry point `build_unique_file_objects`. A secondary fix in `src/codemie_tools/base/file_tool_mixin.py` (line 182) addresses the `_filter_requested_files` fallback returning all files when LLM params are absent; this change is similarly small but requires understanding safe-failure implications across 12 subclasses. The total direct change surface is 2 files, 2 functions, roughly 20–30 lines.

The risk surface is meaningfully broader than the code change size. `build_unique_file_objects_list` has 4 callers in different layers; `tool_node.py` omits `history_index` (the primary runtime path for tool file injection), while `assistant_service.py` passes it. Scoping history accumulation to the current turn affects all callers and could break legitimate multi-turn file reference use cases (e.g. "summarize all files I've uploaded today"). The three service-layer callers (`assistant_service.py`, `tool_execution_service.py`, `toolkit_service.py`) need manual inspection to confirm they would behave correctly with a scoped result. The `_get_unique_messages_from_history` last-write-wins edit-deduplication guard also relies on full-history access and must be preserved in any scoped implementation.

The test coverage gap is the most significant complexity driver. The entire `_collect_files_from_conversation` / `build_unique_file_objects` stack has zero test coverage, and the multi-turn attachment scenario has never been exercised in the test suite. The acceptance criteria explicitly require regression tests for sequential file attachment requests — this is additional scope beyond the code fix itself. The `_filter_requested_files` fallback and `_handle_file_attachments` upload path are also untested. Overall complexity is low-to-moderate for the code change but moderate for safe delivery: the fix is small and scoped, but requires a careful cross-caller audit and a meaningful test authoring effort to meet the stated acceptance criteria without introducing regressions in the 12 `FileToolMixin` subclass tools.
