# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for the add_conversation_pause_tables migration and its models."""

from __future__ import annotations

import importlib
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic.script import ScriptDirectory

from codemie.rest_api.models.conversation import Conversation
from codemie.rest_api.models.conversation_pause import ConversationCheckpointRow, ConversationPendingToolCallRow

MODULE = "external.alembic.versions.b4c8e1f2a9d3_add_conversation_pause_tables"


class _OpRecorder:
    """Minimal stand-in for alembic's `op`, recording the calls a migration makes."""

    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    def create_table(self, table_name, *columns):
        self.calls.append(("create_table", (table_name, columns)))

    def drop_table(self, table_name):
        self.calls.append(("drop_table", table_name))

    def f(self, name):
        return name

    def get_bind(self):
        return "connection"


def _run(monkeypatch, direction):
    module = importlib.import_module(MODULE)
    recorder = _OpRecorder()
    monkeypatch.setattr(module, "op", recorder)
    monkeypatch.setattr(module, "_end_old_format_records", lambda connection: None)
    monkeypatch.setattr(
        module, "_end_new_format_pauses", lambda connection: recorder.calls.append(("end_new_format_pauses", None))
    )
    getattr(module, direction)()
    return recorder


def _created(recorder):
    result = {}
    for kind, payload in recorder.calls:
        if kind == "create_table":
            name, items = payload
            result[name] = items
    return result


def _pk(items):
    pk = [i for i in items if isinstance(i, sa.PrimaryKeyConstraint)]
    assert len(pk) == 1
    return list(pk[0].columns.keys()) if pk[0].columns.keys() else list(pk[0]._pending_colargs)


def _columns(items):
    return {i.name: i for i in items if isinstance(i, sa.Column)}


def test_upgrade_creates_both_tables_with_rule_3_primary_keys(monkeypatch):
    tables = _created(_run(monkeypatch, "upgrade"))

    assert set(tables) == {"conversation_checkpoints", "conversation_pending_tool_calls"}
    assert _pk(tables["conversation_checkpoints"]) == ["conversation_id", "checkpoint_ns"]
    assert _pk(tables["conversation_pending_tool_calls"]) == ["conversation_id"]


def test_upgrade_references_conversations_with_cascade(monkeypatch):
    tables = _created(_run(monkeypatch, "upgrade"))

    for items in tables.values():
        fks = [i for i in items if isinstance(i, sa.ForeignKeyConstraint)]
        assert len(fks) == 1
        assert list(fks[0].column_keys) == ["conversation_id"]
        assert fks[0].elements[0]._colspec == "conversations.id"
        assert fks[0].ondelete == "CASCADE"


def test_checkpoint_and_metadata_are_nullable(monkeypatch):
    columns = _columns(_created(_run(monkeypatch, "upgrade"))["conversation_checkpoints"])

    assert columns["checkpoint"].nullable is True
    assert columns["metadata"].nullable is True
    assert set(columns) == {
        "conversation_id",
        "checkpoint_ns",
        "checkpoint_id",
        "checkpoint",
        "metadata",
        "writes",
    }


def test_pending_tool_call_columns(monkeypatch):
    columns = _columns(_created(_run(monkeypatch, "upgrade"))["conversation_pending_tool_calls"])

    assert set(columns) == {
        "conversation_id",
        "root_assistant_id",
        "history_index",
        "original_user_message",
        "items",
        "thoughts",
        "payload",
    }
    assert set(ConversationPendingToolCallRow.__table__.c.keys()) == set(columns)
    assert "saved" not in columns
    assert "run_id" not in columns
    assert "claimed_at" not in columns
    assert columns["history_index"].nullable is True
    assert isinstance(columns["original_user_message"].type, sa.Text)
    for name in ("items", "thoughts", "payload"):
        assert isinstance(columns[name].type, postgresql.JSONB)


def test_downgrade_drops_both_tables(monkeypatch):
    recorder = _run(monkeypatch, "downgrade")

    assert sorted(p for k, p in recorder.calls if k == "drop_table") == [
        "conversation_checkpoints",
        "conversation_pending_tool_calls",
    ]


def test_downgrade_ends_the_pauses_before_dropping_their_tables(monkeypatch):
    recorder = _run(monkeypatch, "downgrade")

    assert [kind for kind, _ in recorder.calls] == ["end_new_format_pauses", "drop_table", "drop_table"]


def test_revision_follows_the_previous_head():
    module = importlib.import_module(MODULE)
    assert module.down_revision == "b7e2d4f6a8c1"
    versions = Path(importlib.import_module("external.alembic.versions").__path__[0])
    script = ScriptDirectory(str(versions.parent))
    heads = script.get_heads()
    # Later migrations may chain after this one; the history must stay linear and still contain it
    assert len(heads) == 1
    assert module.revision in {rev.revision for rev in script.walk_revisions("base", heads[0])}


def test_models_map_the_metadata_column_under_another_attribute():
    assert ConversationCheckpointRow.__table__.c["metadata"].nullable is True
    assert "checkpoint_metadata" in ConversationCheckpointRow.model_fields
    assert list(ConversationPendingToolCallRow.__table__.primary_key.columns.keys()) == ["conversation_id"]


def test_conversation_no_longer_maps_the_old_pause_columns():
    assert "pending_checkpoint" not in Conversation.model_fields
    assert "pending_tool_call" not in Conversation.model_fields
    assert "pending_checkpoint" not in Conversation.__table__.c
    assert "pending_tool_call" not in Conversation.__table__.c


CANCELLED_TEXT = "Tool call was cancelled without a decision."


def _step(thought_id, **fields):
    return {"id": thought_id, "message": "", **fields}


def _history():
    return [
        {"role": "User", "message": "first", "history_index": 0},
        {
            "role": "Assistant",
            "message": "first answer",
            "history_index": 0,
            "thoughts": [_step("old", interrupted=True)],
        },
        {"role": "User", "message": "second", "history_index": 1},
        {
            "role": "Assistant",
            "message": "second answer",
            "history_index": 1,
            "thoughts": [
                _step("waiting", interrupted=True, in_progress=True, message="Waiting"),
                _step("done", interrupted=False, message="result"),
                _step(
                    "parent", children=[_step("child", interrupted=True, children=[_step("grand", interrupted=True)])]
                ),
            ],
        },
    ]


def test_cancelled_history_cancels_only_the_waiting_steps_of_the_last_assistant_entry():
    module = importlib.import_module(MODULE)
    history = _history()

    patched = module._cancelled_history(history)

    cancelled = {"interrupted": False, "aborted": True, "in_progress": False, "message": CANCELLED_TEXT}
    thoughts = patched[3]["thoughts"]
    assert thoughts[0] == _step("waiting", **cancelled)
    child = thoughts[2]["children"][0]
    assert child == _step("child", **cancelled, children=[_step("grand", **cancelled)])
    assert thoughts[1] == history[3]["thoughts"][1]
    assert patched[:3] == history[:3]
    assert patched[1]["thoughts"][0]["interrupted"] is True


def test_cancelled_history_without_an_assistant_entry_is_unchanged():
    module = importlib.import_module(MODULE)
    history = [{"role": "User", "message": "hi"}]

    assert module._cancelled_history(history) == history
    assert module._cancelled_history(None) == []
