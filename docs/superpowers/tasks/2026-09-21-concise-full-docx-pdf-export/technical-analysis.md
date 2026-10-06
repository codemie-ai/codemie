# Technical Research

**Task**: conversation export docx pdf
**Generated**: 2026-09-21
**Research path**: filesystem

---

## 1. Original Context

Implement backend support for EPMCDME-14774 (concise-vs-full DOCX/PDF conversation export). The codemie-ui frontend slice is already done (branch hide-tool-outputs-docx-pdf-export, sibling repo codemie-ui) and calls GET v1/conversations/{id}/export?export_format={docx|pdf}&include_tool_outputs={true|false}. Omitted or false means concise export (prompts + assistant responses only, no tool outputs, new default). true means today's existing full export (unchanged). JSON export is untouched, no param sent for it. Backend needs to honor that query param, filter tool-output content out of the concise variant, and preserve DOCX/PDF formatting (headings/lists/tables/links/code blocks) per acceptance criteria. Need to find: the existing export endpoint implementation, how DOCX/PDF generation works today, where tool-output content is represented in the conversation/message model, and how the full export currently includes tool outputs so we know what to filter for the concise path.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/rest_api/routers/conversation.py:820-861` — `export_conversation`, the `GET /v1/conversations/{conversation_id}/export` router handler. Takes `export_format: ConversationExportFormat = ConversationExportFormat.JSON` plus optional `page`/`per_page` `Query` params. No `include_tool_outputs` (or any tool-output) parameter exists on this endpoint today.
- `src/codemie/rest_api/routers/conversation.py:864-926` — `_get_streaming_response`, called by the handler above. For `json` it builds a raw dict (`assistants`, `history`, timestamps) and streams it unmodified — the full `Thought` list on every `GeneratedMessage` is serialized as-is via `jsonable_encoder`. For `pdf`/`docx` it constructs `MessageExporter(conversation=conversation, export_format=ExportFormat(export_format), assistant=assistant)` and streams `service.run()`.
- `src/codemie/rest_api/routers/conversation.py:534-575` — sibling endpoint `GET /v1/conversations/{conversation_id}/history/{history_index}/{message_index}/export` (`export_conversation_message`, per-message export). Out of scope per the ticket (frontend spec's non-goals explicitly exclude it) but shares `MessageExporter`.
- `src/codemie/service/conversation/message_exporter.py` — the whole DOCX/PDF/PPTX generation pipeline:
  - `ExportFormat` enum (PDF/DOCX/PPTX) with `content_type`.
  - `ContentParser` — turns raw markdown into `Node`s (currently a thin pass-through: `RawContent`, with code-block splitting for thought text).
  - `FormatProcessor` subclasses (`PDFProcessor`, `DOCXProcessor`, `PPTXProcessor`) + `ProcessorFactory` — per-format Pandoc arg building and structural adjustments (PPTX removes `PageBreak`, adds slide-break `HorizontalRule`s).
  - `DocumentBuilder` — builds the `pandoc_nodes.Document` tree: `build_conversation_document()` (full export, iterates `message_pairs`) and `build_single_message_document()` (per-message export). `_add_message_pair_to_document()` adds `User` heading + paragraph, then (if `ai_message.thoughts`) calls `_add_thoughts_to_document()`, then `CodeMie Assistant` heading + paragraph with `ai_message.message`.
  - `_add_thoughts_to_document()` (line 406) is exactly where tool-output content enters the full-conversation document today: `filtered_thoughts = [t for t in ai_message.thoughts if ExportUtils.should_include_thought(t)]` — called **without** the `export_full_thought` kwarg, so it defaults to `True` (include everything) unconditionally. Each surviving thought becomes a `Heading` (thought title) + optional `Input`/`Output` sub-headings + parsed content nodes.
  - `MessageExporter` — top-level orchestrator (`__init__`, `run()`, `filename`, `content_type`, `_export_full_conversation()`, `_filter_assistant_messages()`, `_export_document()`, `_process_markdown()` and format-specific LaTeX/SVG/Unicode escaping for PDF). No `include_tool_outputs`/`export_full_thought` parameter exists on this class; it always renders every thought.
- `src/codemie/service/conversation/export_utils.py:192-221` — `ExportUtils.should_include_thought(thought, export_full_thought: bool = True)`. **This is already the exact filtering primitive the ticket needs, but it is unwired.** When `export_full_thought=False` it keeps only thoughts whose `author_name` contains "thoughts" (case-insensitive, e.g. "CodeMie Thoughts") and drops everything else — including tool-call thoughts such as "Python Repl Code Interpreter" (`author_type=Tool`). It always drops thoughts with empty `message` or no `author_type` regardless of the flag. No caller in `message_exporter.py` passes `export_full_thought=False`; only unit tests exercise that branch (see Testing Landscape).
- `src/codemie/service/conversation/pandoc_nodes.py` — the Pandoc markdown node/serializer tree: `Document`, `Heading`, `Paragraph`, `CodeBlock`, `HorizontalRule`, `PageBreak`, `RawContent`, `SerializationContext`. There are no dedicated `List`/`Table`/`Link` node types — lists, tables, and links are expected to already be present as literal Pandoc-markdown syntax inside `Paragraph.content`/`RawContent.content` (i.e., inside `ai_message.message` and thought text), and Pandoc itself renders that markdown into DOCX/PDF structure at conversion time. Formatting preservation is therefore primarily a "don't mangle the underlying markdown when filtering nodes" concern, not a new serialization capability.
- `src/codemie/rest_api/models/conversation.py:1009-1013` — `ConversationExportFormat(StrEnum)`: `PDF`, `DOCX`, `JSON`.
- `src/codemie/rest_api/models/conversation.py:116-144` — `GeneratedMessage(ChatMessage)`: `message` (final text), `thoughts: Optional[List[Thought]]` (tool-call/agent-step trace — this is where "tool outputs" live for the assistant role), `assistant_id`, `history_index`, plus A2UI/workflow fields not relevant here.
- `src/codemie/chains/base.py:28-58` — `Thought` model: `id`, `parent_id`, `input_text`, `message`, `author_type` (`ThoughtAuthorType`: `Tool`/`Agent`/`System`), `author_name` (free text, e.g. "Python Repl Code Interpreter", "CodeMie Thoughts", "Research Agent"), `output_format`, `error`, `children`. There is no boolean/enum on `Thought` itself that cleanly means "this is a tool output" versus "this is chain-of-thought reasoning" — the existing filter in `ExportUtils.should_include_thought` uses a substring match on `author_name` ("thoughts" in the name) as its proxy for "not a tool step".

### Architecture and Layers Affected

- **API/router layer**: `src/codemie/rest_api/routers/conversation.py` — `export_conversation` needs a new query parameter and `_get_streaming_response` needs to thread it to `MessageExporter` for the `pdf`/`docx` branch (the `json` branch is explicitly unchanged per the ticket).
- **Service layer**: `src/codemie/service/conversation/message_exporter.py` — `MessageExporter.__init__`/`_export_full_conversation`, `DocumentBuilder.build_conversation_document`/`_add_message_pair_to_document`/`_add_thoughts_to_document` need to carry the concise/full flag down to the `ExportUtils.should_include_thought` call site.
- **Utility layer**: `src/codemie/service/conversation/export_utils.py` — `ExportUtils.should_include_thought` already implements the filtering predicate; it is exposed but not consumed with `export_full_thought=False` anywhere in production code today.
- Single-message export (`export_conversation_message` / `export_single_message` / `build_single_message_document`) is a separate code path that also calls `_add_thoughts_to_document` but is out of scope (frontend explicitly does not touch it, and the ticket's URL is the full-conversation `/export` endpoint only).

### Integration Points

- `pypandoc` (wraps the `pandoc` CLI) — actual markdown → DOCX/PDF/PPTX conversion; invoked via `pypandoc.convert_text(...)` in `MessageExporter._export_document`/`export_single_message`.
- `markitdown` (`MarkItDown`) — imported in `ContentParser.__init__` but not currently used beyond parsing raw content into a single `RawContent` node; not deeply exercised by the existing pipeline.
- `docx` (python-docx) — used only in tests (`tests/codemie/service/conversation/test_message_exporter.py`) to open the generated DOCX and assert on runs; not used by production `message_exporter.py` code (it shells out to pandoc, not python-docx, for generation).
- `cairosvg` (optional) — used for inline SVG→PNG conversion during PDF export (`_convert_svg_to_png`), gracefully degrades to `[Image: alt]` placeholders if absent.
- `MermaidService` / `FileService` (via `ExportUtils`) — mermaid diagram-to-PNG rendering and sandbox-image-to-base64 embedding, applied to thought content and message markdown before Pandoc conversion. Both are exercised regardless of concise/full mode for whichever content survives filtering.
- No external HTTP/cloud SDK client is specific to export; conversion is entirely local (pandoc/latex binaries assumed present in the runtime image).

### Patterns and Conventions

- Query-parameter based endpoint variants follow the existing `page`/`per_page`/`isFinished` pattern in this same router: `Optional[bool] = Query(None, alias=...)` or plain typed defaults directly in the function signature (see `get_conversation_list`, `remove_folder`'s `remove_conversations: bool = False`). A new boolean flag would fit this convention directly as a function parameter with a default, mirroring `remove_conversations: bool = False` on `remove_folder` (`conversation.py:710`).
- `FormatProcessor` + `ProcessorFactory` is a small factory pattern already in place for format-specific behavior — the concise/full distinction is cross-cutting (applies identically to PDF and DOCX) and does not need a new processor subclass; it belongs in `DocumentBuilder`/`MessageExporter` construction, not in `ProcessorFactory`.
- `ExportUtils.should_include_thought(thought, export_full_thought)` is already written as a toggle-driven predicate — the natural seam is threading a single boolean (e.g. `export_full_thought` or an `include_tool_outputs`-named flag) from the router, through `MessageExporter.__init__`, into `DocumentBuilder`, down to this existing call.
- Errors from the export flow are raised as `ExtendedHTTPException` (see `export_conversation_message`'s `except Exception` wrapping into a 422, and `_get_streaming_response`'s 501 for unsupported formats) — any new validation error should follow the same pattern.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/api/rest-api-patterns.md` — router registration, error-response, and auth-dependency conventions (generic, applies to this endpoint but has no export-specific content).
- `.ai-run/guides/testing/testing-api-patterns.md` — router tests should exercise the router/client boundary for request validation, auth, and response shape; negative cases for validation/authorization changes are expected.
- No guide in `.ai-run/guides/` specifically covers conversation export, DOCX/PDF generation, or Pandoc usage.
- `docs/` (top-level, excluding `docs/superpowers/`) has no design doc or ADR for the export feature; the only hits for "export" are unrelated (`activity-events-ui-plan.md`, an analytics JSON dump).

### Architectural Decisions

- No ADRs found for this feature area. The one clear "decision already made in code but not yet activated" is `ExportUtils.should_include_thought`'s `export_full_thought` parameter and its author-name-substring heuristic for distinguishing "thoughts" (kept when concise) from tool/agent steps (dropped when concise) — this predates the current ticket (it has dedicated tests already) but was never wired to a caller that passes `False`.
- Inline comments of note: `message_exporter.py:570-571` documents that `_build_extra_args` is for single-message export only, while full-conversation export uses `PDFProcessor.get_pandoc_args()` — a structural asymmetry between the two export paths worth remembering if the concise flag needs to reach both.

### Derived Conventions

- Boolean query flags on this router default in the function signature rather than via `Query(...)` wrapping when no extra FastAPI validation (ge/le/alias) is needed (e.g. `remove_conversations: bool = False` on `remove_folder`); `include_tool_outputs`/equivalent would likely follow this simpler form unless an alias is required to match the exact wire name the frontend already sends (`include_tool_outputs`, snake_case, so no alias needed).
- Filtering predicates live in `ExportUtils` as static methods (`should_include_thought`, `replace_sandbox_images`, `replace_mermaid_with_images`) rather than inline in `DocumentBuilder` — consistent with keeping `DocumentBuilder` focused on tree assembly and pushing decision logic into the utils class.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/conversation/test_message_exporter.py` — extensive unit coverage of `ExportFormat`, `ContentParser`, the three `FormatProcessor`s, `DocumentBuilder` (including `test_build_single_message_document_with_thoughts`, `test_build_conversation_document`), `MessageExporter` for both single-message and full-conversation modes, markdown processing, and — notably — `test_tool_thoughts_excluded_when_export_full_thought_false` / `test_tool_thoughts_included_when_export_full_thought_true` (lines 749-778), which test `ExportUtils.should_include_thought` directly with both flag values but do **not** exercise `MessageExporter`/`DocumentBuilder`/the router with the flag threaded through end-to-end.
- `tests/codemie/rest_api/routers/test_conversation_pagination.py` — covers the `/conversations/{id}/export` endpoint but only for `export_format=json` (`test_export_json_without_pagination`, `test_export_json_without_pagination_no_dates`, `test_export_json_with_pagination`, `test_export_json_not_found`). No test in this file (or `test_conversation.py`) exercises the `pdf`/`docx` branch of `export_conversation`/`_get_streaming_response` at the router level.
- `tests/codemie/rest_api/routers/test_conversation.py` — covers the sibling per-message export endpoint (`test_export_conversation_message`, `_not_found`, `_permission_err`) via `CONVETSATION_MSG_EXPORT_PATH = "/v1/conversations/123/history/0/0/export?export_format=pdf"`, not the full-conversation endpoint.

### Testing Framework and Patterns

- `pytest` with `unittest.mock.patch`/`MagicMock`/`AsyncMock`, `fastapi.testclient.TestClient`, and `app.dependency_overrides[conversation_router.authenticate] = lambda: user` for auth bypass (both test files above use this exact fixture pattern: `user`, `mock_litellm_startup`, `client`, `override_dependency`).
- `MessageExporter` unit tests use `MagicMock(spec=Conversation)`/`MagicMock(spec=Assistant)` fixtures (`mock_assistant`, `mock_conversation_simple`, `mock_conversation_multi_messages`) with real `GeneratedMessage`/`Thought` Pydantic instances attached, and assert against the serialized Pandoc markdown tree (`doc.serialize()`) rather than the final DOCX/PDF bytes in most cases; a few (`TestDocxExport`) open the generated DOCX with `python-docx` to assert on paragraph runs.

### Coverage Gaps

- No router-level test exercises `pdf`/`docx` export at all (with or without the new parameter) — only `json` is covered at that layer today.
- No test threads a tool-output-filtering flag through `MessageExporter`/`DocumentBuilder` end-to-end; the only existing coverage of the filtering predicate is at the `ExportUtils.should_include_thought` unit level, disconnected from the export pipeline that would need to call it with `False`.
- No test asserts DOCX/PDF structural preservation (headings/lists/tables/links/code blocks surviving through Pandoc) for either variant — existing `TestDocxExport`/`TestFormatProcessors` tests check pandoc-arg construction and markdown-tree shape, not rendered-document structural assertions beyond simple text presence.

---

## 5. Configuration and Environment

### Environment Variables

- None found specific to conversation export. `message_exporter.py` and `export_utils.py` have no `os.environ`/`getenv`/config() reads.

### Configuration Files

- No config file (settings.py-equivalent, `.env.example` entry, etc.) governs export behavior; `pandoc`/`pdflatex`/`cairosvg` availability is an environment/image concern, not a repo-tracked config value.

### Feature Flags and Deployment Concerns

- No feature flag or runtime toggle exists for this domain; the ticket's mechanism is a plain request query parameter, not a flag.
- No Dockerfile/CI reference to `export_format`/`include_tool_outputs` was found.

---

## 6. Risk Indicators

- The one existing filtering primitive (`ExportUtils.should_include_thought`'s `export_full_thought` flag) uses an `author_name`-substring heuristic ("thoughts" in the name) as its concise/full boundary. Speculative: whether that heuristic actually matches "no tool outputs" as the ticket defines it — versus dropping all `thoughts` entirely for the concise variant, including any chain-of-thought reasoning steps — is a design question for spec/plan, since `should_include_thought(False)` still keeps "CodeMie Thoughts"-named entries rather than reducing to "prompts + assistant responses only."
- Two disjoint export code paths call `_add_thoughts_to_document`/`should_include_thought`: full-conversation (`build_conversation_document` → `_add_message_pair_to_document`) and single-message (`build_single_message_document`). The ticket's endpoint only needs the full-conversation path, but both currently share the same unconditional call, so the wiring must be scoped carefully to avoid accidentally changing single-message export behavior (which is explicitly out of scope per the frontend spec).
- Zero router-level test coverage exists today for the `pdf`/`docx` branch of `/v1/conversations/{id}/export` — any regression in that branch (new or pre-existing) would not be caught by the current suite.
- `MessageExporter.__init__`/`DocumentBuilder` construction has no existing parameter for this flag; adding one touches a constructor used by both the full-conversation and per-message router handlers (only one of which needs the new behavior), and by every existing unit test fixture that constructs these classes directly.
- Formatting preservation (headings/lists/tables/links/code blocks) is Pandoc's responsibility once markdown reaches `pypandoc.convert_text`; there is no dedicated list/table/link node in `pandoc_nodes.py`, so "preserving formatting" for the concise variant is really "don't corrupt the markdown of the nodes that remain" rather than a new rendering capability — low risk for `Paragraph`/`Heading`/`CodeBlock` nodes, since removing thought nodes doesn't touch `ai_message.message` content itself.
- `ContentParser.parse_message_content` is presently a pass-through (`[RawContent(content)]`) and `markitdown` is imported but effectively unused — not a risk for this ticket, but a sign the "enhanced formatting" mentioned in the module docstring isn't yet load-bearing for the final assistant message content either variant relies on.

---

## 7. Summary for Complexity Assessment

The touched surface is small and concentrated in two files: `src/codemie/rest_api/routers/conversation.py` (the `export_conversation` handler and `_get_streaming_response`, adding one query parameter and threading it only through the `pdf`/`docx` branch) and `src/codemie/service/conversation/message_exporter.py` (`MessageExporter.__init__`, `_export_full_conversation`, `DocumentBuilder.build_conversation_document`/`_add_message_pair_to_document`/`_add_thoughts_to_document`), which must pass a boolean down to an already-existing predicate in `src/codemie/service/conversation/export_utils.py::ExportUtils.should_include_thought`. No new architectural layer, model field, migration, or dependency is required — the "hard part" (filtering thoughts out of an export) is already implemented as a unit-tested utility function that simply has no production caller passing `export_full_thought=False`. The `json` export path is untouched by design (confirmed unaffected by the query param dispatch already present).

Technical novelty is low: this is wiring an existing toggle through an existing pipeline, not building new document-generation capability. Pandoc handles headings/lists/tables/links/code-block markdown rendering already, and no new Pandoc node types are implied by "concise" mode — filtering removes whole thought nodes rather than altering the serialization of surviving content. The main open question is definitional, not architectural: whether the existing `author_name`-substring heuristic for "thoughts" already matches the ticket's "prompts + assistant responses only, no tool outputs" bar, or whether concise mode should drop thoughts entirely — this is a spec-level decision, not a research finding.

Test-coverage posture is the largest risk factor: the full-conversation `pdf`/`docx` router branch has zero existing test coverage (only `json` is tested at the router level), and the filtering predicate is tested in isolation but never through `MessageExporter`/`DocumentBuilder`/the router end-to-end. Any implementation will need new router-level tests for both `include_tool_outputs=true` and `false`/omitted on `pdf` and `docx`, plus an end-to-end assertion that filtered thoughts don't appear in the generated document tree — none of which have a template to copy beyond the existing `json`-only pagination tests and the isolated `ExportUtils` unit tests.

---

## 8. External References

- `/Users/Anton_Atrashkou/codemie-dev/codemie-ui` (git branch `hide-tool-outputs-docx-pdf-export`, resolved and read) — the frontend slice referenced by the task as "already done":
  - `src/store/chats.ts:466-479` (`exportChat`): builds `` `v1/conversations/${chat.id}/export?export_format=${format}${toolOutputsParam}` `` where `toolOutputsParam` is `'&include_tool_outputs=true'` only when `includeToolOutputs` is truthy **and** `format` is `'docx'` or `'pdf'` — confirming the exact query-string contract the backend must honor (param omitted entirely, never sent as `false`, and never sent for `json`).
  - `docs/superpowers/tasks/2026-09-21-hide-tool-outputs-docx-pdf-export/spec.md` (resolved and read) — the frontend's own spec for this ticket, which states explicitly: "The backend is assumed to accept an additional boolean query parameter on the existing export endpoints, alongside `export_format`: `GET v1/conversations/{id}/export?export_format={docx|pdf|json}&include_tool_outputs={true|false}`... Omitted or `false` → concise variant... `true` → full variant (current/legacy behavior)... For `export_format=json`, the parameter is not sent." It also flags as an open risk: "The backend's actual parameter name/shape is unconfirmed; if it lands on a different contract... only the URL-building line in `chatsStore.exportChat` needs to change" — i.e., the frontend has NOT hard-committed to `include_tool_outputs` being the final backend contract name, only to what it currently sends.
  - `git log` on that branch (`4dcc83ca2`, `2a61ac9f5`, `4eefab718`) confirms the frontend change is merged into that branch's history and adds only the `include_tool_outputs` query param, two new menu items ("Export to DOCX/PDF (with tool outputs)"), and loading/duplicate-click UX — no backend code.
