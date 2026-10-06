# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

"""Real-session SQLite tests for the targeted ``Conversation.update(columns=...)`` override."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.orm.exc import StaleDataError
from sqlmodel import Session

from codemie.rest_api.models.conversation import Conversation, GeneratedMessage, UserMark
from codemie.service.conversation.history_materializer import MaterializedConversation

_OLD_DATE = datetime(2020, 1, 1, 12, 0, 0)


def _seed(**fields) -> Conversation:
    conv = Conversation(
        id=fields.pop("id", "conv-1"),
        conversation_id=fields.pop("conversation_id", "conv-1"),
        conversation_name=fields.pop("conversation_name", "original"),
        date=_OLD_DATE,
        update_date=_OLD_DATE,
        **fields,
    )
    conv.save()
    return conv


def _read(engine, id_: str = "conv-1") -> Conversation | None:
    with Session(engine) as session:
        return session.get(Conversation, id_)


def _kinds(statements: list[str]) -> list[str]:
    return [s.strip().split()[0].upper() for s in statements]


def _delete_row(engine, id_: str = "conv-1") -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM conversations WHERE id = :id"), {"id": id_})


def test_update_issues_one_update_and_no_select(conversation_sqlite_engine, conversation_update_sql):
    conv = _seed()
    conv.conversation_name = "renamed"

    conv.update(columns=["conversation_name"])

    assert len(conversation_update_sql) == 1
    assert _kinds(conversation_update_sql[0]) == ["UPDATE"]
    stored = _read(conversation_sqlite_engine)
    assert stored.conversation_name == "renamed"
    assert stored.update_date > _OLD_DATE


def test_update_writes_only_listed_columns_and_update_date(conversation_sqlite_engine, conversation_update_sql):
    _seed(folder="f1")
    conv = Conversation.get_by_id("conv-1")
    # Another writer changes `folder` after we loaded the row; our stale value must not overwrite it.
    with conversation_sqlite_engine.begin() as conn:
        conn.execute(text("UPDATE conversations SET folder = 'f2' WHERE id = 'conv-1'"))
    conv.conversation_name = "renamed"

    conv.update(columns=["conversation_name"])

    statement = conversation_update_sql[0][0]
    assert "conversation_name" in statement
    assert "update_date" in statement
    assert "folder" not in statement
    assert "history" not in statement
    assert _read(conversation_sqlite_engine).folder == "f2"


def test_update_on_deleted_row_raises_stale_data_error(conversation_sqlite_engine, conversation_update_sql):
    conv = _seed()
    _delete_row(conversation_sqlite_engine)
    conv.conversation_name = "renamed"

    with pytest.raises(StaleDataError, match="Record conv-1 has been deleted"):
        conv.update(columns=["conversation_name"])

    assert _read(conversation_sqlite_engine) is None


def test_empty_written_set_on_deleted_row_raises_via_id_only_select(
    conversation_sqlite_engine, conversation_update_sql
):
    conv = _seed()
    _delete_row(conversation_sqlite_engine)

    with pytest.raises(StaleDataError, match="Record conv-1 has been deleted"):
        conv.update(columns=[], touch_timestamp=False)

    assert _kinds(conversation_update_sql[0]) == ["SELECT"]
    select_sql = conversation_update_sql[0][0]
    assert "conversations.id" in select_sql
    assert "history" not in select_sql


def test_empty_written_set_on_existing_row_writes_nothing(conversation_sqlite_engine, conversation_update_sql):
    conv = _seed()

    conv.update(columns=[], touch_timestamp=False)

    assert _kinds(conversation_update_sql[0]) == ["SELECT"]
    assert _read(conversation_sqlite_engine).update_date == _OLD_DATE


def test_update_without_identity_raises(conversation_sqlite_engine, conversation_update_sql):
    conv = Conversation(id="never-saved", conversation_id="never-saved")

    with pytest.raises(ValueError, match="identity"):
        conv.update(columns=["conversation_name"])

    assert conversation_update_sql[0] == []


def test_touch_timestamp_false_leaves_update_date_unchanged(conversation_sqlite_engine, conversation_update_sql):
    conv = _seed()
    conv.pinned = True

    conv.update(columns=["pinned"], touch_timestamp=False)

    stored = _read(conversation_sqlite_engine)
    assert stored.pinned is True
    assert stored.update_date == _OLD_DATE
    assert "update_date" not in conversation_update_sql[0][0]


def test_change_save_revert_on_same_instance_is_persisted(conversation_sqlite_engine, conversation_update_sql):
    conv = _seed()
    conv.conversation_name = "renamed"
    conv.update(columns=["conversation_name"])

    conv.conversation_name = "original"
    conv.update(columns=["conversation_name"])

    assert _read(conversation_sqlite_engine).conversation_name == "original"


def test_second_update_writes_only_its_own_columns(conversation_sqlite_engine, conversation_update_sql):
    conv = _seed()
    conv.history.append(GeneratedMessage(role="User", message="hi", history_index=0))
    conv.update(columns=["history"])

    conv.import_source = "api"
    conv.update(columns=["import_source"])

    second = conversation_update_sql[1][0]
    assert "import_source" in second
    assert "history" not in second
    stored = _read(conversation_sqlite_engine)
    assert stored.import_source == "api"
    assert [m.message for m in stored.history] == ["hi"]


def test_jsonb_none_round_trips(conversation_sqlite_engine, conversation_update_sql):
    conv = _seed(
        pending_checkpoint={"a": 1},
        pending_tool_call={"history_index": 3},
        final_user_mark=UserMark(mark="correct", type="thumbs"),
    )
    conv.pending_checkpoint = None
    conv.pending_tool_call = None
    conv.final_user_mark = None

    conv.update(columns=["pending_checkpoint", "pending_tool_call", "final_user_mark"])

    stored = _read(conversation_sqlite_engine)
    assert stored.pending_checkpoint is None
    assert stored.pending_tool_call is None
    assert stored.final_user_mark is None


def test_workflow_name_only_update_writes_materialized_history(conversation_sqlite_engine, conversation_update_sql):
    _seed(
        is_workflow_conversation=True,
        history=[GeneratedMessage(role="Assistant", message="", workflow_execution_ref=True, execution_id="e1")],
    )
    materialized = [GeneratedMessage(role="Assistant", message="materialized output", execution_id="e1")]
    with patch(
        "codemie.service.conversation.history_materializer.materialize_workflow_conversation",
        return_value=MaterializedConversation(history=materialized),
    ):
        conv = Conversation.get_by_id("conv-1")
    conv.conversation_name = "renamed"

    conv.update(columns=["conversation_name"])

    assert "history" in conversation_update_sql[0][0]
    stored = _read(conversation_sqlite_engine)
    assert [m.message for m in stored.history] == ["materialized output"]
    assert stored.conversation_name == "renamed"


def test_ordinary_chat_name_only_update_does_not_write_history(conversation_sqlite_engine, conversation_update_sql):
    _seed(history=[GeneratedMessage(role="User", message="hi", history_index=0)])
    conv = Conversation.get_by_id("conv-1")
    conv.conversation_name = "renamed"

    conv.update(columns=["conversation_name"])

    assert "history" not in conversation_update_sql[0][0]


def test_detector_warns_for_unlisted_changed_column(conversation_sqlite_engine, conversation_update_sql):
    conv = _seed()
    conv.conversation_name = "renamed"
    conv.folder = "unlisted"

    with patch("codemie.rest_api.models.conversation.logger") as mock_logger:
        conv.update(columns=["conversation_name"])

    mock_logger.warning.assert_called_once()
    assert "folder" in mock_logger.warning.call_args.args[0]
    stored = _read(conversation_sqlite_engine)
    assert stored.conversation_name == "renamed"
    assert stored.folder is None
