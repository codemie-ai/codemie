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
from unittest.mock import MagicMock, patch

from codemie.service.background_tasks_service import BackgroundTasksService
from codemie.core.models import BackgroundTaskRequest


@pytest.fixture
def service():
    return BackgroundTasksService()


@patch('codemie.service.background_tasks_service.BackgroundTasks')
def test_save(mock_background_tasks, service):
    task = MagicMock(
        task='test_task', user='test_user', status='test_status', assistant='test_assistant', spec=BackgroundTaskRequest
    )

    result = service.save(task)

    assert mock_background_tasks.called
    assert isinstance(result, str)


@patch('codemie.service.background_tasks_service.BackgroundTasks')
def test_update(mock_background_tasks, service):
    service.update(
        task_id='test_task_id', status='test_status', current_step='test_current_step', final_output='test_final_output'
    )

    assert mock_background_tasks.get_by_id.called
    assert mock_background_tasks.get_by_id.return_value.update.called


@patch('codemie.service.background_tasks_service.BackgroundTasks')
def test_get_task(mock_background_tasks, service):
    task_id = 'test_task_id'
    result = service.get_task(task_id)
    mock_background_tasks.get_by_id.assert_called_once_with(task_id)

    assert mock_background_tasks.get_by_id.called
    assert result == mock_background_tasks.get_by_id.return_value
