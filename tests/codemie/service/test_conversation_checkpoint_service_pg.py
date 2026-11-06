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

"""The checkpoint service's SQL on a real Postgres (AC4, rule 9): namespace merge rules, the pending row, old records.

Live only (TEST_LOCAL_PG_URL); every test runs on a throwaway table, dropped afterwards. Run it with
``TEST_LOCAL_PG_URL=postgresql://postgres:password@localhost:5432/postgres poetry run pytest <this file>``.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import event, text

from codemie.agents.tool_confirmation.conversation_checkpoint_saver import ConversationCheckpointSaver
from codemie.chains.base import Thought
from codemie.rest_api.models.conversation import Conversation, GeneratedMessage

from codemie.agents.tool_confirmation.models import DecisionItem, StoredPendingToolCall, ToolCallPendingEvent
from codemie.core.tool_step_states import CANCELLED_MESSAGE
from codemie.repository.conversation_pause_repository import ConversationPauseRepository
from codemie.service.conversation_checkpoint_service import ConversationCheckpointService
from tests.codemie.service.pg_conversations import live, pg_conversations

pytestmark = live

CONV = "conv-pg"
NS = "s:1"


@pytest.fixture
def db():
    with pg_conversations() as conversations:
        yield conversations


def _write(cid: str, idx: int, task: str = "t1", channel: str = "messages") -> dict:
    return {
        f"{cid}|{task}|{idx}": {
            "checkpoint_id": cid,
            "task_id": task,
            "idx": idx,
            "channel": channel,
            "value": f"{cid}/{idx}",
        }
    }


def _entry(cid: str) -> dict:
    return {"checkpoint_id": cid, "checkpoint": f"cp-{cid}", "metadata": "m"}


# --- the checkpoint rows: put_checkpoint / put_writes merge rules on the conversation_checkpoints table -----


def _repo() -> ConversationPauseRepository:
    return ConversationPauseRepository()


def _row(db, ns: str = NS) -> dict:
    return db.checkpoint_rows(CONV)[ns]


def test_checkpoint_then_writes_keeps_regular_writes_and_replaces_special_ones(db):
    db.insert(CONV)
    repo = _repo()

    repo.put_checkpoint(CONV, NS, _entry("0002"))
    repo.put_writes(CONV, NS, "0002", _write("0002", 0), _write("0002", -3, channel="__interrupt__"))
    first = {**_write("0002", 0)["0002|t1|0"], "value": "second"}
    special = {**_write("0002", -3, channel="__interrupt__")["0002|t1|-3"], "value": "replaced"}
    repo.put_writes(CONV, NS, "0002", {"0002|t1|0": first}, {"0002|t1|-3": special})

    writes = _row(db)["writes"]
    assert writes["0002|t1|0"]["value"] == "0002/0"  # a regular write is never overwritten
    assert writes["0002|t1|-3"]["value"] == "replaced"  # a special write replaces the stored one
    assert _row(db)["checkpoint"] == "cp-0002"

    repo.put_checkpoint(CONV, NS, _entry("0003"))  # an upsert replaces the namespace entry

    assert list(db.checkpoint_rows(CONV)) == [NS]
    assert _row(db)["checkpoint_id"] == "0003" and _row(db)["checkpoint"] == "cp-0003"
    assert repo.get_checkpoint(CONV, NS)["checkpoint_id"] == "0003"


def test_writes_before_a_checkpoint_make_a_writes_only_row_that_is_no_checkpoint(db):
    db.insert(CONV)
    repo = _repo()

    repo.put_writes(CONV, NS, "0002", _write("0002", 0), {})

    assert _row(db)["checkpoint"] is None and _row(db)["checkpoint_id"] is None
    entry = repo.get_checkpoint(CONV, NS)
    assert "checkpoint" not in entry and list(entry["writes"]) == ["0002|t1|0"]
    saver = ConversationCheckpointSaver(repo)
    assert saver.get_tuple({"configurable": {"thread_id": CONV, "checkpoint_ns": NS}}) is None

    repo.put_checkpoint(CONV, NS, _entry("0002"))  # the checkpoint of the same id keeps those writes

    assert list(_row(db)["writes"]) == ["0002|t1|0"] and _row(db)["checkpoint_id"] == "0002"


def test_a_newer_checkpoint_drops_older_writes_and_older_writes_are_ignored(db):
    db.insert(CONV)
    repo = _repo()
    repo.put_checkpoint(CONV, NS, _entry("0002"))
    repo.put_writes(CONV, NS, "0002", _write("0002", 0), {})

    repo.put_checkpoint(CONV, NS, _entry("0003"))
    repo.put_writes(CONV, NS, "0002", _write("0002", 1), {})  # late writes of the older checkpoint

    assert _row(db)["checkpoint_id"] == "0003"
    assert _row(db)["writes"] == {}


def test_namespaces_are_independent_and_the_id_comparison_is_bytewise(db):
    db.insert(CONV)
    repo = _repo()
    repo.put_checkpoint(CONV, "", _entry("1f0-a"))
    repo.put_checkpoint(CONV, NS, _entry("1f0-B"))
    repo.put_writes(CONV, NS, "1f0-B", _write("1f0-B", 0), {})

    # "a" > "B" bytewise (COLLATE "C"), so these writes belong to a newer checkpoint and are kept.
    repo.put_writes(CONV, NS, "1f0-a", _write("1f0-a", 0), {})
    repo.put_checkpoint(CONV, NS, _entry("1f0-a"))

    assert _row(db, "")["checkpoint"] == "cp-1f0-a"
    assert sorted(_row(db)["writes"]) == ["1f0-a|t1|0"]


def _saver_cfg(ns: str = NS, checkpoint_id: str | None = None) -> dict:
    configurable = {"thread_id": CONV, "checkpoint_ns": ns}
    if checkpoint_id:
        configurable["checkpoint_id"] = checkpoint_id
    return {"configurable": configurable}


def _checkpoint(cp_id: str) -> dict:
    return {
        "v": 1,
        "id": cp_id,
        "ts": "2026-01-01",
        "channel_values": {},
        "channel_versions": {},
        "versions_seen": {},
        "pending_sends": [],
    }


def test_the_saver_reads_one_namespace_in_one_select_filtered_by_conversation_and_namespace(db):
    """Rule 6 (AR-4): get_tuple reads the one namespace row it needs, filtered in SQL."""
    db.insert(CONV)
    saver = ConversationCheckpointSaver(_repo())
    saver.put(_saver_cfg(""), _checkpoint("0001"), {"step": 1}, {})
    saver.put(_saver_cfg(), _checkpoint("0001"), {"step": 1}, {})
    statements = _repository_statements(db)

    found = saver.get_tuple(_saver_cfg())

    assert found.config["configurable"] == {"thread_id": CONV, "checkpoint_ns": NS, "checkpoint_id": "0001"}
    assert len(statements) == 1
    select = statements[0].upper()
    assert select.lstrip().startswith("SELECT") and "CONVERSATION_ID" in select.split("WHERE", 1)[1]
    assert "CHECKPOINT_NS" in select.split("WHERE", 1)[1]


def test_the_saver_rules_hold_over_the_repository(db):
    """A newer checkpoint drops the older one's writes; the stored checkpoint's writes come back as pending."""
    db.insert(CONV)
    saver = ConversationCheckpointSaver(_repo())
    first = saver.put(_saver_cfg(), _checkpoint("0001"), {"step": 1}, {})
    saver.put_writes(first, [("messages", "hello"), ("__interrupt__", "pause")], "task-1")

    pending = saver.get_tuple(_saver_cfg()).pending_writes
    assert sorted((task, channel, value) for task, channel, value in pending) == [
        ("task-1", "__interrupt__", "pause"),
        ("task-1", "messages", "hello"),
    ]

    second = saver.put(_saver_cfg(), _checkpoint("0002"), {"step": 2}, {})
    saver.put_writes(first, [("messages", "late")], "task-1")  # late writes of the older checkpoint

    found = saver.get_tuple(_saver_cfg())
    assert found.config["configurable"]["checkpoint_id"] == second["configurable"]["checkpoint_id"] == "0002"
    assert found.pending_writes == []
    assert saver.get_tuple(_saver_cfg(checkpoint_id="0001")) is None


def test_a_missing_conversation_is_an_error(db):
    with pytest.raises(ValueError):
        _repo().put_checkpoint("absent", NS, _entry("0001"))
    with pytest.raises(ValueError):
        _repo().put_writes("absent", NS, "0001", _write("0001", 0), {})


def test_delete_checkpoints_removes_every_namespace_of_the_conversation_only(db):
    db.insert(CONV)
    db.insert("other")
    repo = _repo()
    repo.put_checkpoint(CONV, "", _entry("0001"))
    repo.put_checkpoint(CONV, NS, _entry("0001"))
    repo.put_checkpoint("other", "", _entry("0001"))

    assert repo.delete_checkpoints(CONV) == 2

    assert db.checkpoint_rows(CONV) == {} and list(db.checkpoint_rows("other")) == [""]
    assert repo.get_checkpoint(CONV, NS) is None


# --- concurrent connections: each statement merges inside the database --------------------------------------


def _together(*calls) -> None:
    """Run the calls on their own threads (own sessions), released at once."""
    barrier = threading.Barrier(len(calls))
    errors: list[BaseException] = []

    def run(call):
        try:
            barrier.wait()
            call()
        except BaseException as error:  # noqa: BLE001 - re-raised below
            errors.append(error)

    threads = [threading.Thread(target=run, args=(call,)) for call in calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if errors:
        raise errors[0]


@pytest.mark.parametrize("with_checkpoint", [False, True])
def test_concurrent_put_writes_on_one_row_both_survive(db, with_checkpoint):
    db.insert(CONV)
    repo = _repo()
    if with_checkpoint:
        repo.put_checkpoint(CONV, NS, _entry("0002"))

    for round_ in range(10):
        checkpoint_ns = f"{NS}-{round_}"
        if with_checkpoint:
            repo.put_checkpoint(CONV, checkpoint_ns, _entry("0002"))
        _together(
            lambda ns=checkpoint_ns: repo.put_writes(CONV, ns, "0002", _write("0002", 0, task="a"), {}),
            lambda ns=checkpoint_ns: repo.put_writes(CONV, ns, "0002", _write("0002", 0, task="b"), {}),
            lambda ns=checkpoint_ns: repo.put_writes(CONV, ns, "0002", {}, _write("0002", -3, "c", "__interrupt__")),
        )
        assert sorted(_row(db, checkpoint_ns)["writes"]) == ["0002|a|0", "0002|b|0", "0002|c|-3"]


def test_a_newer_put_racing_writes_of_the_older_id_never_keeps_the_older_writes(db):
    db.insert(CONV)
    repo = _repo()
    for round_ in range(10):
        checkpoint_ns = f"{NS}-{round_}"
        repo.put_checkpoint(CONV, checkpoint_ns, _entry("0002"))
        _together(
            lambda ns=checkpoint_ns: repo.put_checkpoint(CONV, ns, _entry("0003")),
            lambda ns=checkpoint_ns: repo.put_writes(CONV, ns, "0002", _write("0002", 0), {}),
        )
        row = _row(db, checkpoint_ns)
        assert row["checkpoint_id"] == "0003" and row["writes"] == {}


# --- the pending row -----------------------------------------------------------------------------------------


def _item(index: int, auto_allowed: bool = False) -> DecisionItem:
    return DecisionItem(
        thought_id=f"thought_{index}",
        tool_call_id=f"call_{index}",
        location_key="",
        checkpoint_ns="post_model_hook:1",
        event=ToolCallPendingEvent(pending_tool_call_id=f"call_{index}", tool_name="write", tool_args={}),
        auto_allowed=auto_allowed,
    )


def _stored(history_index: int | None = 1, items: list[DecisionItem] | None = None) -> StoredPendingToolCall:
    return StoredPendingToolCall(
        items=items or [_item(0)],
        root_assistant_id="a",
        history_index=history_index,
        original_user_message="go",
        thoughts=[{"id": "thought_0", "interrupted": True}],
        payload={"carried_ids": ["c1"]},
    )


def _pause(db, history_index: int | None = 1) -> ConversationCheckpointService:
    db.insert(CONV)
    service = ConversationCheckpointService()
    service.write_pause(CONV, _stored(history_index))
    return service


def test_write_pause_stores_the_row_and_replaces_an_existing_one(db):
    _pause(db)

    row = db.pending_row(CONV)

    assert (row["root_assistant_id"], row["history_index"], row["original_user_message"]) == ("a", 1, "go")
    assert row["items"][0]["thought_id"] == "thought_0"
    assert row["thoughts"] == [{"id": "thought_0", "interrupted": True}]
    assert row["payload"] == {"carried_ids": ["c1"]}
    assert ConversationCheckpointService().get_pending(CONV).history_index == 1

    ConversationCheckpointService().write_pause(CONV, _stored(2, [_item(5)]))  # replaces the existing row

    row = db.pending_row(CONV)
    assert row["history_index"] == 2 and [item["thought_id"] for item in row["items"]] == ["thought_5"]


def test_write_pause_for_a_missing_conversation_is_an_error(db):
    with pytest.raises(ValueError, match="Conversation not found"):
        ConversationCheckpointService().write_pause("nope", _stored())


def test_get_pending_returns_the_stored_pause_or_none(db):
    db.insert(CONV)
    service = ConversationCheckpointService()
    assert service.get_pending(CONV) is None

    service.write_pause(CONV, _stored())
    pending = service.get_pending(CONV)

    assert pending.original_user_message == "go" and pending.history_index == 1
    assert pending.items[0].thought_id == "thought_0" and pending.payload == {"carried_ids": ["c1"]}


def test_deleting_the_conversation_cascades_to_both_tables(db):
    _pause(db)
    _repo().put_checkpoint(CONV, NS, _entry("0001"))

    db.connection.execute(text("DELETE FROM conversations WHERE id = :c"), {"c": CONV})

    assert db.pending_row(CONV) is None and db.checkpoint_rows(CONV) == {}


def test_the_orm_does_not_map_the_old_columns_so_a_whole_row_save_cannot_restore_them():
    columns = {column.name for column in Conversation.__table__.columns}
    assert "pending_tool_call" not in columns and "pending_checkpoint" not in columns


# --- consume: one transaction, select for update, validate, delete returning ------------------------------------


def test_consume_returns_the_row_and_deletes_it_once(db):
    service = _pause(db)
    _repo().put_checkpoint(CONV, NS, _entry("0001"))
    seen = []

    row = service.consume(CONV, seen.append)

    assert row.history_index == 1 and row.original_user_message == "go"
    assert [item.thought_id for item in seen[0].items] == ["thought_0"]
    assert db.pending_row(CONV) is None
    assert set(db.checkpoint_rows(CONV)) == {NS}, "the resumed run still reads the checkpoint rows"
    assert service.consume(CONV, seen.append) is None and len(seen) == 1


def test_two_concurrent_consumes_give_one_row_and_one_none(db):
    db.insert(CONV)
    service = ConversationCheckpointService()
    results: list = []
    for _ in range(10):
        service.write_pause(CONV, _stored())
        results.clear()
        _together(*[lambda: results.append(service.consume(CONV, lambda row: None))] * 2)
        assert sorted(result is None for result in results) == [False, True]
        assert db.pending_row(CONV) is None


def test_a_validate_that_raises_leaves_the_row_unchanged(db):
    service = _pause(db)
    before = db.pending_row(CONV)

    def refuse(row):
        raise RuntimeError("409")

    with pytest.raises(RuntimeError, match="409"):
        service.consume(CONV, refuse)

    assert db.pending_row(CONV) == before


# --- cancel_pause (rule 7) ------------------------------------------------------------------------------------


def _thought(step_id: str, interrupted: bool, message: str | None = None, children=None) -> Thought:
    return Thought(id=step_id, interrupted=interrupted, message=message, children=children or [])


def _conversation(history_index: int = 1) -> Conversation:
    nested = _thought("delegation", False, children=[_thought("w0", True), _thought("done", False, "result")])
    other = _thought("w9", True)
    return Conversation(
        id=CONV,
        conversation_id=CONV,
        user_id="u",
        history=[
            GeneratedMessage(role="User", message="a", history_index=0),
            GeneratedMessage(role="Assistant", message="", history_index=0, thoughts=[other]),
            GeneratedMessage(role="User", message="b", history_index=history_index),
            # two variants of the paused turn (a regeneration keeps the older one)
            GeneratedMessage(role="Assistant", message="", history_index=history_index, thoughts=[nested]),
            GeneratedMessage(
                role="Assistant", message="", history_index=history_index, thoughts=[_thought("w1", True)]
            ),
        ],
    )


def _repository_statements(db) -> list[str]:
    """Every SQL statement the repository sends from now on (the reader connection is another engine)."""
    statements: list[str] = []
    event.listen(db.engine, "before_cursor_execute", lambda *args: statements.append(args[2]))
    return statements


def _cancel(conversation: Conversation) -> tuple[bool, MagicMock]:
    with patch.object(Conversation, "update") as update:
        return ConversationCheckpointService().cancel_pause(conversation), update


def test_cancel_pause_deletes_the_row_and_the_checkpoints_and_cancels_only_waiting_steps_of_the_turn(db):
    _pause(db)
    _repo().put_checkpoint(CONV, NS, _entry("0001"))
    conversation = _conversation()

    cancelled, update = _cancel(conversation)

    assert cancelled is True
    update.assert_called_once_with()
    assert db.pending_row(CONV) is None and db.checkpoint_rows(CONV) == {}
    history = conversation.history
    nested = history[3].thoughts[0].children
    assert (nested[0].interrupted, nested[0].aborted, nested[0].message) == (False, True, CANCELLED_MESSAGE)
    assert (nested[1].interrupted, nested[1].aborted, nested[1].message) == (False, False, "result")
    assert history[4].thoughts[0].message == CANCELLED_MESSAGE, "every variant of the turn"
    assert history[1].thoughts[0].interrupted is True, "another turn is untouched"
    assert history[1].thoughts[0].message is None


def test_cancel_pause_with_no_row_and_no_waiting_step_sends_one_statement_and_saves_nothing(db):
    """Rule 7 (AR-2): the turn start reads nothing before its one DELETE ... RETURNING."""
    db.insert(CONV)
    _repo().put_checkpoint(CONV, NS, _entry("0001"))
    conversation = Conversation(
        id=CONV,
        conversation_id=CONV,
        user_id="u",
        history=[
            GeneratedMessage(role="User", message="a", history_index=0),
            GeneratedMessage(role="Assistant", message="", history_index=0, thoughts=[_thought("done", False, "r")]),
        ],
    )
    statements = _repository_statements(db)

    cancelled, update = _cancel(conversation)

    assert cancelled is False
    assert len(statements) == 1 and statements[0].lstrip().upper().startswith("DELETE")
    assert "RETURNING" in statements[0].upper()
    update.assert_not_called()
    assert set(db.checkpoint_rows(CONV)) == {NS}, "leftovers stay for the run start and run end deletes"


def test_cancel_pause_with_no_row_cancels_the_waiting_steps_of_the_last_assistant_entry_only(db):
    """Rule 7.2 (AR-3 option c): steps whose pause is gone stop offering Allow/Deny."""
    db.insert(CONV)
    conversation = _conversation()

    cancelled, update = _cancel(conversation)

    assert cancelled is False
    update.assert_called_once_with()
    history = conversation.history
    assert history[4].thoughts[0].message == CANCELLED_MESSAGE, "the last assistant entry"
    assert history[4].thoughts[0].interrupted is False and history[4].thoughts[0].aborted is True
    nested = history[3].thoughts[0].children
    assert nested[0].interrupted is True, "an earlier entry is untouched"
    assert history[1].thoughts[0].interrupted is True
