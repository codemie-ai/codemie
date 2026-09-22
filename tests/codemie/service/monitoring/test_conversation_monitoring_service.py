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

from unittest.mock import patch

import pytest

from codemie.core.models import TokensUsage
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.base import ConversationStatus
from codemie.rest_api.utils.client_context import ClientSource
from codemie.rest_api.security.user import User
from codemie.service.monitoring.conversation_monitoring_service import ConversationMonitoringService
from codemie.service.monitoring.metrics_constants import MetricsAttributes


@pytest.fixture
def mock_assistant():
    return Assistant(
        name="test_assistant",
        description="Test Assistant",
        project="test",
        toolkits=[],
        system_prompt="",
        llm_model_type="test_model",
        slug="test",
        mcp_servers=[],
        assistant_ids=[],
    )


@pytest.fixture
def mock_user():
    return User(name="test_user", username="test@example.com", id="test_id")


@pytest.fixture
def tokens_usage():
    return TokensUsage(input_tokens=10, output_tokens=20, money_spent=0.01)


@patch.object(ConversationMonitoringService, "send_count_metric")
@pytest.mark.parametrize(
    "client_source,expected_value",
    [
        (ClientSource.MS_TEAMS_BOT, ClientSource.MS_TEAMS_BOT.value),
        (ClientSource.OTHER, ClientSource.OTHER.value),
        (None, ClientSource.PLATFORM.value),
    ],
)
def test_send_conversation_metric_emits_client_source(
    mock_send_count_metric, mock_assistant, mock_user, tokens_usage, client_source, expected_value
):
    ConversationMonitoringService.send_conversation_metric(
        user=mock_user,
        assistant=mock_assistant,
        tokens_usage=tokens_usage,
        time_elapsed=1.5,
        conversation_id="conv-1",
        llm_model="test-model",
        status=ConversationStatus.SUCCESS,
        client_source=client_source,
    )

    _, call_kwargs = mock_send_count_metric.call_args
    assert call_kwargs["attributes"][MetricsAttributes.CLIENT_SOURCE] == expected_value
