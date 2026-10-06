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

"""Per-call-site real-session SQLite tests for ``Conversation.update(columns=...)`` in the service layer.

Each test seeds a real ``Conversation`` row on the shared in-memory SQLite harness
(``conversation_sqlite_engine`` / ``conversation_update_sql`` from tests/codemie/conftest.py),
runs the real site function (mocking only unrelated collaborators), and asserts exactly one
UPDATE (no pre-read SELECT) plus the expected persisted columns.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.orm.exc import StaleDataError

from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.conversation import Conversation, GeneratedMessage, UpsertHistoryRequest
from codemie.rest_api.models.feedback import FeedbackDeleteRequest, FeedbackRequest, MarkEnum
from codemie.rest_api.models.standard import AuthorEnum
from codemie.core.models import (
    AssistantChatRequest,
    ToolCallPolicy,
    TokensUsage,
    UpdateAiMessageRequest,
    UpdateConversationRequest,
)
from codemie.agents.tool_confirmation.models import ToolCallPendingEvent
from codemie.core.models import UserEntity
from codemie.core.workflow_models import (
    WorkflowConfig,
    WorkflowExecution,
    WorkflowNextState,
    WorkflowState,
)
from codemie.service import chat_naming_service as chat_naming_module
from codemie.service.chat_naming_service import ChatNamingService
from codemie.service.conversation.history_materializer import MaterializedConversation
from codemie.service.conversation_checkpoint_service import ConversationCheckpointService
from codemie.service.conversation_service import ConversationService, UpsertChatHistoryParams
from codemie.service.workflow_service import WorkflowService

_OLD_DATE = datetime(2020, 1, 1, 12, 0, 0)


def _seed(**fields) -> Conversation:
    conv = Conversation(
        id=fields.pop("id", "conv-1"),
        conversation_id=fields.pop("conversation_id", "conv-1"),
        conversation_name=fields.pop("conversation_name", "original"),
        user_id=fields.pop("user_id", "user-1"),
        user_name=fields.pop("user_name", "Test User"),
        date=_OLD_DATE,
        update_date=_OLD_DATE,
        **fields,
    )
    conv.save()
    return conv


def _kinds(statements: list[str]) -> list[str]:
    return [s.strip().split()[0].upper() for s in statements]


def _mock_user(id_: str = "user-1", name: str = "Test User"):
    user = MagicMock()
    user.id = id_
    user.name = name
    return user


def _mock_assistant() -> Assistant:
    return Assistant(
        id="asst-1",
        name="test_assistant",
        description="Test Assistant",
        project="test-project",
        toolkits=[],
        system_prompt="",
        llm_model_type="test_model",
        slug="test",
    )


@patch("codemie.service.conversation_service.AgentWorkspaceService.sync_uploaded_files")
def test_upsert_chat_history_updates_existing_conversation(
    mock_sync_uploaded_files, conversation_sqlite_engine, conversation_update_sql
):
    _seed(
        assistant_ids=["asst-1"],
        initial_assistant_id="asst-1",
        project="old-project",
        history=[],
    )
    request = AssistantChatRequest(
        conversation_id="conv-1",
        text="Hello",
        history=[],
        file_names=[],
    )

    ConversationService.upsert_chat_history(
        UpsertChatHistoryParams(
            request=request,
            assistant=_mock_assistant(),
            user=_mock_user(),
            assistant_response="Hi there",
            tokens_usage=TokensUsage(input_tokens=1, output_tokens=1, money_spent=0.0),
            in_progress=True,  # skip turn-completion collaborators (metrics/monitoring/naming)
        )
    )

    assert len(conversation_update_sql) == 1
    assert _kinds(conversation_update_sql[0]) == ["UPDATE"]
    statement = conversation_update_sql[0][0]
    assert "history" in statement
    assert "project" in statement
    assert "assistant_ids" in statement

    stored = Conversation.find_by_id("conv-1")
    assert len(stored.history) == 2
    assert stored.project == "test-project"
    assert stored.assistant_ids == ["asst-1"]
    mock_sync_uploaded_files.assert_called_once()


@patch("codemie.service.conversation_service.AgentWorkspaceService.sync_uploaded_files")
def test_upsert_chat_history_names_precreated_conversation_and_fills_initial_assistant(
    mock_sync_uploaded_files, conversation_sqlite_engine, conversation_update_sql
):
    _seed(
        conversation_name=None,
        assistant_ids=[],
        initial_assistant_id=None,
        history=[],
    )
    request = AssistantChatRequest(
        conversation_id="conv-1",
        text="Hello",
        history=[],
        file_names=[],
    )

    ConversationService.upsert_chat_history(
        UpsertChatHistoryParams(
            request=request,
            assistant=_mock_assistant(),
            user=_mock_user(),
            assistant_response="Hi there",
            tokens_usage=TokensUsage(input_tokens=1, output_tokens=1, money_spent=0.0),
            in_progress=True,  # skip turn-completion collaborators (metrics/monitoring/naming)
        )
    )

    assert len(conversation_update_sql) == 1
    assert _kinds(conversation_update_sql[0]) == ["UPDATE"]
    statement = conversation_update_sql[0][0]
    assert "conversation_name" in statement
    assert "initial_assistant_id" in statement

    stored = Conversation.find_by_id("conv-1")
    assert stored.conversation_name == ConversationService._truncate_name(request.text)
    assert stored.initial_assistant_id == "asst-1"
    assert stored.assistant_ids == ["asst-1"]


@patch("codemie.service.conversation_service.ConversationService._upsert_conversation_metrics")
def test_upsert_conversation_with_history_appends_in_place(
    mock_metrics, conversation_sqlite_engine, conversation_update_sql
):
    _seed(assistant_ids=["asst-1"], initial_assistant_id="asst-1", history=[])
    request = UpsertHistoryRequest(
        assistant_id="asst-1",
        history=[GeneratedMessage(history_index=0, message="Hello", role="User")],
    )

    ConversationService.upsert_conversation_with_history("conv-1", request, _mock_user(), import_source=None)

    assert len(conversation_update_sql) == 1
    assert _kinds(conversation_update_sql[0]) == ["UPDATE"]
    statement = conversation_update_sql[0][0]
    assert "history" in statement
    assert "assistant_ids" in statement
    stored = Conversation.find_by_id("conv-1")
    assert [m.message for m in stored.history] == ["Hello"]


@patch("codemie.service.conversation_service.ConversationService._upsert_conversation_metrics")
def test_write_once_import_source_is_second_update_with_only_its_column(
    mock_metrics, conversation_sqlite_engine, conversation_update_sql
):
    _seed(assistant_ids=["asst-1"], initial_assistant_id="asst-1", history=[], import_source=None)
    request = UpsertHistoryRequest(
        assistant_id="asst-1",
        history=[GeneratedMessage(history_index=0, message="Hello", role="User")],
    )

    ConversationService.upsert_conversation_with_history("conv-1", request, _mock_user(), import_source="claude_code")

    assert len(conversation_update_sql) == 2
    assert _kinds(conversation_update_sql[0]) == ["UPDATE"]
    assert _kinds(conversation_update_sql[1]) == ["UPDATE"]
    second = conversation_update_sql[1][0]
    assert "import_source" in second
    assert "history" not in second
    assert "assistant_ids" not in second
    stored = Conversation.find_by_id("conv-1")
    assert stored.import_source == "claude_code"
    assert [m.message for m in stored.history] == ["Hello"]


def _feedback_request(**overrides) -> FeedbackRequest:
    fields = {
        "conversationId": "conv-1",
        "assistant_id": "asst-1",
        "request": "What is this?",
        "response": "An answer",
        "messageIndex": 0,
        "author": AuthorEnum.USER,
        "mark": MarkEnum.CORRECT,
        "feedback_id": "fb-1",
    }
    fields.update(overrides)
    return FeedbackRequest(**fields)


def _feedback_delete_request(**overrides) -> FeedbackDeleteRequest:
    fields = {
        "conversationId": "conv-1",
        "feedbackId": "fb-1",
        "messageIndex": 0,
        "assistant_id": "asst-1",
        "author": AuthorEnum.USER,
    }
    fields.update(overrides)
    return FeedbackDeleteRequest(**fields)


@patch("codemie.service.conversation_service.ConversationMonitoringService.send_feedback_metric")
@patch("codemie.service.conversation_service.ConversationMetrics.get_by_conversation_id")
def test_add_feedback_updates_history_with_one_update_and_no_select(
    mock_metrics_get, mock_send_metric, conversation_sqlite_engine, conversation_update_sql
):
    mock_metrics_get.return_value = MagicMock()
    _seed(history=[GeneratedMessage(history_index=0, message="Hi", role="User")])

    ConversationService.add_feedback(_feedback_request(), _mock_user())

    assert len(conversation_update_sql) == 1
    assert _kinds(conversation_update_sql[0]) == ["UPDATE"]
    assert "history" in conversation_update_sql[0][0]
    stored = Conversation.find_by_id("conv-1")
    assert stored.history[0].user_mark.mark == MarkEnum.CORRECT


@patch("codemie.service.conversation_service.ConversationMonitoringService.send_feedback_delete_metric")
@patch("codemie.service.conversation_service.ConversationMetrics.get_by_conversation_id")
def test_remove_feedback_updates_history_with_one_update_and_no_select(
    mock_metrics_get, mock_send_delete_metric, conversation_sqlite_engine, conversation_update_sql
):
    mock_metrics_get.return_value = MagicMock()
    _seed(
        history=[
            GeneratedMessage(
                history_index=0, message="Hi", role="User", user_mark={"mark": "correct", "type": "thumbs"}
            )
        ]
    )

    ConversationService.remove_feedback(_feedback_delete_request(), _mock_user())

    assert len(conversation_update_sql) == 1
    assert _kinds(conversation_update_sql[0]) == ["UPDATE"]
    assert "history" in conversation_update_sql[0][0]
    stored = Conversation.find_by_id("conv-1")
    assert stored.history[0].user_mark is None


@patch("codemie.service.conversation_service.ConversationMonitoringService.send_feedback_delete_metric")
@patch("codemie.service.conversation_service.ConversationMonitoringService.send_feedback_metric")
@patch("codemie.service.conversation_service.ConversationMetrics.get_by_conversation_id")
def test_add_then_remove_feedback_round_trips_user_mark_in_history(
    mock_metrics_get,
    mock_send_metric,
    mock_send_delete_metric,
    conversation_sqlite_engine,
    conversation_update_sql,
):
    mock_metrics_get.return_value = MagicMock()
    _seed(history=[GeneratedMessage(history_index=0, message="Hi", role="User")])

    ConversationService.add_feedback(_feedback_request(), _mock_user())
    assert Conversation.find_by_id("conv-1").history[0].user_mark.mark == MarkEnum.CORRECT

    ConversationService.remove_feedback(_feedback_delete_request(), _mock_user())

    assert len(conversation_update_sql) == 2
    assert _kinds(conversation_update_sql[0]) == ["UPDATE"]
    assert _kinds(conversation_update_sql[1]) == ["UPDATE"]
    assert Conversation.find_by_id("conv-1").history[0].user_mark is None


@patch("codemie.service.conversation_service.ConversationFolder.delete_by_folder")
def test_delete_conversation_folder_clears_folder_and_bumps_update_date(
    mock_delete_folder, conversation_sqlite_engine, conversation_update_sql
):
    _seed(folder="F1")

    ConversationService.delete_conversation_folder(user=_mock_user(), folder="F1", remove_conversations=False)

    assert len(conversation_update_sql) == 1
    assert _kinds(conversation_update_sql[0]) == ["UPDATE"]
    statement = conversation_update_sql[0][0]
    assert "folder" in statement
    assert "update_date" in statement
    stored = Conversation.find_by_id("conv-1")
    assert stored.folder == ""
    assert stored.update_date > _OLD_DATE
    mock_delete_folder.assert_called_once_with("F1", "user-1")


@patch("codemie.service.conversation_service.ConversationFolder.get_by_folder")
@patch("codemie.service.conversation_service.ConversationFolder.create_folder")
def test_update_conversation_folder_persists_folder_and_leaves_update_date_unchanged(
    mock_create_folder, mock_get_by_folder, conversation_sqlite_engine, conversation_update_sql
):
    mock_get_by_folder.return_value = None  # existing_folder.update() is ConversationFolder, not this site
    _seed(folder="Old")

    ConversationService.update_conversation_folder(user=_mock_user(), folder="Old", new_folder="New")

    assert len(conversation_update_sql) == 1
    assert _kinds(conversation_update_sql[0]) == ["UPDATE"]
    statement = conversation_update_sql[0][0]
    assert "folder" in statement
    assert "update_date" not in statement
    stored = Conversation.find_by_id("conv-1")
    assert stored.folder == "New"
    assert stored.update_date == _OLD_DATE


@pytest.mark.parametrize(
    "seed_kwargs, request_kwargs, expected_column, assert_fn",
    [
        ({}, {"name": "Renamed"}, "conversation_name", lambda c: c.conversation_name == "Renamed"),
        ({}, {"llm_model": "gpt-x"}, "llm_model", lambda c: c.llm_model == "gpt-x"),
        (
            {},
            {"enable_image_generation": True},
            "enable_image_generation",
            lambda c: c.enable_image_generation is True,
        ),
        (
            {},
            {"image_generation_model": "gpt-image-1"},
            "image_generation_model",
            lambda c: c.image_generation_model == "gpt-image-1",
        ),
        ({}, {"pinned": True}, "pinned", lambda c: c.pinned is True),
        ({}, {"folder": "F1"}, "folder", lambda c: c.folder == "F1"),
        (
            {},
            {"tool_call_policy": ToolCallPolicy.AUTO_APPROVE},
            "tool_call_policy",
            lambda c: c.tool_call_policy == ToolCallPolicy.AUTO_APPROVE,
        ),
        (
            {"assistant_ids": ["a1", "a2"], "initial_assistant_id": "a1"},
            {"active_assistant_id": "a2"},
            "assistant_ids",
            lambda c: c.assistant_ids == ["a2", "a1"],
        ),
    ],
    ids=[
        "name",
        "llm_model",
        "enable_image_generation",
        "image_generation_model",
        "pinned",
        "folder",
        "tool_call_policy",
        "assistant_ids",
    ],
)
def test_update_conversation_writes_only_the_changed_branch_column(
    seed_kwargs,
    request_kwargs,
    expected_column,
    assert_fn,
    conversation_sqlite_engine,
    conversation_update_sql,
):
    _seed(**seed_kwargs)
    request = UpdateConversationRequest(**request_kwargs)
    conversation = Conversation.find_by_id("conv-1")

    ConversationService.update_conversation(conversation, request)

    assert len(conversation_update_sql) == 1
    assert _kinds(conversation_update_sql[0]) == ["UPDATE"]
    statement = conversation_update_sql[0][0]
    assert expected_column in statement
    assert "update_date" not in statement
    stored = Conversation.find_by_id("conv-1")
    assert assert_fn(stored)


def test_empty_request_on_deleted_row_raises_via_id_only_select(conversation_sqlite_engine, conversation_update_sql):
    conv = _seed()
    with conversation_sqlite_engine.begin() as sql_conn:
        sql_conn.execute(text("DELETE FROM conversations WHERE id = :id"), {"id": "conv-1"})
    request = UpdateConversationRequest()

    with pytest.raises(StaleDataError, match="Record conv-1 has been deleted"):
        ConversationService.update_conversation(conv, request)

    assert len(conversation_update_sql) == 1
    assert _kinds(conversation_update_sql[0]) == ["SELECT"]
    select_sql = conversation_update_sql[0][0]
    assert "conversations.id" in select_sql
    assert "history" not in select_sql


def test_workflow_rename_only_writes_back_materialized_history(conversation_sqlite_engine, conversation_update_sql):
    _seed(
        is_workflow_conversation=True,
        history=[GeneratedMessage(role="Assistant", message="", workflow_execution_ref=True, execution_id="e1")],
    )
    materialized = [GeneratedMessage(role="Assistant", message="materialized output", execution_id="e1")]
    with patch(
        "codemie.service.conversation.history_materializer.materialize_workflow_conversation",
        return_value=MaterializedConversation(history=materialized),
    ):
        conversation = Conversation.get_by_id("conv-1")
    request = UpdateConversationRequest(name="Renamed")

    ConversationService.update_conversation(conversation, request)

    assert len(conversation_update_sql) == 1
    statement = conversation_update_sql[0][0]
    assert "conversation_name" in statement
    assert "history" in statement
    stored = Conversation.find_by_id("conv-1")
    assert stored.conversation_name == "Renamed"
    assert [m.message for m in stored.history] == ["materialized output"]


def test_remove_history_index_persists_in_place_index_decrement(conversation_sqlite_engine, conversation_update_sql):
    _seed(
        history=[
            GeneratedMessage(history_index=0, message="first", role="User", assistant_id="a1"),
            GeneratedMessage(history_index=1, message="second", role="User", assistant_id="a1"),
        ],
        assistant_ids=["a1"],
        initial_assistant_id="a1",
    )
    conversation = Conversation.get_by_id("conv-1")

    ConversationService.remove_conversation_history_index(conversation, 0)

    assert len(conversation_update_sql) == 1
    assert _kinds(conversation_update_sql[0]) == ["UPDATE"]
    statement = conversation_update_sql[0][0]
    assert "history" in statement
    assert "assistant_ids" in statement
    assert "initial_assistant_id" in statement
    stored = Conversation.find_by_id("conv-1")
    assert len(stored.history) == 1
    # The surviving message's history_index must have been decremented in place (it was 1,
    # now 0), proving the in-place mutation persists through the targeted write.
    assert stored.history[0].history_index == 0
    assert stored.history[0].message == "second"


@patch("codemie.core.workflow_models.workflow_execution.WorkflowExecution.delete_by_conversation_ids")
def test_clear_conversation_history_persists_cleared_history(
    mock_delete_by_conv_ids, conversation_sqlite_engine, conversation_update_sql
):
    _seed(
        history=[GeneratedMessage(history_index=0, message="first", role="User", assistant_id="a1")],
        assistant_ids=["a1"],
        initial_assistant_id="a1",
    )
    conversation = Conversation.get_by_id("conv-1")

    ConversationService.clear_conversation_history(conversation)

    assert len(conversation_update_sql) == 1
    assert _kinds(conversation_update_sql[0]) == ["UPDATE"]
    statement = conversation_update_sql[0][0]
    assert "history" in statement
    assert "assistant_ids" in statement
    assert "initial_assistant_id" in statement
    stored = Conversation.find_by_id("conv-1")
    assert stored.history == []
    mock_delete_by_conv_ids.assert_called_once()


def test_update_ai_message_persists_in_place_history_append(conversation_sqlite_engine, conversation_update_sql):
    _seed(
        history=[
            GeneratedMessage(history_index=0, message="user turn", role="User"),
            GeneratedMessage(history_index=0, message="original answer", role="Assistant"),
        ]
    )
    conversation = Conversation.get_by_id("conv-1")
    request = UpdateAiMessageRequest(message_index=0, message="edited answer")

    ConversationService.update_conversation_ai_message(conversation, 0, request)

    assert len(conversation_update_sql) == 1
    assert _kinds(conversation_update_sql[0]) == ["UPDATE"]
    statement = conversation_update_sql[0][0]
    assert "history" in statement
    assert "assistant_ids" not in statement
    assert "initial_assistant_id" not in statement
    stored = Conversation.find_by_id("conv-1")
    assert len(stored.history) == 4
    assert stored.history[-1].message == "edited answer"


def test_save_checkpoint_persists_pending_checkpoint_only(conversation_sqlite_engine, conversation_update_sql):
    _seed(pending_checkpoint=None, pending_tool_call=None)
    checkpoint_data = {"channel_values": {"messages": []}, "ts": "2026-01-01"}

    ConversationCheckpointService().save_checkpoint("conv-1", checkpoint_data)

    assert len(conversation_update_sql) == 1
    assert _kinds(conversation_update_sql[0]) == ["UPDATE"]
    statement = conversation_update_sql[0][0]
    assert "pending_checkpoint" in statement
    assert "pending_tool_call" not in statement
    stored = Conversation.find_by_id("conv-1")
    assert stored.pending_checkpoint == checkpoint_data


def test_save_pending_tool_call_persists_pending_tool_call_only(conversation_sqlite_engine, conversation_update_sql):
    _seed(pending_checkpoint=None, pending_tool_call=None)
    tool_call = ToolCallPendingEvent(
        pending_tool_call_id="call_abc",
        tool_name="search",
        tool_args={"q": "test"},
    )

    ConversationCheckpointService().save_pending_tool_call("conv-1", tool_call)

    assert len(conversation_update_sql) == 1
    assert _kinds(conversation_update_sql[0]) == ["UPDATE"]
    statement = conversation_update_sql[0][0]
    assert "pending_tool_call" in statement
    assert "pending_checkpoint" not in statement
    stored = Conversation.find_by_id("conv-1")
    assert stored.pending_tool_call == tool_call.model_dump()


def test_save_interrupt_context_persists_history_index_and_original_message(
    conversation_sqlite_engine, conversation_update_sql
):
    _seed(
        pending_checkpoint=None,
        pending_tool_call={"pending_tool_call_id": "call_abc", "tool_name": "search", "tool_args": {"q": "test"}},
        history=[],
    )

    ConversationCheckpointService().save_interrupt_context(
        "conv-1", history_index=3, original_user_message="What is this?"
    )

    assert len(conversation_update_sql) == 1
    assert _kinds(conversation_update_sql[0]) == ["UPDATE"]
    statement = conversation_update_sql[0][0]
    assert "pending_tool_call" in statement
    assert "pending_checkpoint" not in statement
    stored = Conversation.find_by_id("conv-1")
    assert stored.pending_tool_call["history_index"] == 3
    assert stored.pending_tool_call["original_user_message"] == "What is this?"


def test_clear_nulls_both_columns_without_touching_history(conversation_sqlite_engine, conversation_update_sql):
    _seed(
        pending_checkpoint={"ts": "2026-01-01"},
        pending_tool_call={"pending_tool_call_id": "call_abc"},
        history=[GeneratedMessage(history_index=0, message="Hi", role="User")],
    )

    ConversationCheckpointService().clear("conv-1")

    assert len(conversation_update_sql) == 1
    assert _kinds(conversation_update_sql[0]) == ["UPDATE"]
    statement = conversation_update_sql[0][0]
    assert "pending_checkpoint" in statement
    assert "pending_tool_call" in statement
    assert "history" not in statement
    stored = Conversation.find_by_id("conv-1")
    assert stored.pending_checkpoint is None
    assert stored.pending_tool_call is None
    assert stored.history == [GeneratedMessage(history_index=0, message="Hi", role="User")]


def test_rename_conversation_persists_conversation_name_and_touches_update_date(
    conversation_sqlite_engine, conversation_update_sql
):
    _seed(conversation_name="original", history=[])

    with (
        patch.object(chat_naming_module, "_is_chat_contextual_naming_enabled", return_value=True),
        patch.object(ChatNamingService, "generate_name", classmethod(lambda cls, *a, **kw: "New Name")),
    ):
        ChatNamingService.rename_conversation(
            conversation_id="conv-1", first_message="hi", assistant_response="hello", request_id="req-1"
        )

    assert len(conversation_update_sql) == 1
    assert _kinds(conversation_update_sql[0]) == ["UPDATE"]
    statement = conversation_update_sql[0][0]
    assert "conversation_name" in statement
    assert "update_date" in statement
    assert "history" not in statement
    stored = Conversation.find_by_id("conv-1")
    assert stored.conversation_name == "New Name"
    assert stored.update_date > _OLD_DATE


def _workflow_config() -> WorkflowConfig:
    return WorkflowConfig(
        id="workflow_123",
        name="Test Workflow",
        description="A test workflow",
        yaml_config="",
        project="test-project",
        states=[
            WorkflowState(id="state1", assistant_id="assistant1", task="task1", next=WorkflowNextState(state_id="end")),
        ],
    )


def _user_model() -> UserEntity:
    return UserEntity(user_id="user-1", username="Test User", name="Test User")


@patch("codemie.service.workflow_service.AgentWorkspaceService.sync_uploaded_files")
def test_create_workflow_execution_appends_history_only(
    mock_sync_uploaded_files, conversation_sqlite_engine, conversation_update_sql
):
    _seed(history=[], is_workflow_conversation=True)

    with (
        patch.object(WorkflowService, "_augment_user_input_with_history", return_value="hi"),
        patch.object(WorkflowExecution, "save"),
    ):
        WorkflowService.create_workflow_execution(
            _workflow_config(), _user_model(), user_input="hi", conversation_id="conv-1"
        )

    assert len(conversation_update_sql) == 1
    assert _kinds(conversation_update_sql[0]) == ["UPDATE"]
    statement = conversation_update_sql[0][0]
    assert "history" in statement
    assert "conversation_name" not in statement
    stored = Conversation.find_by_id("conv-1")
    assert len(stored.history) == 2
    assert stored.history[0].message == "hi"
    mock_sync_uploaded_files.assert_called_once()


def test_append_user_message_on_resume_appends_conversation_history_only(
    conversation_sqlite_engine, conversation_update_sql
):
    _seed(history=[])
    execution = WorkflowExecution(
        workflow_id="workflow_123",
        execution_id="exec-1",
        history=[],
        conversation_id="conv-1",
    )

    with patch.object(WorkflowExecution, "update"):
        WorkflowService().append_user_message_on_resume(execution, "hello resume")

    assert len(conversation_update_sql) == 1
    assert _kinds(conversation_update_sql[0]) == ["UPDATE"]
    statement = conversation_update_sql[0][0]
    assert "history" in statement
    assert "conversation_name" not in statement
    stored = Conversation.find_by_id("conv-1")
    assert len(stored.history) == 2
    assert stored.history[0].message == "hello resume"
