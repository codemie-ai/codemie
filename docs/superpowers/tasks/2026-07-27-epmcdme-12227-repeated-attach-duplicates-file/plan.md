# EPMCDME-12227 — Repeated attach request duplicates previous file — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: sdlc-light Stage 4 mandates inline `superpowers:test-driven-development` in the current conversation (no subagent-per-task). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scope conversation-history file collection to only the most recent user turn so sequential attach requests do not re-include previously uploaded files, and tighten `_filter_requested_files` to no longer fall back to *all* files when the LLM specified filenames that did not match.

**Architecture:** Two-file, two-function change. `_collect_files_from_conversation` currently accumulates files from every prior message; we replace the accumulation with a "latest-user-turn only" selector while preserving the `_get_unique_messages_from_history` last-write-wins guard (which handles message editing). `_filter_requested_files` currently falls back to returning `all_files` when the LLM's requested names did not match any available file; we remove that fallback branch (empty result instead) while preserving the "no names requested at all → return all" behavior (12 `FileToolMixin` subclasses rely on the no-names path).

**Tech Stack:** Python 3, pytest, pydantic, existing `Conversation` / `GeneratedMessage` models.

## Global Constraints

- Preserve backward-compat for the 12 `FileToolMixin` subclasses that call `_get_supported_files()`/`_resolve_files()` without ever invoking `_filter_requested_files` (CSVTool, PDFTool, DocxTool, FileAnalysisTool, EmailAnalysisTool, Confluence tools, ADO Wiki tools, xWiki tool). Only `GenericJiraIssueTool.execute` calls `_filter_requested_files`.
- Preserve last-write-wins deduplication for edited messages — the `_get_unique_messages_from_history` helper must remain the pre-filter step so an edited-then-emptied user message wins over its original version.
- No changes to `Conversation` schema, `FileObject`, or public function signatures. `build_unique_file_objects` / `build_unique_file_objects_list` keep the same signature.
- Testing: pytest, follow `.ai-run/guides/testing/testing-patterns.md`. Tests live under `tests/codemie/core/` and `tests/codemie_tools/base/` (mirroring the target files).
- Commits reference `EPMCDME-12227` per `.ai-run/guides/standards/git-workflow.md`.

---

### Task 1: Regression test — sequential attach turns via `build_unique_file_objects_list`

**Files:**
- Test (create): `tests/codemie/core/test_file_collection_scoping.py`

**Interfaces:**
- Consumes: `codemie.core.utils.build_unique_file_objects_list(file_names, conversation_id, history_index)`; `codemie.rest_api.models.conversation.Conversation.find_by_id` (mocked). Signature is unchanged after this plan.
- Produces: nothing — regression test only.

- [ ] **Step 1: Write the failing test**

Create `tests/codemie/core/test_file_collection_scoping.py`:

```python
"""Regression coverage for EPMCDME-12227: sequential attach turns must not re-include prior files."""
from unittest.mock import MagicMock, patch

import pytest

from codemie.core.utils import build_unique_file_objects_list


def _encoded(name: str) -> str:
    """The two-turn scenarios pass file references as name-only tokens; the resolver treats
    an unparseable URL as a plain file name (see _process_file_names_to_objects). Using a
    distinctive prefix keeps them unambiguous in assertions."""
    return f"repo://{name}"


def _make_message(role: str, history_index: int, file_names):
    m = MagicMock()
    m.role = role
    m.history_index = history_index
    m.file_names = list(file_names) if file_names else []
    m.__class__.__name__ = "GeneratedMessage"
    return m


@pytest.fixture
def patch_conversation(monkeypatch):
    """Patch Conversation.find_by_id to return a conversation with a scripted history."""
    def _install(history):
        conv = MagicMock()
        conv.history = history
        with patch("codemie.rest_api.models.conversation.Conversation.find_by_id",
                   return_value=conv):
            yield
    return _install


def _run(file_names, conversation_id, history_index, history):
    """Call the SUT with a patched Conversation whose history is `history`."""
    conv = MagicMock()
    conv.history = history
    with patch("codemie.rest_api.models.conversation.Conversation.find_by_id",
               return_value=conv), \
         patch("codemie.core.utils._should_include_message",
               side_effect=lambda m, hi: (
                   hi is None or (m.history_index is not None and m.history_index < hi)
               )):
        return build_unique_file_objects_list(
            file_names=file_names, conversation_id=conversation_id, history_index=history_index
        )


def test_second_attach_turn_does_not_include_first_turn_file():
    """Reproducer for EPMCDME-12227: turn 2 must see test2.png only, never test1.png."""
    history = [
        _make_message("user", history_index=0, file_names=[_encoded("test1.png")]),
        _make_message("assistant", history_index=1, file_names=[]),
        _make_message("user", history_index=2, file_names=[_encoded("test2.png")]),
        _make_message("assistant", history_index=3, file_names=[]),
    ]
    # Simulate turn 3: current call carries no new file_names, history_index points past turn 2.
    result = _run(file_names=None, conversation_id="conv-1", history_index=4, history=history)

    names = sorted(f.name for f in result)
    assert names == ["test2.png"], (
        f"Expected only test2.png (most-recent user turn), got {names}. "
        "Prior-turn file leaked into a later attach request."
    )


def test_history_with_no_file_uploads_returns_empty():
    """No user turn carries files → collection yields nothing (not the empty accumulator)."""
    history = [
        _make_message("user", history_index=0, file_names=[]),
        _make_message("assistant", history_index=1, file_names=[]),
    ]
    result = _run(file_names=None, conversation_id="conv-1", history_index=2, history=history)
    assert result == []


def test_current_turn_file_names_still_included_alongside_latest_history_turn():
    """When the caller passes current-turn file_names AND history has a prior upload,
    dedup rules still apply — the current turn wins by name, but the latest history turn's
    files are also present when their names differ."""
    history = [
        _make_message("user", history_index=0, file_names=[_encoded("history1.png")]),
    ]
    result = _run(
        file_names=[_encoded("current.png")],
        conversation_id="conv-1",
        history_index=2,
        history=history,
    )
    names = sorted(f.name for f in result)
    assert names == ["current.png", "history1.png"], (
        f"Both current-turn file and latest-history-turn file should be present; got {names}."
    )


def test_edited_message_last_write_wins_within_latest_turn():
    """If a user edited their latest turn to remove a file, the edited (empty) version wins —
    the removed file must not reappear."""
    # Two entries at the same history_index simulate original + edited resubmit.
    history = [
        _make_message("user", history_index=0, file_names=[_encoded("original.png")]),
        _make_message("user", history_index=0, file_names=[]),  # edit removed the file
    ]
    result = _run(file_names=None, conversation_id="conv-1", history_index=1, history=history)
    assert result == [], (
        "Edited-empty user message must win over the original; "
        "otherwise removed files leak into later tool calls."
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/core/test_file_collection_scoping.py -v`
Expected: `test_second_attach_turn_does_not_include_first_turn_file` **FAILS** (returns both `test1.png` and `test2.png`). `test_history_with_no_file_uploads_returns_empty` should PASS on current code (no messages carry files → nothing collected). `test_current_turn_file_names_still_included_alongside_latest_history_turn` PASSes on current code by accident (only one history turn). `test_edited_message_last_write_wins_within_latest_turn` should PASS on current code (dedup already handles this). The one that MUST fail as the reproducer is the first test.

- [ ] **Step 3: Commit the failing test**

```bash
git -C /Users/oleg_sotnichenko/codemie-dev/codemie add tests/codemie/core/test_file_collection_scoping.py
git -C /Users/oleg_sotnichenko/codemie-dev/codemie commit -m "test(EPMCDME-12227): failing regression for sequential attach turns"
```

---

### Task 2: Primary fix — scope `_collect_files_from_conversation` to the latest turn with files

**Files:**
- Modify: `src/codemie/core/utils.py:607-628`

**Interfaces:**
- Consumes: `codemie.rest_api.models.conversation.Conversation`, `_get_unique_messages_from_history`, `_process_file_names_to_objects` (all internal to `utils.py`).
- Produces: unchanged public signature `_collect_files_from_conversation(conversation_id: str, history_index: int | None, unique_files_dict: dict) -> None`. Behavior change: files are collected only from messages whose `history_index` equals the maximum `history_index` among deduplicated messages that carry `file_names`.

- [ ] **Step 1: Modify `_collect_files_from_conversation`**

Replace lines 607-628 of `src/codemie/core/utils.py` with:

```python
def _collect_files_from_conversation(conversation_id: str, history_index: int | None, unique_files_dict: dict) -> None:
    """
    Collect files from the most recent user turn in conversation history.

    Prior to EPMCDME-12227 this function accumulated files from every prior turn, causing
    subsequent attach requests to re-upload files from earlier turns. We now scope collection
    to the single most recent turn that carried any files, defined as: the maximum
    ``history_index`` value present among the deduplicated messages whose ``file_names`` is
    non-empty. All messages at that ``history_index`` contribute (preserving edit-related
    multi-message turns), and the ``_get_unique_messages_from_history`` last-write-wins guard
    still runs first so an edited-to-empty message wins over its original version.

    Args:
        conversation_id: The conversation ID to retrieve history from
        history_index: Optional upper bound (exclusive) on messages to consider; forwarded to
            ``_get_unique_messages_from_history``.
        unique_files_dict: Dictionary to store unique FileObjects (mutated in place).
    """
    from codemie.rest_api.models.conversation import Conversation

    conversation = Conversation.find_by_id(conversation_id)
    history = getattr(conversation, "history", [])

    # Deduplicate messages first so an edited-then-emptied user message replaces its original;
    # this preserves the guard behavior that predates the EPMCDME-12227 fix.
    unique_messages = _get_unique_messages_from_history(history, history_index)

    # Restrict to messages whose file_names is non-empty, then pick the highest history_index
    # among them. This is the "most recent user turn that uploaded files"; earlier turns are
    # excluded so a later attach request never re-includes them (EPMCDME-12227).
    messages_with_files = [
        m for m in unique_messages.values()
        if getattr(m, "file_names", None)
    ]
    if not messages_with_files:
        return

    latest_turn_index = max(
        m.history_index for m in messages_with_files if m.history_index is not None
    )
    for message in messages_with_files:
        if message.history_index == latest_turn_index:
            _process_file_names_to_objects(message.file_names, unique_files_dict)
```

- [ ] **Step 2: Run the regression test to verify it passes**

Run: `poetry run pytest tests/codemie/core/test_file_collection_scoping.py -v`
Expected: all four tests PASS.

- [ ] **Step 3: Run the full utils test module to check for regressions in adjacent code**

Run: `poetry run pytest tests/codemie/core/ -v -k "file or conversation or utils"`
Expected: no new failures. If existing tests fail because they asserted the old accumulator behavior, they must be updated to reflect the new scoping (add an explicit comment referencing EPMCDME-12227 on any such update).

- [ ] **Step 4: Commit**

```bash
git -C /Users/oleg_sotnichenko/codemie-dev/codemie add src/codemie/core/utils.py
git -C /Users/oleg_sotnichenko/codemie-dev/codemie commit -m "fix(EPMCDME-12227): scope conversation file collection to latest user turn"
```

---

### Task 3: Failing test — `_filter_requested_files` must not fall back to all files on name mismatch

**Files:**
- Test (create): `tests/codemie_tools/base/test_file_tool_mixin.py`

**Interfaces:**
- Consumes: `codemie_tools.base.file_tool_mixin.FileToolMixin._filter_requested_files(all_files, params_dict)`.
- Produces: nothing — regression test only.

- [ ] **Step 1: Write the failing test**

Create `tests/codemie_tools/base/test_file_tool_mixin.py`:

```python
"""Coverage for FileToolMixin._filter_requested_files — see EPMCDME-12227 secondary locus."""
from codemie_tools.base.file_tool_mixin import FileToolMixin


class _Subject(FileToolMixin):
    """Minimal subclass — the mixin has no __init__ and no state we need."""


def _fake_files(*names):
    return {name: (b"content-of-" + name.encode(), "text/plain") for name in names}


def test_returns_all_when_no_file_key_in_params():
    """The 'no LLM intent' path is preserved — 12 subclasses rely on this defensive behavior."""
    subj = _Subject()
    result = subj._filter_requested_files(_fake_files("a.txt", "b.txt"), params_dict={})
    assert set(result.keys()) == {"a.txt", "b.txt"}


def test_returns_all_when_file_key_present_but_empty_list():
    """Empty ``files: []`` is treated as 'no names requested', same as key absent."""
    subj = _Subject()
    result = subj._filter_requested_files(_fake_files("a.txt", "b.txt"), params_dict={"files": []})
    assert set(result.keys()) == {"a.txt", "b.txt"}


def test_returns_only_matched_names_when_llm_specifies_matching_names():
    subj = _Subject()
    result = subj._filter_requested_files(
        _fake_files("a.txt", "b.txt"), params_dict={"files": ["a.txt"]}
    )
    assert set(result.keys()) == {"a.txt"}


def test_returns_empty_when_llm_specifies_names_but_none_match():
    """EPMCDME-12227 secondary fix: no more silent fallback to all_files on mismatch.

    Previously the mixin returned every available file when the LLM's names did not match
    (e.g. wrong extension casing, URL-encoding drift). That fallback masked mismatches and
    contributed to attaching stale files to Jira. The tool should now receive an empty
    result and surface the mismatch instead of guessing.
    """
    subj = _Subject()
    result = subj._filter_requested_files(
        _fake_files("a.txt", "b.txt"), params_dict={"files": ["nonexistent.txt"]}
    )
    assert result == {}, (
        "Expected empty result when requested names did not match any available file; "
        "silent fallback to all files masks mismatches and leaks unrelated files."
    )


def test_returns_partial_match_only_when_some_names_match():
    subj = _Subject()
    result = subj._filter_requested_files(
        _fake_files("a.txt", "b.txt"), params_dict={"files": ["a.txt", "missing.txt"]}
    )
    assert set(result.keys()) == {"a.txt"}
```

- [ ] **Step 2: Run the tests to verify the mismatch case fails**

Run: `poetry run pytest tests/codemie_tools/base/test_file_tool_mixin.py -v`
Expected: `test_returns_empty_when_llm_specifies_names_but_none_match` **FAILS** (current code returns both `a.txt` and `b.txt`). All other tests PASS.

- [ ] **Step 3: Commit the failing test**

```bash
git -C /Users/oleg_sotnichenko/codemie-dev/codemie add tests/codemie_tools/base/test_file_tool_mixin.py
git -C /Users/oleg_sotnichenko/codemie-dev/codemie commit -m "test(EPMCDME-12227): failing coverage for _filter_requested_files fallback"
```

---

### Task 4: Secondary fix — remove `_filter_requested_files` all-files fallback on name mismatch

**Files:**
- Modify: `src/codemie_tools/base/file_tool_mixin.py:143-184`

**Interfaces:**
- Consumes: nothing new.
- Produces: unchanged public signature `_filter_requested_files(self, all_files, params_dict) -> Dict[str, Tuple[bytes, str]]`. Behavior change: when the LLM specified names but none matched any available file, the method now returns an empty dict (previously returned `all_files`). The "no names in params" path is unchanged (still returns `all_files`).

- [ ] **Step 1: Modify `_filter_requested_files` line 182 branch**

In `src/codemie_tools/base/file_tool_mixin.py`, replace lines 174-184 (`filtered_files = {}` through `return result`) with:

```python
        filtered_files = {}
        for file_name in requested_file_names:
            if file_name in all_files:
                filtered_files[file_name] = all_files[file_name]
                logger.debug(f"Matched requested file: {file_name}")
            else:
                logger.warning(f"Requested file '{file_name}' not found in available files: {list(all_files.keys())}")

        # EPMCDME-12227: previously this returned `all_files` on empty match, which leaked
        # unrelated files to callers (e.g. Jira attachment). If the LLM specified names,
        # honor that intent — an empty result surfaces the mismatch instead of guessing.
        logger.debug(f"Filtered result: {len(filtered_files)} files - {list(filtered_files.keys())}")
        return filtered_files
```

- [ ] **Step 2: Run the mixin tests to verify all pass**

Run: `poetry run pytest tests/codemie_tools/base/test_file_tool_mixin.py -v`
Expected: all five tests PASS.

- [ ] **Step 3: Run existing Jira and file-analysis tests to check for regressions**

Run:
```
poetry run pytest tests/codemie_tools/core/project_management/jira/ tests/codemie_tools/file_analysis/ -v
```
Expected: no new failures. Note existing coverage does not exercise the mismatch fallback, so this is a safety net rather than a targeted verification.

- [ ] **Step 4: Commit**

```bash
git -C /Users/oleg_sotnichenko/codemie-dev/codemie add src/codemie_tools/base/file_tool_mixin.py
git -C /Users/oleg_sotnichenko/codemie-dev/codemie commit -m "fix(EPMCDME-12227): drop _filter_requested_files fallback to all-files on mismatch"
```

---

### Task 5: End-to-end sanity — full test suite for the two affected modules

**Files:**
- No changes; validation only.

**Interfaces:** N/A.

- [ ] **Step 1: Run the two touched test areas in one command to confirm nothing else regressed**

Run:
```
poetry run pytest tests/codemie/core/ tests/codemie_tools/base/ tests/codemie_tools/core/project_management/jira/ -v
```
Expected: all pass. If a preexisting failure exists (unrelated to this branch on main), record it in the run notes and skip retrying.

- [ ] **Step 2: If step 1 passes, no commit — proceed to Stage 5**

No file changes in this task. The sdlc-light Stage 5 (code review) picks up the diff from Tasks 1–4.
