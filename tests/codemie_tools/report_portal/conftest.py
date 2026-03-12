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

from unittest.mock import MagicMock

import pytest

from codemie_tools.report_portal.report_portal_client import ReportPortalClient
from codemie_tools.report_portal.tools import BaseReportPortalTool


@pytest.fixture
def mock_report_portal_client():
    """Create a mock Report Portal client."""
    client = MagicMock(spec=ReportPortalClient)
    # Setup common return values
    client.get_all_launches.return_value = {'content': [{'id': 'launch-123'}]}
    client.get_launch_details.return_value = {'id': 'launch-123', 'name': 'Test Launch'}
    client.find_test_item_by_id.return_value = {'id': 'item-123', 'name': 'Test Item'}
    client.get_test_items_for_launch.return_value = {'content': [{'id': 'item-123'}]}
    client.get_logs_for_test_items.return_value = {'content': [{'log': 'log entry'}]}
    client.get_user_information.return_value = {'username': 'testuser', 'email': 'test@example.com'}
    client.get_dashboard_data.return_value = {'id': 'dashboard-123', 'name': 'Test Dashboard'}

    return client


@pytest.fixture
def mock_base_report_portal_tool():
    """Create a mock Base Report Portal tool with client."""
    tool = MagicMock(spec=BaseReportPortalTool)
    tool.endpoint = 'http://reportportal.example.com'
    tool.api_key = 'test_api_key'
    tool.project = 'test_project'
    tool._client = MagicMock(spec=ReportPortalClient)
    return tool
