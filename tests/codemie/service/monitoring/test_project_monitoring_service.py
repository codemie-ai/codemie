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
from unittest.mock import patch

from codemie.service.monitoring.project_monitoring_service import ProjectMonitoringService
from codemie.rest_api.security.user import User


@pytest.fixture
def mock_user():
    return User(id='test_user_id', name='test_user_name')


@patch.object(ProjectMonitoringService, "send_count_metric")
def test_send_project_creation_metric(mock_send_count_metric, mock_user):
    ProjectMonitoringService.send_project_creation_metric(user=mock_user, project_name="Test Project")

    expected_attributes = {
        "user_id": mock_user.id,
        "user_name": mock_user.name,
        "project": "Test Project",
    }

    mock_send_count_metric.assert_any_call(
        name="create_project",
        attributes=expected_attributes,
    )
