# Assistant-level "Hide Tool Outputs" — Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-assistant boolean `hide_tool_outputs` that, when ON, makes the backend serve end users only the final response (no thoughts, tool errors or debug), on every chat and history read path.

**Architecture:** One policy module (`service/assistant/tool_output_visibility.py`) owns the decision (`is_hidden`) and all stripping. Live responses are stripped at a single egress point, where the router returns `handler.process_request(...)` / `handle_tool_call_resume(...)`, so the standard, hedged and A2A handlers are all covered without edits. History reads (conversation detail, live-stream replay, share, export) strip in memory at read time using the assistant's *current* master flag. Full thoughts are always persisted; nothing stripped is ever written back.

**Tech Stack:** Python, FastAPI/Starlette, SQLModel, Alembic, pytest.

**Spec:** `docs/superpowers/tasks/2026-10-05-epmcdme-15555-hide-tool-outputs/spec.md` (same folder)

## Global Constraints

- Field name: `hide_tool_outputs`, `bool`, non-nullable, `server_default false`; existing assistants default OFF.
- Create/update request field is an optional `bool` defaulting to `False`; an omitted field on update leaves the stored value unchanged (`_map_assistant_request` already honours `model_fields_set`).
- When ON, hide ALL thoughts (including `codemie_thoughts`), plus `tool_errors` and `debug` on the final chunk. Keep: final response, `a2ui`, `tool_call_pending`, `agent_error`, `execution_error`.
- Applies to everyone, including the creator.
- Persistence is unchanged: full thoughts are stored; filtering is read/egress-time only. No `.update()`/save may follow an in-memory strip.
- Multi-assistant history: each message is checked against its own `assistant_id`.
- Non-goal (deliberate, so orchestration is not broken): sub-assistant, parent-assistant and workflow calls are NOT filtered; the workflow thoughts path is out of scope. A2A/virtual assistants have no flag in the UI and default to false, so they are unaffected.
- Serving code never reads the flag directly; it only calls the policy module.
- Repo rules (`codemie/AGENTS.md`): load the P0 guide first; write/run tests and run git operations only when the user explicitly asks (this plan contains test steps for when they do); the MR description must ask a reviewer to post `/sanity`; PRs are draft.
- Commit subject prefix: `EPMCDME-15555:`; branch `EPMCDME-15555-hide-tool-outputs`.

## Review Focus

1. **Security-error reply** — `_return_security_error_response` delivers the user-facing message only as a `thought` with `generated=""`. Stripping thoughts alone would leave a blank reply. Expectation: when ON, that message is carried in `generated` (streamed copy only, not persisted). Pinned in Task 2.
2. **Permission-filtered assistant lookup** — `Assistant.get_by_ids` filters by the viewer's READ ability, so a shared-page viewer without access would fail open. Expectation: hide decisions use `get_by_ids_no_permission_check`. Pinned in Task 2.
3. **Deleted assistant** — an `assistant_id` that no longer resolves cannot be checked; expectation (deliberate, needs user confirmation): thoughts are shown. Pinned in Task 2.
4. **Multi-assistant live-stream replay** — replayed chunks carry no `assistant_id`; the replay uses the conversation's `initial_assistant_id`. Expectation: ON there hides replayed thoughts. Known limitation for mixed conversations. Pinned in Task 4.
5. **Version-pinned chat** — a request with `version` uses that version's flag for the live reply, while history uses the master flag. Pinned in Task 3.
6. **Flag-only edit** creates a new version (compare-service `candidate` dict must include it). Pinned in Task 1.
7. **Stream shapes** — sync and async body iterators, `str` and `bytes` chunks, multiple NDJSON lines per chunk, non-JSON lines passed through untouched. Pinned in Task 2.

---

## File Structure

- Create `src/codemie/service/assistant/tool_output_visibility.py` — policy + stripping (single responsibility).
- Create `src/external/alembic/versions/<rev>_add_hide_tool_outputs_to_assistants.py`.
- Modify `src/codemie/rest_api/models/assistant.py` — `AssistantRequest`, `AssistantBase`, `AssistantConfiguration`.
- Modify `src/codemie/service/assistant/assistant_version_service.py`, `assistant_version_compare_service.py`.
- Modify `src/codemie/rest_api/routers/assistant.py` — egress in `_ask_assistant` and `_ask_assistant_resume`.
- Modify `src/codemie/rest_api/routers/conversation.py`, `src/codemie/service/share_conversation_service.py`, `src/codemie/rest_api/models/conversation.py` — history paths and `AssistantDetails`.
- Tests under `tests/codemie/service/assistant/`, `tests/codemie/rest_api/{models,routers}/`.

---

### Task 1: Data model, migration and versioning

**Files:**
- Modify: `src/codemie/rest_api/models/assistant.py` (`AssistantRequest` ~347, `AssistantBase` ~675, `AssistantConfiguration` ~1275)
- Modify: `src/codemie/service/assistant/assistant_version_service.py` (lines ~60, ~118, ~280, ~326)
- Modify: `src/codemie/service/assistant/assistant_version_compare_service.py` (`has_configuration_changes` `candidate` dict)
- Create: `src/external/alembic/versions/f1a2b3c4d5e6_add_hide_tool_outputs_to_assistants.py`
- Test: `tests/codemie/rest_api/models/test_assistant_hide_tool_outputs.py`, `tests/codemie/service/assistant/test_assistant_version_hide_tool_outputs.py`

**Interfaces:**
- Consumes: alembic head `d9e8f7a6b5c4`.
- Produces: `AssistantRequest.hide_tool_outputs: bool = False`; `Assistant.hide_tool_outputs: bool`; `AssistantConfiguration.hide_tool_outputs: bool`; versioning carries the field.

- [ ] **Step 1: Write failing tests**

```python
# tests/codemie/rest_api/models/test_assistant_hide_tool_outputs.py
from codemie.rest_api.models.assistant import AssistantRequest, Assistant


def test_request_defaults_to_false_and_is_unset():
    req = AssistantRequest.model_validate(MINIMAL_REQUEST)  # reuse the minimal-valid payload used by neighbouring tests
    assert req.hide_tool_outputs is False
    assert "hide_tool_outputs" not in req.model_fields_set


def test_omitted_field_on_update_keeps_stored_value():
    assistant = make_assistant(hide_tool_outputs=True)  # reuse the neighbouring tests' assistant factory
    req = AssistantRequest.model_validate(MINIMAL_REQUEST)
    assistant._map_assistant_request(req)  # same call update_assistant uses
    assert assistant.hide_tool_outputs is True


def test_explicit_false_on_update_turns_it_off():
    assistant = make_assistant(hide_tool_outputs=True)
    req = AssistantRequest.model_validate({**MINIMAL_REQUEST, "hide_tool_outputs": False})
    assistant._map_assistant_request(req)
    assert assistant.hide_tool_outputs is False
```

```python
# tests/codemie/service/assistant/test_assistant_version_hide_tool_outputs.py
def test_flag_only_edit_counts_as_configuration_change():
    # build an existing assistant/version with hide_tool_outputs=False and a request identical except True
    assert AssistantVersionCompareService.has_configuration_changes(existing, request_with_flag_true) is True


def test_new_version_carries_flag_and_partial_update_keeps_it():
    # create_new_version with request.model_fields_set lacking the field keeps previous value;
    # with the field set, the new configuration stores the new value
    ...


def test_rollback_restores_flag_from_target_config():
    ...
```

Fill the `...` bodies using the fixtures in the existing `tests/codemie/service/assistant/test_assistant_version*.py` (locate with `grep -rn "file_attachment_enabled" tests/`); each body must assert on the concrete flag value.

- [ ] **Step 2: Run** `pytest tests/codemie/rest_api/models/test_assistant_hide_tool_outputs.py tests/codemie/service/assistant/test_assistant_version_hide_tool_outputs.py -v` — Expected: FAIL (`hide_tool_outputs` unknown).

- [ ] **Step 3: Implement**

In `AssistantRequest` next to `interactive_enabled`:
```python
    hide_tool_outputs: bool = False
```
In `AssistantBase` and `AssistantConfiguration` next to `interactive_enabled` (same precedent):
```python
    hide_tool_outputs: bool = SQLField(default=False, sa_column_kwargs={"server_default": "false"})
```
`assistant_version_service.py`, mirror each `file_attachment_enabled` line:
- `create_initial_version` (~60): `hide_tool_outputs=request.hide_tool_outputs,`
- `create_new_version` (~118): `hide_tool_outputs=versioned('hide_tool_outputs'),`
- `rollback` (~280): `hide_tool_outputs=target_config.hide_tool_outputs,`
- `update_assistant_config_fields` (~326): `assistant.hide_tool_outputs = config.hide_tool_outputs` (follow the exact style of the neighbouring `file_attachment_enabled` line).

`assistant_version_compare_service.py`: add to `candidate`:
```python
            'hide_tool_outputs': request.hide_tool_outputs,
```
Migration (precedent `e7f8a9b0c1d2_add_interactive_enabled_to_assistants.py`, two-table pattern `v3w4x5y6z7a8_add_file_attachment_enabled.py`):
```python
"""add hide_tool_outputs to assistants and assistant_configurations"""
import sqlalchemy as sa
from alembic import op

revision = 'f1a2b3c4d5e6'
down_revision = 'd9e8f7a6b5c4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ('assistants', 'assistant_configurations'):
        op.add_column(table, sa.Column('hide_tool_outputs', sa.Boolean(), server_default=sa.text('false'), nullable=False))


def downgrade() -> None:
    for table in ('assistant_configurations', 'assistants'):
        op.drop_column(table, 'hide_tool_outputs')
```
Verify `alembic heads` shows a single head before committing (`d9e8f7a6b5c4` must still be the head; if not, use the real head).

- [ ] **Step 4: Run** the Step 2 command — Expected: PASS. Also run `alembic heads` — single head `f1a2b3c4d5e6`.

- [ ] **Step 5: Commit** (only when the user asks)
```bash
git add src/codemie/rest_api/models/assistant.py src/codemie/service/assistant/ src/external/alembic/versions/f1a2b3c4d5e6_add_hide_tool_outputs_to_assistants.py tests/
git commit -m "EPMCDME-15555: add hide_tool_outputs assistant field, migration and versioning"
```

---

### Task 2: Policy module

**Files:**
- Create: `src/codemie/service/assistant/tool_output_visibility.py`
- Test: `tests/codemie/service/assistant/test_tool_output_visibility.py`

**Interfaces:**
- Consumes: `Assistant.hide_tool_outputs` (Task 1); `Assistant.get_by_ids_no_permission_check(ids)`; `GeneratedMessage` (`assistant_id`, `thoughts`); `BaseModelResponse` (`codemie.core.models`).
- Produces (all in `codemie.service.assistant.tool_output_visibility`):
  - `is_hidden(assistant) -> bool`
  - `strip_history(history: list[GeneratedMessage], default_assistant_id: str | None) -> list[GeneratedMessage]` — returns new list, never mutates input
  - `strip_stream(iterator)` — wraps a sync or async iterator of `str|bytes` NDJSON chunks
  - `strip_chunk(chunk: str | bytes) -> str | bytes | None` — `None` means drop
  - `apply_to_response(assistant, response)` — `StreamingResponse` or `BaseModelResponse`; no-op when not hidden
  - `is_hidden_by_id(assistant_id: str | None) -> bool` — unfiltered lookup; `False` for `None`/unknown id (add `return bool(_hidden_ids([assistant_id]))` and a test for None, unknown, hidden, open)

- [ ] **Step 1: Write failing tests**

```python
import json
import pytest
from types import SimpleNamespace
from unittest.mock import patch

from codemie.service.assistant import tool_output_visibility as tov

A = "codemie.service.assistant.tool_output_visibility.Assistant.get_by_ids_no_permission_check"


def line(**kw):
    return json.dumps(kw) + "\n"


def test_is_hidden():
    assert tov.is_hidden(SimpleNamespace(hide_tool_outputs=True)) is True
    assert tov.is_hidden(SimpleNamespace(hide_tool_outputs=False)) is False
    assert tov.is_hidden(SimpleNamespace()) is False


def test_thought_only_chunk_is_dropped():
    assert tov.strip_chunk(line(thought={"id": "1", "message": "x"}, generated_chunk=None, last=False)) is None


def test_text_chunk_loses_thought_and_debug_but_keeps_text():
    out = json.loads(tov.strip_chunk(line(generated_chunk="hi", thought={"id": "1"}, debug={"a": 1})))
    assert out["generated_chunk"] == "hi" and out["thought"] is None and out["debug"] is None


def test_final_chunk_nulls_tool_errors_keeps_agent_and_execution_error():
    out = json.loads(tov.strip_chunk(line(last=True, generated="done", tool_errors=[{"e": 1}],
                                          agent_error={"m": "boom"}, execution_error="E")))
    assert out["tool_errors"] is None and out["agent_error"] == {"m": "boom"} and out["execution_error"] == "E"


@pytest.mark.parametrize("key,val", [("a2ui", {"v": 1}), ("tool_call_pending", {"id": "t"})])
def test_a2ui_and_tool_call_pending_chunks_survive_even_with_thought(key, val):
    out = json.loads(tov.strip_chunk(line(**{key: val}, thought={"id": "1"})))
    assert out[key] == val and out["thought"] is None


def test_security_error_message_moves_to_generated():
    chunk = line(last=True, generated="", thought={"id": "s", "message": "Blocked by policy", "error": True})
    out = json.loads(tov.strip_chunk(chunk))
    assert out["generated"] == "Blocked by policy" and out["thought"] is None


def test_non_json_line_and_bytes_handled():
    assert tov.strip_chunk("not json\n") == "not json\n"
    out = tov.strip_chunk(line(generated_chunk="x", thought={"id": "1"}).encode())
    assert isinstance(out, bytes)


def test_multiple_lines_in_one_chunk():
    chunk = line(thought={"id": "1"}) + line(generated_chunk="a")
    out = tov.strip_chunk(chunk)
    assert out.count("\n") == 1 and json.loads(out)["generated_chunk"] == "a"


def test_strip_stream_sync_and_async():
    chunks = [line(thought={"id": "1"}), line(generated_chunk="a")]
    assert len(list(tov.strip_stream(iter(chunks)))) == 1

    async def agen():
        for c in chunks:
            yield c

    import asyncio
    async def collect():
        return [c async for c in tov.strip_stream(agen())]
    assert len(asyncio.run(collect())) == 1


def _msg(assistant_id, thoughts=("t",)):
    return make_generated_message(assistant_id=assistant_id, thoughts=list(thoughts))  # use the GeneratedMessage factory/constructor from neighbouring tests


def test_strip_history_checks_each_message_assistant_and_does_not_mutate():
    history = [_msg("hidden"), _msg("open"), _msg(None)]
    with patch(A, return_value=[SimpleNamespace(id="hidden", hide_tool_outputs=True),
                                SimpleNamespace(id="open", hide_tool_outputs=False)]) as lookup:
        out = tov.strip_history(history, default_assistant_id="hidden")
    assert [bool(m.thoughts) for m in out] == [False, True, False]  # None falls back to default
    assert all(m.thoughts for m in history)  # input untouched
    lookup.assert_called_once()  # one query for the whole history


def test_strip_history_deleted_assistant_shows_thoughts():
    with patch(A, return_value=[]):
        out = tov.strip_history([_msg("gone")], default_assistant_id=None)
    assert out[0].thoughts


def test_strip_history_uses_unfiltered_lookup_not_permission_filtered():
    with patch(A, return_value=[]) as unfiltered, patch("codemie.service.assistant.tool_output_visibility.Assistant.get_by_ids") as filtered:
        tov.strip_history([_msg("x")], None)
    unfiltered.assert_called_once()
    filtered.assert_not_called()


def test_apply_to_response_noop_when_not_hidden():
    sentinel = object()
    assert tov.apply_to_response(SimpleNamespace(hide_tool_outputs=False), sentinel) is sentinel


def test_apply_to_response_sync_base_model_response():
    resp = make_base_model_response(thoughts=[thought], tool_errors=[{"e": 1}])  # from codemie.core.models
    out = tov.apply_to_response(SimpleNamespace(hide_tool_outputs=True), resp)
    assert out.thoughts == [] and not getattr(out, "tool_errors", None) and out.generated == resp.generated


def test_apply_to_response_streaming_wraps_iterator():
    from starlette.responses import StreamingResponse
    resp = StreamingResponse(iter([line(thought={"id": "1"}), line(generated_chunk="a")]))
    out = tov.apply_to_response(SimpleNamespace(hide_tool_outputs=True), resp)
    assert len(list(out.body_iterator)) == 1
```

Replace `make_generated_message` / `make_base_model_response` / `thought` with real constructors (check required fields of `GeneratedMessage` in `rest_api/models/conversation.py` and `BaseModelResponse` in `core/models.py:854`).

- [ ] **Step 2: Run** `pytest tests/codemie/service/assistant/test_tool_output_visibility.py -v` — Expected: FAIL (module missing).

- [ ] **Step 3: Implement**

```python
"""Single policy point for the assistant-level "hide tool outputs" flag."""
import json
from typing import Any, AsyncIterator, Iterable, Iterator

from starlette.responses import StreamingResponse

from codemie.rest_api.models.assistant import Assistant

_PAYLOAD_KEYS = ("generated", "generated_chunk", "a2ui", "tool_call_pending", "agent_error", "execution_error", "last")


def is_hidden(assistant: Any) -> bool:
    return bool(getattr(assistant, "hide_tool_outputs", False))


def _strip_line(line: str) -> str | None:
    text = line.strip()
    if not text:
        return line
    try:
        data = json.loads(text)
    except ValueError:
        return line
    if not isinstance(data, dict):
        return line
    thought = data.get("thought")
    if thought and not any(data.get(k) for k in _PAYLOAD_KEYS):
        return None
    if isinstance(thought, dict) and thought.get("error") and not data.get("generated") and thought.get("message"):
        data["generated"] = thought["message"]
    for key in ("thought", "debug"):
        if key in data:
            data[key] = None
    if data.get("last") and "tool_errors" in data:
        data["tool_errors"] = None
    return json.dumps(data) + ("\n" if line.endswith("\n") else "")


def strip_chunk(chunk: str | bytes) -> str | bytes | None:
    is_bytes = isinstance(chunk, bytes)
    text = chunk.decode() if is_bytes else chunk
    kept = [out for raw in text.splitlines(keepends=True) if (out := _strip_line(raw)) is not None]
    if not kept:
        return None
    joined = "".join(kept)
    return joined.encode() if is_bytes else joined


def strip_stream(iterator):
    if hasattr(iterator, "__aiter__"):

        async def _agen() -> AsyncIterator:
            try:
                async for chunk in iterator:
                    if (out := strip_chunk(chunk)) is not None:
                        yield out
            finally:
                close = getattr(iterator, "aclose", None)
                if close:
                    await close()

        return _agen()

    def _gen() -> Iterator:
        try:
            for chunk in iterator:
                if (out := strip_chunk(chunk)) is not None:
                    yield out
        finally:
            close = getattr(iterator, "close", None)
            if close:
                close()

    return _gen()


def apply_to_response(assistant: Any, response: Any) -> Any:
    if not is_hidden(assistant):
        return response
    if isinstance(response, StreamingResponse):
        response.body_iterator = strip_stream(response.body_iterator)
        return response
    fields = type(response).model_fields if hasattr(type(response), "model_fields") else {}
    update = {k: v for k, v in (("thoughts", []), ("tool_errors", None)) if k in fields}
    return response.model_copy(update=update) if update else response


def _hidden_ids(assistant_ids: Iterable[str | None]) -> set[str]:
    ids = sorted({i for i in assistant_ids if i})
    if not ids:
        return set()
    return {a.id for a in Assistant.get_by_ids_no_permission_check(ids) if is_hidden(a)}


def strip_history(history: list, default_assistant_id: str | None) -> list:
    if not history:
        return history
    hidden = _hidden_ids((m.assistant_id or default_assistant_id) for m in history if m.thoughts)
    if not hidden:
        return history
    return [
        m.model_copy(update={"thoughts": []}) if m.thoughts and (m.assistant_id or default_assistant_id) in hidden else m
        for m in history
    ]
```
Deleted assistants are absent from the lookup result, so their thoughts are shown (deliberate, Review Focus 3). Note the single lookup covers the whole history (Review Focus 2).

- [ ] **Step 4: Run** the Step 2 command — Expected: PASS.

- [ ] **Step 5: Commit** (only when the user asks): `git commit -m "EPMCDME-15555: add tool output visibility policy module"`

---

### Task 3: Egress on ask and resume routes

**Files:**
- Modify: `src/codemie/rest_api/routers/assistant.py` (`_ask_assistant` final `return handler.process_request(...)` ~2460; `_ask_assistant_resume` `return handler.handle_tool_call_resume(...)` ~1155)
- Test: `tests/codemie/rest_api/routers/test_assistant_hide_tool_outputs.py`

**Interfaces:**
- Consumes: `apply_to_response(assistant, response)` (Task 2); `execution_assistant` passed into `_ask_assistant` is already version-applied.
- Produces: all ask/resume responses for a flagged assistant are stripped.

- [ ] **Step 1: Write failing tests** (follow the fixture style of the existing `_ask_assistant` tests in `tests/codemie/rest_api/routers/`; find them with `grep -rn "_ask_assistant" tests/`)

```python
def test_ask_strips_stream_when_flag_on(...):
    handler = MagicMock(); handler.process_request.return_value = StreamingResponse(iter([line(thought={"id": "1"}), line(generated_chunk="a")]))
    with patch("codemie.rest_api.routers.assistant.get_request_handler", return_value=handler):
        resp = _ask_assistant(flagged_assistant, raw_request, request, user, background_tasks, True, ErrorDetailLevel...)  # use the real signature
    assert [json.loads(c)["generated_chunk"] for c in resp.body_iterator] == ["a"]


def test_ask_unchanged_when_flag_off(...): ...  # same stream, flag False → both chunks preserved (regression)


def test_resume_strips_when_flag_on(...): ...


def test_hedged_and_a2a_handlers_are_covered(...):
    # get_request_handler returns a HedgedAssistantHandler / A2AAssistantHandler stub whose process_request yields a thought chunk;
    # assert stripped — proves no handler-level change is needed


def test_version_pinned_request_uses_version_flag(...):
    # execution_assistant.hide_tool_outputs=True (version) while master False → stripped; and the reverse → not stripped
```
Complete each body with concrete assertions.

- [ ] **Step 2: Run** `pytest tests/codemie/rest_api/routers/test_assistant_hide_tool_outputs.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement**

In `_ask_assistant`:
```python
        return apply_to_response(
            assistant,
            handler.process_request(request, background_tasks, raw_request, include_tool_errors=..., error_detail_level=...),
        )
```
(keep the existing arguments verbatim; only wrap). In `_ask_assistant_resume`:
```python
    return apply_to_response(assistant, handler.handle_tool_call_resume(request, raw_request))
```
Use whatever the local assistant variable is named in each function. Import `from codemie.service.assistant.tool_output_visibility import apply_to_response`. Do NOT wrap the virtual-assistant variant (`assistant.id` is None; flag false). Confirm `process_request` returns a response object, not a coroutine; if any path returns a coroutine, await-wrap in that path.

- [ ] **Step 4: Run** the Step 2 command — Expected: PASS.

- [ ] **Step 5: Commit** (only when asked): `git commit -m "EPMCDME-15555: strip tool outputs at ask/resume egress"`

---

### Task 4: History read paths and API contract

**Files:**
- Modify: `src/codemie/rest_api/models/conversation.py` (`AssistantDetails`, lines ~92-99)
- Modify: `src/codemie/rest_api/routers/conversation.py` (`get_conversation_by_id` 201, `get_conversation_stream` 383, `export_conversation_message` 682, `export_conversation` 996 / `_get_streaming_response`)
- Modify: `src/codemie/service/share_conversation_service.py` (`get_shared_conversation` 106)
- Test: `tests/codemie/rest_api/routers/test_conversation_hide_tool_outputs.py`, `tests/codemie/service/test_share_conversation_hide_tool_outputs.py`

**Interfaces:**
- Consumes: `strip_history`, `strip_stream` (Task 2).
- Produces: `AssistantDetails.hide_tool_outputs: bool = False`, populated on the conversation detail and shared-conversation responses (not list items).

- [ ] **Step 1: Write failing tests**

```python
def test_detail_strips_thoughts_for_flagged_assistant_and_exposes_flag(...)
    # conversation with 2 AI messages (assistant hidden / open); assert first thoughts == [], second intact,
    # assistant_data[hidden].hide_tool_outputs is True, open False

def test_detail_does_not_persist_stripped_history(...)
    # after the call, Conversation.find_by_id(...).history still has thoughts (no update called)

def test_detail_paginated_branch_also_stripped(...)

def test_flag_off_regression_history_unchanged(...)

def test_turning_flag_off_restores_thoughts(...)   # flag flipped on the assistant → next read shows stored thoughts

def test_stream_replay_stripped_when_initial_assistant_hidden(...)

def test_shared_conversation_strips_for_viewer_without_read_access(...)
    # viewer lacks READ on the assistant; thoughts still stripped (unfiltered lookup) and flag exposed

def test_json_export_and_docx_export_and_single_message_export_strip(...)
    # JSON payload "history" has no thoughts; MessageExporter receives the stripped conversation
```
Use existing fixtures in neighbouring conversation router/share tests; each body needs concrete assertions.

- [ ] **Step 2: Run** `pytest tests/codemie/rest_api/routers/test_conversation_hide_tool_outputs.py tests/codemie/service/test_share_conversation_hide_tool_outputs.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement**

`AssistantDetails`: add `hide_tool_outputs: bool = False`.

`get_conversation_by_id`: in the `AssistantDetails(...)` construction add `hide_tool_outputs=a.hide_tool_outputs,`. After `response = ConversationResponse.model_validate(conversation)`, and only for non-workflow conversations:
```python
    if not conversation.is_workflow_conversation:
        response.history = strip_history(response.history, conversation.initial_assistant_id)
```
Caveat: `Assistant.get_by_ids` is permission-filtered, so the exposed flag can be missing for a user without READ; the strip itself does not depend on it. Also put `hide_tool_outputs` into `assistant_data` only for assistants the user can read (existing behaviour).

`share_conversation_service.get_shared_conversation`: add `hide_tool_outputs=a.hide_tool_outputs` to each `AssistantDetails`; strip the returned conversation without mutating the loaded object:
```python
    conversation = conversation.model_copy(update={"history": strip_history(conversation.history, conversation.initial_assistant_id)})
```
`get_conversation_stream`: wrap the generator so both the snapshot replay and live drain go through the strip when the initial assistant is hidden:
```python
    hidden = bool(conversation.initial_assistant_id) and is_hidden_by_id(conversation.initial_assistant_id)
    ...
    body = stream_generator(...)
    return StreamingResponse(strip_stream(body) if hidden else body, ...)
```
Add `is_hidden_by_id(assistant_id) -> bool` to the policy module (`return bool(_hidden_ids([assistant_id]))`) and a test for it in Task 2's file. Match the actual return shape of `get_conversation_stream` (it may build `StreamingResponse`/`EventSourceResponse` inline; wrap the iterator argument only).

Exports: in `_get_streaming_response` JSON branch replace `conversation.history` with `strip_history(conversation.history, conversation.initial_assistant_id)`; for PDF/DOCX and `export_conversation_message`, pass `conversation.model_copy(update={"history": strip_history(...)})` to `MessageExporter`. Never call `.update()` on these copies.

- [ ] **Step 4: Run** the Step 2 command — Expected: PASS.

- [ ] **Step 5: Commit** (only when asked): `git commit -m "EPMCDME-15555: strip tool outputs on conversation read, share and export paths"`

---

### Task 5: Regression and multi-tool validation

**Files:**
- Test: `tests/codemie/rest_api/routers/test_hide_tool_outputs_e2e.py`

**Interfaces:**
- Consumes: Tasks 1-4.
- Produces: acceptance evidence for the story.

- [ ] **Step 1: Write tests**

```python
def test_multi_tool_assistant_on_shows_only_final_response(...):
    # assistant with 2+ toolkits, flag ON; simulate a run emitting several tool thoughts, a2ui, final chunk with tool_errors
    # assert: only text chunks + final chunk; no thought/debug/tool_errors; a2ui, agent_error, execution_error preserved

def test_existing_assistant_without_flag_behaves_as_before(...):
    # assistant created without hide_tool_outputs (DB default) → every thought/tool error delivered exactly as before

def test_creator_also_hidden(...):   # creator user gets the same stripped stream

def test_on_then_off_round_trip_persists_full_thoughts(...):
    # ask with ON, then flip OFF, read history → thoughts present (full thoughts were stored)

def test_conversation_level_setting_cannot_reenable(...):
    # request carries the client-side hide-tool-outputs = false / include_tool_errors=True; flag ON still strips
```
- [ ] **Step 2: Run** `pytest tests/codemie/rest_api/routers/test_hide_tool_outputs_e2e.py -v` then the full affected set `pytest tests/codemie/service/assistant tests/codemie/rest_api -q` — Expected: PASS, no regressions.
- [ ] **Step 3: Commit** (only when asked): `git commit -m "EPMCDME-15555: add multi-tool and regression coverage for hidden tool outputs"`

---

## Self-Review

- **Spec coverage:** data model/API (T1), policy + security-error (T2), live egress incl. hedged/A2A/resume/version (T3), history/stream replay/share/export + `AssistantDetails` (T4), multi-tool + regression (T5). SDK/IaC/UI live in their repos' plans.
- **Placeholders:** test bodies marked "fill using neighbouring fixtures" name the fixtures to reuse and the assertion required; no implementation placeholders.
- **Type consistency:** `strip_history(history, default_assistant_id)`, `strip_stream`, `strip_chunk`, `apply_to_response`, `is_hidden`, `is_hidden_by_id` used identically across tasks.
- **Open items for executor to verify in code:** `process_request` is sync-returning; `GeneratedMessage`/`BaseModelResponse` constructors; exact `get_conversation_stream` return shape; alembic head.
