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

"""GET /assistants/metrics/{conversation_id} reports Headroom savings only when tokens were saved."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from codemie.core.headroom_savings import HeadroomSavings
from codemie.core.models import TokensUsage
from codemie.rest_api.models.conversation import Conversation, ConversationMetrics, GeneratedMessage
from codemie.rest_api.routers.assistant import router
from codemie.rest_api.security.authentication import authenticate

CONVERSATION = "codemie.rest_api.models.conversation.Conversation.find_by_id"
ROUTER = "codemie.rest_api.routers.assistant.execution"
METRICS = f"{ROUTER}.ConversationMetrics.get_by_fields"
EXECUTIONS = "codemie.core.workflow_models.WorkflowExecution.get_by_execution_id"
HEADROOM_KEYS = {"headroom_tokens_saved", "headroom_tokens_original"}

app = FastAPI()
app.include_router(router)
app.dependency_overrides[authenticate] = lambda: MagicMock()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _allowed_and_unhydrated():
    """Access and cost settlement are not under test: the caller may view, and stored usage is served as stored."""
    with (
        patch(f"{ROUTER}.Ability") as ability,
        patch(
            f"{ROUTER}.ConversationService.hydrate_conversation_metrics",
            new_callable=AsyncMock,
            side_effect=lambda conversation, metrics: metrics,
        ),
        patch(
            "codemie.service.workflow_service.WorkflowService.hydrate_execution_cost",
            new_callable=AsyncMock,
            side_effect=lambda execution: execution.tokens_usage,
        ),
    ):
        ability.return_value.can.return_value = True
        yield


def _savings(original, compacted) -> HeadroomSavings:
    return HeadroomSavings(
        tokens_original=original, tokens_compacted=compacted, tokens_saved=original - compacted, mode="sdk"
    )


def _message(headroom=None) -> GeneratedMessage:
    return GeneratedMessage(role="Assistant", message="x", headroom=headroom)


def _conversation(history, workflow=False) -> MagicMock:
    conversation = MagicMock()
    conversation.history = history
    conversation.is_workflow_conversation = workflow
    conversation.get_total_classifier_cost_usd.return_value = 0
    conversation.get_headroom_savings.side_effect = lambda: Conversation.get_headroom_savings(conversation)
    return conversation


def _metrics() -> ConversationMetrics:
    return ConversationMetrics(
        conversation_id="c1", total_money_spent=0.1, total_input_tokens=10, total_output_tokens=5
    )


def test_assistant_conversation_with_savings_returns_the_totals(client):
    conversation = _conversation([_message(_savings(1000, 400)), _message(), _message(_savings(500, 300))])
    with patch(CONVERSATION, return_value=conversation), patch(METRICS, return_value=_metrics()):
        body = client.get("/v1/assistants/metrics/c1").json()

    assert body["headroom_tokens_saved"] == 800
    assert body["headroom_tokens_original"] == 1500


@pytest.mark.parametrize("history", [[], [_message()], [_message(_savings(100, 100))]])
def test_assistant_conversation_without_savings_has_no_headroom_keys(client, history):
    with patch(CONVERSATION, return_value=_conversation(history)), patch(METRICS, return_value=_metrics()):
        body = client.get("/v1/assistants/metrics/c1").json()

    assert HEADROOM_KEYS.isdisjoint(body)


def test_missing_conversation_has_no_headroom_keys(client):
    with patch(CONVERSATION, return_value=None), patch(METRICS, return_value=_metrics()):
        body = client.get("/v1/assistants/metrics/c1").json()

    assert HEADROOM_KEYS.isdisjoint(body)


def _execution(headroom) -> MagicMock:
    execution = MagicMock()
    execution.tokens_usage = TokensUsage(input_tokens=10, output_tokens=5, money_spent=0.1, headroom=headroom)
    return execution


def _workflow_conversation() -> MagicMock:
    message = MagicMock()
    message.execution_id = "exec-1"
    return _conversation([message], workflow=True)


def test_workflow_conversation_with_savings_sums_executions(client):
    with (
        patch(CONVERSATION, return_value=_workflow_conversation()),
        patch(EXECUTIONS, return_value=[_execution(_savings(1000, 600)), _execution(_savings(200, 100))]),
    ):
        body = client.get("/v1/assistants/metrics/c1").json()

    assert body["headroom_tokens_saved"] == 500
    assert body["headroom_tokens_original"] == 1200


@pytest.mark.parametrize("headroom", [None, _savings(100, 100)])
def test_workflow_conversation_without_savings_has_no_headroom_keys(client, headroom):
    with (
        patch(CONVERSATION, return_value=_workflow_conversation()),
        patch(EXECUTIONS, return_value=[_execution(headroom)]),
    ):
        body = client.get("/v1/assistants/metrics/c1").json()

    assert HEADROOM_KEYS.isdisjoint(body)
    assert body["total_input_tokens"] == 10
