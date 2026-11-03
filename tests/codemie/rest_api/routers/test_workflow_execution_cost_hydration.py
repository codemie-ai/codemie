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

"""The execution GET behind the Info popup shows the hydrated usage, not the stored copy."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.core.models import TokensUsage
from codemie.rest_api.routers.workflow_executions import get_workflow_execution
from codemie.rest_api.security.user import User

STORED = TokensUsage(input_tokens=5, output_tokens=1, money_spent=0.0, money_spent_pending=True)
SETTLED = TokensUsage(input_tokens=5, output_tokens=1, money_spent=0.75)


@pytest.fixture
def workflow_service():
    execution = MagicMock()
    execution.model_dump.return_value = {
        "workflow_id": "wf-1",
        "execution_id": "exec-1",
        "tokens_usage": STORED.model_dump(),
    }
    with (
        patch("codemie.rest_api.routers.workflow_executions.WorkflowService") as service,
        patch("codemie.rest_api.routers.workflow_executions.Ability") as ability,
    ):
        ability.return_value.can.return_value = True
        service.return_value.find_workflow_execution_by_id.return_value = execution
        service.hydrate_execution_cost = AsyncMock(return_value=SETTLED)
        yield service, execution


def test_the_response_carries_the_hydrated_usage(workflow_service):
    service, execution = workflow_service

    response = asyncio.run(get_workflow_execution("exec-1", user=User(id="u-1")))

    assert response.tokens_usage == SETTLED
    service.hydrate_execution_cost.assert_awaited_once_with(execution)


def test_an_execution_without_usage_still_answers(workflow_service):
    service, _ = workflow_service
    service.hydrate_execution_cost.return_value = None

    response = asyncio.run(get_workflow_execution("exec-1", user=User(id="u-1")))

    assert response.tokens_usage is None
