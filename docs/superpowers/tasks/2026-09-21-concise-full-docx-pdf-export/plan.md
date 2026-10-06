# Concise vs. Full DOCX/PDF Conversation Export (EPMCDME-14774) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire a new `include_tool_outputs` query parameter through the full-conversation DOCX/PDF export pipeline so omitted/`false` produces a concise document (no thought/tool-output content, empty pairs skipped) and `true` preserves today's full export unchanged.

**Architecture:** Thread a single boolean flag from the router (`export_conversation`) through `MessageExporter.__init__` into `DocumentBuilder.build_conversation_document` → `_add_message_pair_to_document`, which gates the existing `_add_thoughts_to_document` call and the empty-pair-skip rule. No new node types, no changes to `ExportUtils.should_include_thought`, `build_single_message_document`, or the `json` export path. The shared constructor default stays `True` (today's full behavior) so the per-message export path and every existing direct-construction test fixture are unaffected.

**Tech Stack:** Python, FastAPI, pytest, unittest.mock, fastapi.testclient.TestClient, pypandoc/Pandoc (unaffected by this change).

**Spec:** `docs/superpowers/tasks/2026-09-21-concise-full-docx-pdf-export/spec.md`

**Commit per task using the repository's existing convention.**

## Revision Note (post-review)

A human reviewer sent this plan back with three findings, all confirmed against `message_exporter.py`, `conversation.py`, and the existing test files before revising:

1. Task 3's original tests patched `MessageExporter` entirely and asserted only on `call_args.kwargs["include_tool_outputs"]` — no document was ever generated or inspected, so the AC's "closing the zero-coverage gap" and the empty-pair-skip case went unmet. **Fixed:** added non-mocked end-to-end router tests below that run the real `MessageExporter`/`DocumentBuilder` pipeline (stubbing only `pypandoc.convert_text`'s final external call) and assert on the actual serialized content.
2. Task 2's original test never called `_export_full_conversation` — it built its own `DocumentBuilder` and passed the kwarg itself, so it wouldn't fail if the forwarding line were deleted. **Fixed:** replaced with a test that patches `DocumentBuilder` and asserts the kwarg `_export_full_conversation` actually passes it.
3. No test proved a pair with an empty `ai_message.message` is *not* skipped when `include_tool_outputs=True` (only the concise-mode skip was covered) — the highest-value "full variant unchanged" regression was unverified. **Fixed:** added that case to Task 1 (unit level) and it is also exercised end-to-end by Task 3's new full-mode test.

## Global Constraints

- Query parameter name stays `include_tool_outputs` — do not rename it, do not add `Query`/alias/enum validation.
- `ExportUtils.should_include_thought` and its `export_full_thought` heuristic are not modified, removed, or repurposed — they remain unused in production, exactly as today.
- `build_single_message_document` and the per-message export endpoint (`.../history/{history_index}/{message_index}/export`) are not changed and must retain today's unconditional-full behavior.
- The `json` export format/serialization path is not changed; it must not error if `include_tool_outputs` is present on the query string.
- No new Pandoc node types (`List`/`Table`/`Link`) — formatting preservation relies entirely on Pandoc's existing markdown rendering of unmodified surviving content.
- The pre-existing PDF code-block rendering defect (`--listings`/`fvextra` mismatch) is out of scope — do not touch code-block serialization.
- No query-layer or DB-read-layer filtering for large-payload export failures — out of scope.
- `MessageExporter.__init__`'s new parameter must default to `True` so the per-message export path and any direct construction that omits it keep today's full behavior.

---

### Task 1: Gate thought rendering and empty-pair-skip in `DocumentBuilder`

**Files:**
- Modify: `src/codemie/service/conversation/message_exporter.py:350-404` (`build_conversation_document`, `_add_message_pair_to_document`)
- Test: `tests/codemie/service/conversation/test_message_exporter.py` (add cases to `TestDocumentBuilder`)

**Interfaces:**
- Consumes: existing `Document`, `Heading`, `HeadingLevel`, `Paragraph`, `HorizontalRule` node classes; existing `_add_thoughts_to_document(self, doc, ai_message)` (unchanged, still called with no `export_full_thought` override).
- Produces: `DocumentBuilder.build_conversation_document(self, conversation, assistant, message_pairs, include_tool_outputs: bool = True) -> Document` and `DocumentBuilder._add_message_pair_to_document(self, doc, user_message, ai_message, include_tool_outputs: bool = True) -> None` — later tasks (Task 2) call `build_conversation_document` passing this kwarg explicitly.

**Test-first: yes — `test_add_message_pair_concise_mode_drops_thoughts`, `test_add_message_pair_full_mode_keeps_thoughts`, `test_add_message_pair_concise_mode_skips_empty_pair`, `test_add_message_pair_full_mode_does_not_skip_empty_pair` all fail with `TypeError: unexpected keyword argument 'include_tool_outputs'` before Step 3.**

- [ ] **Step 1: Write the failing tests**

Add to `TestDocumentBuilder` in `tests/codemie/service/conversation/test_message_exporter.py`:

```python
def test_add_message_pair_concise_mode_drops_thoughts(self, mock_conversation_multi_messages, mock_assistant):
    """Concise mode (include_tool_outputs=False) must not add any thought-derived nodes."""
    builder = DocumentBuilder(export_format=ExportFormat.DOCX)
    user_msg = mock_conversation_multi_messages.history[0]
    ai_msg = mock_conversation_multi_messages.history[1]  # has thoughts=[Thought(author_name="Agent 1", ...)]

    doc = builder.build_conversation_document(
        conversation=mock_conversation_multi_messages,
        assistant=mock_assistant,
        message_pairs=[(user_msg, ai_msg)],
        include_tool_outputs=False,
    )

    headings = [child.text for child in doc.children if isinstance(child, Heading)]
    assert "Agent 1" not in headings
    assert "Input" not in headings
    assert "Output" not in headings
    assert "User" in headings
    assert "CodeMie Assistant" in headings

def test_add_message_pair_full_mode_keeps_thoughts(self, mock_conversation_multi_messages, mock_assistant):
    """include_tool_outputs=True (and the default) must render thoughts exactly as today."""
    builder = DocumentBuilder(export_format=ExportFormat.DOCX)
    user_msg = mock_conversation_multi_messages.history[0]
    ai_msg = mock_conversation_multi_messages.history[1]

    doc = builder.build_conversation_document(
        conversation=mock_conversation_multi_messages,
        assistant=mock_assistant,
        message_pairs=[(user_msg, ai_msg)],
        include_tool_outputs=True,
    )

    headings = [child.text for child in doc.children if isinstance(child, Heading)]
    assert "Agent 1" in headings

def test_add_message_pair_concise_mode_skips_empty_pair(self, mock_assistant):
    """A pair whose final message is empty and only had thought content is skipped entirely."""
    conversation = MagicMock(spec=Conversation)
    conversation.id = "conv-empty"
    conversation.conversation_name = "Empty AI Message"
    user_msg = GeneratedMessage(role=ChatRole.USER, message="Do the thing", history_index=0)
    ai_msg = GeneratedMessage(
        role=ChatRole.ASSISTANT,
        message="",
        history_index=0,
        thoughts=[
            Thought(id="t1", message="ran a tool", author_name="Tool", author_type=ThoughtAuthorType.Tool)
        ],
    )
    builder = DocumentBuilder(export_format=ExportFormat.DOCX)

    doc = builder.build_conversation_document(
        conversation=conversation,
        assistant=mock_assistant,
        message_pairs=[(user_msg, ai_msg)],
        include_tool_outputs=False,
    )

    headings = [child.text for child in doc.children if isinstance(child, Heading)]
    # Only the conversation title heading should remain; no User/CodeMie Assistant heading for the skipped pair.
    assert "User" not in headings
    assert "CodeMie Assistant" not in headings

def test_add_message_pair_full_mode_does_not_skip_empty_pair(self, mock_assistant):
    """Regression: an empty-message pair must NOT be skipped when include_tool_outputs=True —
    the skip rule is gated on concise mode only, so today's full-export behavior (always render
    the pair) must be unchanged."""
    conversation = MagicMock(spec=Conversation)
    conversation.id = "conv-empty-full"
    conversation.conversation_name = "Empty AI Message Full Mode"
    user_msg = GeneratedMessage(role=ChatRole.USER, message="Do the thing", history_index=0)
    ai_msg = GeneratedMessage(
        role=ChatRole.ASSISTANT,
        message="",
        history_index=0,
        thoughts=[
            Thought(id="t1", message="ran a tool", author_name="Tool", author_type=ThoughtAuthorType.Tool)
        ],
    )
    builder = DocumentBuilder(export_format=ExportFormat.DOCX)

    doc = builder.build_conversation_document(
        conversation=conversation,
        assistant=mock_assistant,
        message_pairs=[(user_msg, ai_msg)],
        include_tool_outputs=True,
    )

    headings = [child.text for child in doc.children if isinstance(child, Heading)]
    assert "User" in headings
    assert "CodeMie Assistant" in headings
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/conversation/test_message_exporter.py -k "concise_mode or full_mode_keeps_thoughts or full_mode_does_not_skip_empty_pair" -v`
Expected: FAIL — `build_conversation_document()` raises `TypeError: unexpected keyword argument 'include_tool_outputs'` (parameter doesn't exist yet).

- [ ] **Step 3: Implement the gating in `DocumentBuilder`**

In `src/codemie/service/conversation/message_exporter.py`:

- `build_conversation_document` (`:350-377`): add `include_tool_outputs: bool = True` as the last parameter; forward it to `_add_message_pair_to_document` in the loop (`:374-375`).
- `_add_message_pair_to_document` (`:379-404`): add `include_tool_outputs: bool = True` as the last parameter. At the top of the method, when `not include_tool_outputs and not (ai_message.message and ai_message.message.strip())`, `return` immediately (skip the pair entirely — no `HorizontalRule`, no headings, no bodies). Otherwise keep the existing `HorizontalRule` + `User` heading/body unchanged, and change the thoughts guard from `if ai_message.thoughts:` to `if include_tool_outputs and ai_message.thoughts:` so `_add_thoughts_to_document` (unchanged, still called with no `export_full_thought` override) is skipped entirely in concise mode. Keep the trailing `CodeMie Assistant` heading/body unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/service/conversation/test_message_exporter.py -v`
Expected: PASS (all `TestDocumentBuilder` tests, including the four new ones and the pre-existing `test_build_conversation_document`/`test_build_single_message_document_with_thoughts`, which call `build_conversation_document`/`build_single_message_document` without the new kwarg and must still pass unchanged since the default is `True`).

- [ ] **Step 5: Commit**

---

### Task 2: Thread `include_tool_outputs` through `MessageExporter`

**Files:**
- Modify: `src/codemie/service/conversation/message_exporter.py:490-518` (`MessageExporter.__init__`), `:643-665` (`_export_full_conversation`)
- Test: `tests/codemie/service/conversation/test_message_exporter.py` (add cases near the existing `TestMessageExporter`/full-conversation tests)

**Interfaces:**
- Consumes: `DocumentBuilder.build_conversation_document(..., include_tool_outputs: bool = True)` from Task 1.
- Produces: `MessageExporter(conversation, export_format, history_index=None, message_index=None, assistant=None, include_tool_outputs: bool = True)` — Task 3's router passes this kwarg explicitly on the full-conversation path.

**Test-first: yes — `test_export_full_conversation_forwards_include_tool_outputs` and `test_message_exporter_default_include_tool_outputs_is_true` both fail with `TypeError: __init__() got an unexpected keyword argument 'include_tool_outputs'` before Step 3.**

- [ ] **Step 1: Write the failing test**

```python
def test_export_full_conversation_forwards_include_tool_outputs(mock_conversation_multi_messages, mock_assistant):
    """_export_full_conversation must forward self.include_tool_outputs into
    DocumentBuilder.build_conversation_document. Patches DocumentBuilder itself and calls
    _export_full_conversation() directly (not a manually-built DocumentBuilder), so deleting the
    forwarding line in _export_full_conversation makes this test fail."""
    exporter = MessageExporter(
        conversation=mock_conversation_multi_messages,
        export_format=ExportFormat.DOCX,
        assistant=mock_assistant,
        include_tool_outputs=False,
    )

    with patch("codemie.service.conversation.message_exporter.DocumentBuilder") as mock_builder_cls:
        mock_builder = MagicMock()
        mock_builder.build_conversation_document.return_value = MagicMock()
        mock_builder_cls.return_value = mock_builder
        with patch.object(exporter, "_export_document", return_value=iter([b"stub"])):
            list(exporter._export_full_conversation())

    assert mock_builder.build_conversation_document.call_args.kwargs["include_tool_outputs"] is False


def test_message_exporter_default_include_tool_outputs_is_true():
    """Regression guard: omitting include_tool_outputs must preserve today's full-export default,
    since the per-message export path and other callers rely on this default."""
    exporter = MessageExporter(
        conversation=MagicMock(spec=Conversation),
        export_format=ExportFormat.DOCX,
        history_index=0,
        message_index=0,
    )
    assert exporter.include_tool_outputs is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/conversation/test_message_exporter.py -k "forwards_include_tool_outputs or default_include_tool_outputs" -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'include_tool_outputs'`.

- [ ] **Step 3: Implement the parameter and threading**

In `src/codemie/service/conversation/message_exporter.py`:

- `MessageExporter.__init__` (`:490-518`): add `include_tool_outputs: bool = True` as the last parameter; store `self.include_tool_outputs = include_tool_outputs`.
- `_export_full_conversation` (`:643-665`): pass `include_tool_outputs=self.include_tool_outputs` into the `builder.build_conversation_document(...)` call (`:660-662`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/service/conversation/test_message_exporter.py -v`
Expected: PASS (all `MessageExporter`/`DocumentBuilder` tests, including every pre-existing test that constructs `MessageExporter(...)` without the new kwarg — confirming the `True` default doesn't regress single-message export or other direct-construction call sites).

- [ ] **Step 5: Commit**

---

### Task 3: Wire the router query parameter for the pdf/docx export branch

**Files:**
- Modify: `src/codemie/rest_api/routers/conversation.py:820-926` (`export_conversation`, `_get_streaming_response`)
- Test: `tests/codemie/rest_api/routers/test_conversation_pagination.py` (add cases near the existing `test_export_json_*` tests)

**Interfaces:**
- Consumes: `MessageExporter(..., include_tool_outputs: bool = True)` from Task 2.
- Produces: `GET /v1/conversations/{conversation_id}/export?export_format={pdf|docx}&include_tool_outputs={true|false}` — the wire contract the codemie-ui frontend (already merged) depends on.

**Test-first: yes — all six tests below fail before Step 3: the `call_args.kwargs`/content-assertion tests raise `KeyError`/`AssertionError` because `export_conversation` has no `include_tool_outputs` parameter to forward, so `MessageExporter` is never constructed with it and the real pipeline never gates thought content.**

- [ ] **Step 1: Write the failing tests**

Add to `tests/codemie/rest_api/routers/test_conversation_pagination.py` (same fixtures/patch pattern as the existing `test_export_json_*` tests — `mock_litellm_startup`, `client`, `override_dependency`, patching `Conversation.find_by_id`, `Ability.can`, `Assistant.get_by_ids`):

```python
def _conversation_with_thought_and_empty_pair():
    return Conversation(
        id="conv-123",
        conversation_id="conv-123",
        user_id="user-123",
        history=[
            GeneratedMessage(role="User", message="Explain X", history_index=0),
            GeneratedMessage(
                role="Assistant",
                message="Here is the explanation.",
                history_index=0,
                thoughts=[
                    Thought(id="t1", message="ran search tool", author_name="Search Tool", author_type="Tool")
                ],
            ),
            GeneratedMessage(role="User", message="Run a tool silently", history_index=1),
            GeneratedMessage(
                role="Assistant",
                message="",
                history_index=1,
                thoughts=[
                    Thought(id="t2", message="tool output only", author_name="Tool", author_type="Tool")
                ],
            ),
        ],
    )


def _write_source_as_output(*, source, to, outputfile, **_kwargs):
    """Stand-in for pypandoc.convert_text: writes the serialized markdown Pandoc would have
    received directly to the output file, so tests can assert on the real DocumentBuilder/
    MessageExporter pipeline output without depending on the pandoc/pdflatex binaries."""
    with open(outputfile, "w", encoding="utf-8") as f:
        f.write(source)


# --- Wiring checks (parameter reaches MessageExporter with the right value) ---

@pytest.mark.parametrize("export_format", ["pdf", "docx"])
@pytest.mark.parametrize("query_suffix", ["", "&include_tool_outputs=false"])
@patch("codemie.rest_api.routers.conversation.Assistant.get_by_ids", new_callable=MagicMock, return_value=[])
@patch("codemie.rest_api.routers.conversation.Ability.can", new_callable=MagicMock, return_value=True)
@patch("codemie.rest_api.routers.conversation.Conversation.find_by_id", new_callable=MagicMock)
@patch("codemie.rest_api.routers.conversation.MessageExporter")
def test_export_concise_mode_omits_thoughts(
    mock_exporter_cls, mock_find, _mock_can, _mock_assistants, client, export_format, query_suffix
):
    """Omitted or explicit include_tool_outputs=false must construct MessageExporter with
    include_tool_outputs=False for both pdf and docx."""
    mock_find.return_value = _conversation_with_thought_and_empty_pair()
    mock_instance = MagicMock()
    mock_instance.run.return_value = iter([b"bytes"])
    mock_instance.content_type = "application/octet-stream"
    mock_exporter_cls.return_value = mock_instance

    response = client.get(f"/v1/conversations/conv-123/export?export_format={export_format}{query_suffix}")

    assert response.status_code == 200
    mock_exporter_cls.assert_called_once()
    assert mock_exporter_cls.call_args.kwargs["include_tool_outputs"] is False


@pytest.mark.parametrize("export_format", ["pdf", "docx"])
@patch("codemie.rest_api.routers.conversation.Assistant.get_by_ids", new_callable=MagicMock, return_value=[])
@patch("codemie.rest_api.routers.conversation.Ability.can", new_callable=MagicMock, return_value=True)
@patch("codemie.rest_api.routers.conversation.Conversation.find_by_id", new_callable=MagicMock)
@patch("codemie.rest_api.routers.conversation.MessageExporter")
def test_export_full_mode_keeps_thoughts(
    mock_exporter_cls, mock_find, _mock_can, _mock_assistants, client, export_format
):
    """include_tool_outputs=true must construct MessageExporter with include_tool_outputs=True."""
    mock_find.return_value = _conversation_with_thought_and_empty_pair()
    mock_instance = MagicMock()
    mock_instance.run.return_value = iter([b"bytes"])
    mock_instance.content_type = "application/octet-stream"
    mock_exporter_cls.return_value = mock_instance

    response = client.get(f"/v1/conversations/conv-123/export?export_format={export_format}&include_tool_outputs=true")

    assert response.status_code == 200
    assert mock_exporter_cls.call_args.kwargs["include_tool_outputs"] is True


@patch("codemie.rest_api.routers.conversation.Assistant.get_by_ids", new_callable=MagicMock, return_value=[])
@patch("codemie.rest_api.routers.conversation.Ability.can", new_callable=MagicMock, return_value=True)
@patch("codemie.rest_api.routers.conversation.Conversation.find_by_id", new_callable=MagicMock)
def test_export_json_unaffected_by_include_tool_outputs_param(mock_find, _mock_can, _mock_assistants, client):
    """json export must not error and must ignore include_tool_outputs when the frontend sends it anyway."""
    mock_find.return_value = _conversation_with_thought_and_empty_pair()

    response = client.get("/v1/conversations/conv-123/export?export_format=json&include_tool_outputs=true")

    assert response.status_code == 200


# --- Non-mocked, real-pipeline checks (closes the zero-coverage gap the AC calls out) ---
# MessageExporter/DocumentBuilder run for real; only pypandoc.convert_text's external binary
# call is stubbed, so assertions are against the actual serialized document content.

@pytest.mark.parametrize("export_format", ["pdf", "docx"])
@patch("codemie.service.conversation.message_exporter.pypandoc.convert_text", side_effect=_write_source_as_output)
@patch("codemie.rest_api.routers.conversation.Assistant.get_by_ids", new_callable=MagicMock, return_value=[])
@patch("codemie.rest_api.routers.conversation.Ability.can", new_callable=MagicMock, return_value=True)
@patch("codemie.rest_api.routers.conversation.Conversation.find_by_id", new_callable=MagicMock)
def test_export_concise_mode_omits_thought_content_end_to_end(
    mock_find, _mock_can, _mock_assistants, _mock_convert, client, export_format
):
    """Real pipeline, thought content absent and the thought-only empty pair skipped entirely
    in concise mode (parameter omitted)."""
    mock_find.return_value = _conversation_with_thought_and_empty_pair()

    response = client.get(f"/v1/conversations/conv-123/export?export_format={export_format}")

    assert response.status_code == 200
    text = response.content.decode("utf-8")
    assert "Search Tool" not in text
    assert "ran search tool" not in text
    assert "Run a tool silently" not in text  # empty pair's user message must not appear: pair fully skipped
    assert "Here is the explanation." in text  # surviving pair's assistant message still present


@pytest.mark.parametrize("export_format", ["pdf", "docx"])
@patch("codemie.service.conversation.message_exporter.pypandoc.convert_text", side_effect=_write_source_as_output)
@patch("codemie.rest_api.routers.conversation.Assistant.get_by_ids", new_callable=MagicMock, return_value=[])
@patch("codemie.rest_api.routers.conversation.Ability.can", new_callable=MagicMock, return_value=True)
@patch("codemie.rest_api.routers.conversation.Conversation.find_by_id", new_callable=MagicMock)
def test_export_full_mode_keeps_thought_content_end_to_end(
    mock_find, _mock_can, _mock_assistants, _mock_convert, client, export_format
):
    """Real pipeline, include_tool_outputs=true reproduces today's full export: thought content
    present and the empty-message/thought-only pair NOT skipped (regression: full variant
    unaffected)."""
    mock_find.return_value = _conversation_with_thought_and_empty_pair()

    response = client.get(f"/v1/conversations/conv-123/export?export_format={export_format}&include_tool_outputs=true")

    assert response.status_code == 200
    text = response.content.decode("utf-8")
    assert "Search Tool" in text
    assert "Run a tool silently" in text  # empty pair's user heading/message still emitted, not skipped
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_conversation_pagination.py -k "concise_mode or full_mode_keeps_thoughts or json_unaffected_by_include_tool_outputs or end_to_end" -v`
Expected: FAIL — `export_conversation` has no `include_tool_outputs` query parameter, so the wiring tests get `KeyError` on `call_args.kwargs["include_tool_outputs"]` and the end-to-end tests see unfiltered thought content / no empty-pair skip regardless of the query string.

- [ ] **Step 3: Implement the router wiring**

In `src/codemie/rest_api/routers/conversation.py`:

- `export_conversation` (`:824-830`): add `include_tool_outputs: bool = False` as a plain function parameter (no `Query`/alias, matching `remove_folder`'s `remove_conversations: bool = False` convention). Pass it into the `_get_streaming_response(...)` call (`:853-861`) as a new `include_tool_outputs=include_tool_outputs` kwarg.
- `_get_streaming_response` (`:864-872`): add `include_tool_outputs: bool = False` as a parameter. In the `pdf`/`docx` branch (`:909-919`), pass `include_tool_outputs=include_tool_outputs` into the `MessageExporter(...)` construction (`:910-914`). The `json` branch (`:892-907`) does not read or reference the parameter at all.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_conversation_pagination.py -v`
Expected: PASS (all tests in the file, including the pre-existing `test_export_json_*` tests, confirming the new parameter doesn't disturb the `json` branch or pagination behavior).

- [ ] **Step 5: Commit**

---

## Self-Review Notes

- **Spec coverage:** Task 1 covers the Approach's step 3 (DocumentBuilder gating + empty-pair-skip, plus the full-mode non-skip regression). Task 2 covers step 2 (`MessageExporter.__init__` default `True`, threading, with a forwarding test that calls `_export_full_conversation` directly). Task 3 covers step 1 (router parameter, pdf/docx-only threading), the acceptance criterion for `json` being unaffected, and — via the new non-mocked end-to-end tests — the AC's explicit requirement for router-level tests that assert thought content absent/present "in the generated document (serialized Pandoc tree or rendered text)" plus the empty-pair-skip case, closing the zero-coverage gap the ticket calls out. The acceptance criterion "per-message export endpoint... retain today's unconditional-full behavior" is covered by Task 2's `test_message_exporter_default_include_tool_outputs_is_true` and by every pre-existing `MessageExporter`/`DocumentBuilder` test in the suite continuing to pass unchanged (Steps 4 of Tasks 1 and 2 explicitly run the full existing test file). "Headings/lists/tables/links/code blocks... continue to render correctly" and the PDF code-block exclusion are architecturally satisfied by construction (no task touches serialization of surviving `Paragraph`/`Heading`/`CodeBlock` content or `_escape_latex_special_chars`/`--listings` handling) and are not re-asserted as a separate task, per the plan's scope (whole-suite/manual verification is the calling flow's stage, not a plan task).
- **Negative-constraints pass:** `ExportUtils.should_include_thought` — no task modifies `export_utils.py`; Task 1 only changes the *caller* (`_add_message_pair_to_document`'s guard around calling `_add_thoughts_to_document`), leaving the predicate itself untouched and still unused in production for the `True`/full path (called with no `export_full_thought` override, exactly as today). `build_single_message_document`/per-message endpoint — no task modifies `message_exporter.py:318-348` or the per-message router handler; Task 2's regression test explicitly guards the shared constructor's default. `json` path — no task modifies the `json` branch of `_get_streaming_response`; Task 3 adds a test proving it tolerates the extra query param. No new Pandoc node types — no task adds to `pandoc_nodes.py`. No param rename/validation — Task 3 uses a plain `bool` matching the existing `remove_conversations: bool = False` convention, wire name unchanged. PDF code-block defect — no task touches `_escape_latex_special_chars`, `--listings`, or PDF header-includes; Task 3's end-to-end tests stub `pypandoc.convert_text` itself, so they never invoke the real pdflatex/pandoc binaries and cannot mask or fix that defect. Large-payload/query-layer filtering — no task touches `ConversationService.get_conversation_history_slice` or any DB-read path. `negative-constraints: all addressed above, none skipped.`
- **Placeholder scan:** no TBD/TODO/"add appropriate handling" phrases; every step shows concrete code or an exact `path:line-range` + one-two sentence description.
- **Type consistency:** `include_tool_outputs: bool = True` is the exact name and default used consistently across `DocumentBuilder.build_conversation_document`, `DocumentBuilder._add_message_pair_to_document`, and `MessageExporter.__init__` (Tasks 1–2); the router (Task 3) uses the same name with default `False`, matching the spec's explicit statement that only the router/`MessageExporter` boundary flips the default, not the shared builder/exporter default.
- **Review-fix verification:** (1) Task 3 now includes `test_export_concise_mode_omits_thought_content_end_to_end`/`test_export_full_mode_keeps_thought_content_end_to_end`, which do not mock `MessageExporter` and assert on real serialized content, exercising the previously-unused empty-pair fixture. (2) Task 2's `test_export_full_conversation_forwards_include_tool_outputs` calls `_export_full_conversation()` directly against a patched `DocumentBuilder` and asserts the kwarg it received, so deleting the forwarding line makes it fail. (3) Task 1's `test_add_message_pair_full_mode_does_not_skip_empty_pair` proves the empty-pair skip does not fire when `include_tool_outputs=True`, and Task 3's full-mode end-to-end test re-verifies the same regression at the router level.
