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

"""GET /v1/assistants/metrics/{id} is gated like the conversation it reports on.

It returns a conversation's token and money totals and can write settled figures back, so a caller
who may not view the conversation must get neither - it used to answer anyone, without authentication.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from codemie.core.ability import Action
from codemie.rest_api.main import app
from codemie.rest_api.routers.assistant.execution import conversation_metrics
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User

ROUTER = "codemie.rest_api.routers.assistant.execution"
CONVERSATION_ID = "conv-of-someone-else"


@pytest.fixture
def client():
    app.dependency_overrides[authenticate] = lambda: User(id="caller", name="Caller")
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def conversation(request):
    conversation = MagicMock(is_workflow_conversation=getattr(request, "param", False))
    with patch("codemie.rest_api.models.conversation.Conversation.find_by_id", return_value=conversation):
        yield conversation


@pytest.fixture
def hydration():
    """Both read paths that can write settled figures back: the assistant one and the workflow one."""
    with (
        patch(f"{ROUTER}.ConversationMetrics.get_by_fields", return_value=MagicMock()),
        patch(f"{ROUTER}.ConversationService.hydrate_conversation_metrics", new_callable=AsyncMock) as hydrate,
        patch(f"{ROUTER}._calculate_workflow_metrics", new_callable=AsyncMock) as workflow_metrics,
    ):
        yield hydrate, workflow_metrics


def test_the_route_requires_an_authenticated_caller() -> None:
    route = next(r for r in app.routes if getattr(r, "endpoint", None) is conversation_metrics)
    assert authenticate in [dependency.call for dependency in route.dependant.dependencies]


@pytest.mark.parametrize("conversation", [False, True], ids=["assistant", "workflow"], indirect=True)
def test_a_caller_who_may_not_view_the_conversation_gets_no_totals_and_triggers_no_write(
    client, conversation, hydration
) -> None:
    with patch(f"{ROUTER}.Ability") as ability:
        ability.return_value.can.return_value = False
        response = client.get(f"/v1/assistants/metrics/{CONVERSATION_ID}")

    assert response.status_code == 401
    assert "total_money_spent" not in response.text
    for read in hydration:
        read.assert_not_awaited()
    # READ, not WRITE: a conversation shared with the caller is theirs to view, totals included.
    ability.return_value.can.assert_called_once_with(Action.READ, conversation)


def test_a_caller_who_may_view_it_gets_the_totals(client, conversation, hydration) -> None:
    hydrate, _ = hydration
    hydrate.return_value = MagicMock(
        total_input_tokens=10,
        total_output_tokens=2,
        total_money_spent=0.5,
        money_spent_pending=False,
        money_spent_unavailable=False,
    )
    conversation.get_total_classifier_cost_usd.return_value = 0
    with patch(f"{ROUTER}.Ability") as ability:
        ability.return_value.can.return_value = True
        response = client.get(f"/v1/assistants/metrics/{CONVERSATION_ID}")

    assert response.status_code == 200
    assert response.json()["total_money_spent"] == 0.5
