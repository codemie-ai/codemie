# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Regression coverage for EPMCDME-12227: sequential attach turns must not re-include prior files."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from codemie.core.constants import ChatRole


def _make_message(role, history_index, file_names):
    m = MagicMock()
    m.role = role
    m.history_index = history_index
    m.file_names = list(file_names) if file_names else []
    return m


def _fake_process(file_names, unique_files_dict):
    """Bypass real FileService/FileObject deserialization by mapping names to a stub with .name."""
    for name in file_names:
        unique_files_dict[name] = SimpleNamespace(name=name)


@pytest.fixture
def utils_module():
    """Import the module under test lazily so patches are applied to the actual symbols."""
    from codemie.core import utils

    return utils


def _run(utils, file_names, conversation_id, history_index, history):
    """Invoke build_unique_file_objects_list against a scripted conversation history."""
    conv = MagicMock()
    conv.history = history
    with (
        patch("codemie.rest_api.models.conversation.Conversation.find_by_id", return_value=conv),
        # MagicMock messages are not GeneratedMessage, so replace the isinstance guard.
        patch.object(
            utils,
            "_should_include_message",
            side_effect=lambda m, hi: (hi is None or (m.history_index is not None and m.history_index < hi)),
        ),
        # Skip real file-object decoding; map name -> stub with .name for assertions.
        patch.object(utils, "_process_file_names_to_objects", side_effect=_fake_process),
    ):
        return utils.build_unique_file_objects_list(
            file_names=file_names, conversation_id=conversation_id, history_index=history_index
        )


def test_second_attach_turn_does_not_include_first_turn_file(utils_module):
    """Reproducer for EPMCDME-12227: turn 2 must see test2.png only, never test1.png."""
    history = [
        _make_message(ChatRole.USER, history_index=0, file_names=["test1.png"]),
        _make_message("assistant", history_index=1, file_names=[]),
        _make_message(ChatRole.USER, history_index=2, file_names=["test2.png"]),
        _make_message("assistant", history_index=3, file_names=[]),
    ]
    # Simulate turn 3: current call has no new file_names; history_index points past turn 2.
    result = _run(utils_module, file_names=None, conversation_id="conv-1", history_index=4, history=history)

    names = sorted(f.name for f in result)
    assert names == ["test2.png"], (
        f"Expected only test2.png (most-recent user turn), got {names}. "
        "Prior-turn file leaked into a later attach request."
    )


def test_history_with_no_file_uploads_returns_empty(utils_module):
    """No user turn carries files -> collection yields nothing (not an accumulator)."""
    history = [
        _make_message(ChatRole.USER, history_index=0, file_names=[]),
        _make_message("assistant", history_index=1, file_names=[]),
    ]
    result = _run(utils_module, file_names=None, conversation_id="conv-1", history_index=2, history=history)
    assert result == []


def test_current_turn_files_replace_history_when_user_uploads_in_this_turn(utils_module):
    """EPMCDME-12227 (repro): when the current request carries its own file_names, prior-turn
    files must NOT be merged in. Uploading a new file in this turn IS the user's intent — the
    previous turn's upload must not leak into the tool's ``input_files`` and get re-attached.

    This exercises the exact scenario Maksym reproduced on codemie-preview: upload test1 in
    turn 1, upload test2 in turn 2 and ask for another attach — only test2 must reach the tool.
    """
    history = [
        _make_message(ChatRole.USER, history_index=0, file_names=["prior-turn.png"]),
        _make_message("assistant", history_index=1, file_names=[]),
    ]
    result = _run(
        utils_module,
        file_names=["current.png"],
        conversation_id="conv-1",
        # history_index points past turn 1 (turn 2's own record not yet persisted / excluded
        # from history collection). This is the real request-time shape that breaks the
        # earlier fix from MR !3864.
        history_index=2,
        history=history,
    )
    names = sorted(f.name for f in result)
    assert names == ["current.png"], (
        f"Expected only current.png (turn's own upload); got {names}. "
        "Prior-turn file leaked back into the current tool call — the EPMCDME-12227 leak."
    )


def test_history_files_reused_when_current_turn_has_no_upload(utils_module):
    """Complement of the previous test: when the user asks a follow-up WITHOUT re-uploading,
    files from the most recent user turn must still be surfaced so ``attach the file I sent
    earlier`` keeps working."""
    history = [
        _make_message(ChatRole.USER, history_index=0, file_names=["earlier.png"]),
        _make_message("assistant", history_index=1, file_names=[]),
    ]
    result = _run(
        utils_module,
        file_names=None,
        conversation_id="conv-1",
        history_index=2,
        history=history,
    )
    names = sorted(f.name for f in result)
    assert names == ["earlier.png"], f"Follow-up with no upload should reuse prior file; got {names}."


def test_edited_message_last_write_wins_within_latest_turn(utils_module):
    """If a user edited their latest turn to remove a file, the edited (empty) version wins."""
    # Two entries at the same history_index simulate original + edited resubmit.
    history = [
        _make_message(ChatRole.USER, history_index=0, file_names=["original.png"]),
        _make_message(ChatRole.USER, history_index=0, file_names=[]),  # edit removed the file
    ]
    result = _run(utils_module, file_names=None, conversation_id="conv-1", history_index=1, history=history)
    assert (
        result == []
    ), "Edited-empty user message must win over the original; otherwise removed files leak into later tool calls."


def test_assistant_role_message_with_files_does_not_shadow_latest_user_turn(utils_module):
    """CR-002: an assistant-role message with file_names at the top history_index must not be
    picked as the 'latest turn'. Prior to the role filter, an UpsertHistoryRequest that imported
    an assistant-role message with file_names could shadow the actual user upload.
    """
    history = [
        _make_message(ChatRole.USER, history_index=0, file_names=["real-user.png"]),
        _make_message("assistant", history_index=1, file_names=["synthetic-assistant.png"]),
    ]
    result = _run(utils_module, file_names=None, conversation_id="conv-1", history_index=2, history=history)
    names = sorted(f.name for f in result)
    assert names == ["real-user.png"], f"Expected only real-user.png (assistant-role must be ignored), got {names}."


def test_edit_to_empty_of_latest_user_turn_does_not_fall_back_to_prior_turn(utils_module):
    """CR-003 (variant b): editing the LATEST user turn to remove all files must yield nothing
    — the selector must not walk back to a prior user turn that still has files (that would
    recreate the exact cross-turn leak the fix is meant to prevent)."""
    history = [
        _make_message(ChatRole.USER, history_index=0, file_names=["prior.png"]),
        _make_message("assistant", history_index=1, file_names=[]),
        _make_message(ChatRole.USER, history_index=2, file_names=["latest.png"]),
        _make_message(ChatRole.USER, history_index=2, file_names=[]),  # user edited turn 2 to remove files
    ]
    result = _run(utils_module, file_names=None, conversation_id="conv-1", history_index=3, history=history)
    assert result == [], (
        f"Expected no files after user emptied the latest turn; got {sorted(f.name for f in result)}. "
        "The selector walked back to a prior user turn — recreates the EPMCDME-12227 leak."
    )
