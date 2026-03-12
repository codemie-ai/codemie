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

import pytest
import asyncio
from unittest.mock import patch, MagicMock
from codemie.triggers.node_controller import NodeController, State


@pytest.fixture
def mock_elastic_client(mocker):
    return mocker.patch('codemie.triggers.node_controller.State.elastic_client')


def test_enable_trigger_active_node_index_exists(mock_elastic_client):
    # Mock indices.exists to return True
    mock_elastic_client.indices.exists.return_value = True
    state = State()
    state.enable_trigger_active_node_index()
    mock_elastic_client.indices.create.assert_not_called()


def test_enable_trigger_active_node_index_not_exists(mock_elastic_client):
    # Mock indices.exists to return False
    mock_elastic_client.indices.exists.return_value = False
    state = State()
    state.enable_trigger_active_node_index()
    mock_elastic_client.indices.create.assert_called_once_with(index=state._index)


@pytest.mark.asyncio
@patch('codemie.triggers.node_controller.State', autospec=True)
@patch('codemie.triggers.node_controller.AsyncIOScheduler', autospec=True)
async def test_start(mock_scheduler, mock_state):
    # Arrange
    mock_state = mock_state.return_value
    mock_state.id = 'test_node_id'
    mock_state.is_active = True
    mock_scheduler = mock_scheduler.return_value
    mock_scheduler.add_job = MagicMock()
    node_controller = NodeController()
    node_controller.state = mock_state

    # Act
    start_task = asyncio.create_task(node_controller.start())
    try:
        await asyncio.wait_for(start_task, timeout=1.0)
    except asyncio.TimeoutError:
        return

    # Assert
    mock_scheduler.add_job.assert_called()
