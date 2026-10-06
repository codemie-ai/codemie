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

import pytest
from unittest.mock import Mock, patch

import httpx
from langgraph.constants import END
from langgraph.types import Command
from openai import APIConnectionError, APITimeoutError, RateLimitError

from codemie.workflows.nodes.summarize_conversation_node import (
    SKIP_SUMMARIZATION,
    SummarizeConversationCommandNode,
    SummarizeConversationNode,
)
from codemie.service.llm_service.llm_service import llm_service
from codemie.core.workflow_models import WorkflowConfig, WorkflowState, WorkflowExecutionStatusEnum
from codemie.core.workflow_models.workflow_models import WorkflowNextState
from codemie.workflows.constants import MESSAGES_VARIABLE, CONTEXT_STORE_VARIABLE, NEXT_KEY


def _make_rate_limit_error():
    response = httpx.Response(429, request=httpx.Request("POST", "https://api.openai.com"))
    return RateLimitError("Rate limit exceeded", response=response, body=None)


@pytest.fixture
def mock_service():
    svc = Mock()
    svc.start_state = Mock(return_value="state-001")
    svc.finish_state = Mock()
    svc.workflow_execution_id = "exec-001"
    return svc


@pytest.fixture
def mock_workflow_config():
    return WorkflowConfig(
        id="wf-1",
        name="Test",
        description="",
        states=[WorkflowState(id="summarize", assistant_id="a1", task="", next=WorkflowNextState(state_id="end"))],
    )


@pytest.fixture
def node(mock_service, mock_workflow_config):
    return SummarizeConversationCommandNode(
        callbacks=[],
        workflow_execution_service=mock_service,
        thought_queue=Mock(),
        workflow_config=mock_workflow_config,
    )


def _state_schema(next_node="assistant_2"):
    return {MESSAGES_VARIABLE: [], CONTEXT_STORE_VARIABLE: {}, NEXT_KEY: [next_node]}


@patch("codemie.workflows.nodes.summarize_conversation_node.should_summarize_memory")
@patch("codemie.workflows.nodes.summarize_conversation_node.get_llm_by_credentials")
def test_non_transient_llm_error_propagates_to_base_node_handler(mock_llm, mock_should_summarize, node, mock_service):
    """Model/non-retryable errors must propagate so BaseNode calls finish_state(FAILED)."""
    mock_should_summarize.return_value = (5000, True)
    mock_llm.return_value.invoke.side_effect = ValueError("invalid response")

    with pytest.raises(ValueError, match="invalid response"):
        node(_state_schema())

    finish_call = mock_service.finish_state.call_args
    assert finish_call is not None
    called_status = finish_call[1].get("status") if finish_call[1] else finish_call[0][2]
    assert called_status == WorkflowExecutionStatusEnum.FAILED


@patch("codemie.workflows.nodes.summarize_conversation_node.should_summarize_memory")
@patch("codemie.workflows.nodes.summarize_conversation_node.get_llm_by_credentials")
@pytest.mark.parametrize(
    "transient_error_factory",
    [
        lambda: ConnectionError("LLM timeout"),
        lambda: TimeoutError("timed out"),
        lambda: httpx.TimeoutException("httpx timeout"),
        lambda: httpx.ConnectError("connection refused"),
        lambda: APITimeoutError(request=Mock()),
        lambda: APIConnectionError(request=Mock(), message="openai connection"),
    ],
)
def test_transient_llm_error_skips_summarization_and_continues(
    mock_llm, mock_should_summarize, node, mock_service, transient_error_factory
):
    """Retryable LLM errors skip summarization and continue to NEXT_KEY without rewriting messages."""
    mock_should_summarize.return_value = (5000, True)
    mock_llm.return_value.invoke.side_effect = transient_error_factory()

    result = node(_state_schema("assistant_2"))

    assert isinstance(result, Command)
    assert result.goto == "assistant_2"
    assert MESSAGES_VARIABLE not in (result.update or {})
    finish_call = mock_service.finish_state.call_args
    assert finish_call is not None
    called_status = finish_call[1].get("status") if finish_call[1] else finish_call[0][2]
    assert called_status == WorkflowExecutionStatusEnum.SUCCEEDED


@patch("codemie.workflows.nodes.summarize_conversation_node.should_summarize_memory")
def test_no_summarization_needed_goes_to_end(mock_should_summarize, node):
    """When summarization is not needed, the existing None path still ends the subgraph."""
    mock_should_summarize.return_value = (100, False)

    result = node.execute(_state_schema(), {})
    assert result is None

    command = node.finalize_and_update_state(None, "null", True, _state_schema())
    assert isinstance(command, Command)
    assert command.goto == END


def test_skip_sentinel_continues_without_message_rewrite(node):
    command = node.finalize_and_update_state(SKIP_SUMMARIZATION, "null", True, _state_schema("assistant_2"))
    assert isinstance(command, Command)
    assert command.goto == "assistant_2"
    assert not command.update


@patch("codemie.workflows.nodes.summarize_conversation_node.should_summarize_memory")
@patch("codemie.workflows.nodes.summarize_conversation_node.get_llm_by_credentials")
def test_rate_limit_error_skips_summarization_and_continues(mock_llm, mock_should_summarize, node, mock_service):
    """HTTP 429 / RateLimitError must be treated as transient and skip summarization."""
    mock_should_summarize.return_value = (5000, True)
    mock_llm.return_value.invoke.side_effect = _make_rate_limit_error()

    result = node(_state_schema("assistant_2"))

    assert isinstance(result, Command)
    assert result.goto == "assistant_2"
    assert MESSAGES_VARIABLE not in (result.update or {})
    finish_call = mock_service.finish_state.call_args
    assert finish_call is not None
    called_status = finish_call[1].get("status") if finish_call[1] else finish_call[0][2]
    assert called_status == WorkflowExecutionStatusEnum.SUCCEEDED


def test_finalize_and_update_state_skip_with_missing_next_key_does_not_crash(node):
    """SKIP_SUMMARIZATION with no NEXT_KEY in state must not raise TypeError."""
    state_without_next = {MESSAGES_VARIABLE: [], CONTEXT_STORE_VARIABLE: {}}
    result = node.finalize_and_update_state(SKIP_SUMMARIZATION, "null", True, state_without_next)
    assert isinstance(result, Command)


class TestFallbackToPlatformDefaultModel:
    """Regression for CR-023: a falsy resolved/actual model used to be passed as
    llm_model=None explicitly to get_llm_by_credentials, overriding its own
    `llm_model: str = llm_service.default_llm_model` default and raising
    ModelNotAllowedException("Model 'None' does not exist") instead of using the
    platform default."""

    @patch("codemie.workflows.nodes.summarize_conversation_node.get_llm_by_credentials")
    def test_execute_with_no_workflow_config_uses_platform_default(self, mock_get_llm, mock_service):
        node = SummarizeConversationNode(
            callbacks=[],
            workflow_execution_service=mock_service,
            thought_queue=Mock(),
            workflow_config=None,
        )
        mock_get_llm.return_value.invoke.return_value.content = "summary"

        node.execute(_state_schema(), {})

        assert mock_get_llm.call_args.kwargs["llm_model"] == llm_service.default_llm_model

    @patch("codemie.workflows.nodes.summarize_conversation_node.get_llm_by_credentials")
    def test_summarize_single_with_none_resolved_model_uses_platform_default(self, mock_get_llm, node):
        mock_get_llm.return_value.invoke.return_value.content = "summary"

        node._summarize_single(messages=[], resolved_model=None)

        assert mock_get_llm.call_args.kwargs["llm_model"] == llm_service.default_llm_model

    @patch("codemie.workflows.nodes.summarize_conversation_node.get_llm_by_credentials")
    def test_summarize_in_batches_with_none_resolved_model_uses_platform_default(self, mock_get_llm, node):
        mock_get_llm.return_value.invoke.return_value.content = "summary"

        node._summarize_in_batches(messages=[Mock()], resolved_model=None)

        assert mock_get_llm.call_args.kwargs["llm_model"] == llm_service.default_llm_model
