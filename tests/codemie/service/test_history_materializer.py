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

"""Tests for materialize_workflow_conversation."""

from unittest.mock import MagicMock, patch

import pytest

from codemie.core.workflow_models import WorkflowExecutionStatusEnum
from codemie.rest_api.models.conversation import GeneratedMessage
from codemie.service.conversation.history_materializer import (
    MaterializedConversation,
    materialize_workflow_conversation,
)


def _plain_message(index: int = 0) -> GeneratedMessage:
    return GeneratedMessage(role="User", message=f"msg-{index}", history_index=index)


def _execution_ref(execution_id: str = "exec-1", index: int = 1) -> GeneratedMessage:
    return GeneratedMessage(
        role="Assistant",
        message=None,
        history_index=index,
        workflow_execution_ref=True,
        execution_id=execution_id,
    )


def _mock_execution(
    status: WorkflowExecutionStatusEnum = WorkflowExecutionStatusEnum.SUCCEEDED,
    output: str = "final output",
    thoughts: list | None = None,
) -> MagicMock:
    execution = MagicMock()
    execution.overall_status = status
    execution.output = output
    execution.update_date = None
    execution.date = None
    execution.tokens_usage = None
    return execution


@pytest.fixture
def mock_workflow_service():
    with patch("codemie.service.workflow_service.WorkflowService") as mock_service:
        yield mock_service


@pytest.fixture
def mock_get_thoughts():
    with patch(
        "codemie.service.conversation.history_materializer._get_execution_thoughts",
        return_value=[],
    ) as mock:
        yield mock


class TestMaterializeWorkflowConversation:
    def test_empty_history_returns_empty_result(self):
        result = materialize_workflow_conversation([])

        assert result.history == []

    def test_returns_materialized_conversation_instance(self):
        result = materialize_workflow_conversation([_plain_message()])

        assert isinstance(result, MaterializedConversation)

    def test_plain_messages_pass_through_unchanged(self):
        messages = [_plain_message(0), _plain_message(1)]

        result = materialize_workflow_conversation(messages)

        assert len(result.history) == 2
        assert result.history[0].message == "msg-0"
        assert result.history[1].message == "msg-1"

    def test_execution_reference_is_replaced_with_materialized_message(self, mock_workflow_service, mock_get_thoughts):
        mock_workflow_service.find_workflow_execution_by_id.return_value = _mock_execution(
            status=WorkflowExecutionStatusEnum.SUCCEEDED, output="done"
        )

        result = materialize_workflow_conversation([_execution_ref("exec-1")])

        assert len(result.history) == 1
        assert result.history[0].message == "done"
        assert result.history[0].workflow_execution_ref is True
        assert result.history[0].execution_id == "exec-1"

    def test_missing_execution_keeps_original_message(self, mock_workflow_service, mock_get_thoughts):
        mock_workflow_service.find_workflow_execution_by_id.return_value = None
        ref = _execution_ref("missing-exec")

        result = materialize_workflow_conversation([ref])

        assert result.history[0] is ref

    def test_materialization_exception_keeps_original_and_continues(self, mock_workflow_service, mock_get_thoughts):
        mock_workflow_service.find_workflow_execution_by_id.side_effect = [
            RuntimeError("db error"),
            _mock_execution(status=WorkflowExecutionStatusEnum.SUCCEEDED, output="ok"),
        ]
        ref1 = _execution_ref("exec-1", index=0)
        ref2 = _execution_ref("exec-2", index=1)

        result = materialize_workflow_conversation([ref1, ref2])

        assert result.history[0] is ref1  # original preserved on error
        assert result.history[1].message == "ok"

    def test_workflow_id_set_as_assistant_id_on_materialized_message(self, mock_workflow_service, mock_get_thoughts):
        mock_workflow_service.find_workflow_execution_by_id.return_value = _mock_execution()

        result = materialize_workflow_conversation([_execution_ref()], workflow_id="wf-42")

        assert result.history[0].assistant_id == "wf-42"

    def test_no_output_falls_back_to_last_thought_message(self, mock_workflow_service):
        execution = _mock_execution(output="")
        mock_workflow_service.find_workflow_execution_by_id.return_value = execution

        with patch(
            "codemie.service.conversation.history_materializer._get_execution_thoughts",
            return_value=[
                {
                    "id": "s1",
                    "author_name": "SomeState",
                    "author_type": "WorkflowState",
                    "message": "thought text",
                    "input_text": None,
                    "children": [],
                    "in_progress": False,
                    "interrupted": False,
                    "aborted": False,
                }
            ],
        ):
            result = materialize_workflow_conversation([_execution_ref()])

        assert result.history[0].message == "thought text"

    def test_tokens_mapped_from_execution(self, mock_workflow_service, mock_get_thoughts):
        execution = _mock_execution()
        execution.tokens_usage = MagicMock(input_tokens=100, output_tokens=50, money_spent=0.01)
        mock_workflow_service.find_workflow_execution_by_id.return_value = execution

        result = materialize_workflow_conversation([_execution_ref()])

        msg = result.history[0]
        assert msg.input_tokens == 100
        assert msg.output_tokens == 50
        assert msg.money_spent == 0.01

    def test_mixed_plain_and_reference_messages(self, mock_workflow_service, mock_get_thoughts):
        mock_workflow_service.find_workflow_execution_by_id.return_value = _mock_execution(
            status=WorkflowExecutionStatusEnum.SUCCEEDED, output="result"
        )
        messages = [
            _plain_message(0),
            _execution_ref("exec-1", index=1),
            _plain_message(2),
        ]

        result = materialize_workflow_conversation(messages)

        assert len(result.history) == 3
        assert result.history[0].message == "msg-0"
        assert result.history[1].message == "result"
        assert result.history[2].message == "msg-2"
