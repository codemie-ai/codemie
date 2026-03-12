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

import unittest
from unittest.mock import patch, Mock

from langchain_core.tools import ToolException

from codemie_tools.itsm.servicenow.models import ServiceNowConfig
from codemie_tools.itsm.servicenow.tools import ServiceNowTableTool

MOCK_RESPONSE = '{"result": "ok"}'


class TestServiceNowTableTool(unittest.TestCase):
    @patch('codemie_tools.itsm.servicenow.tools.requests.request')
    def test_execute_success(self, mock_request):
        mock_request.return_value = Mock(status_code=200, text=MOCK_RESPONSE)
        tool = ServiceNowTableTool(
            config=ServiceNowConfig(url='https://example.local', api_key='fake_api_key'),
        )

        response = tool.execute(table="incident", method="GET")
        self.assertEqual(response, MOCK_RESPONSE)

    @patch('codemie_tools.itsm.servicenow.tools.requests.request')
    def test_execute_error(self, mock_request):
        mock_request.return_value = Mock(status_code=400, text=MOCK_RESPONSE)
        tool = ServiceNowTableTool(
            config=ServiceNowConfig(url='https://example.local', api_key='fake_api_key'),
        )

        with self.assertRaises(ToolException) as ex:
            tool.execute(table="incident", method="GET")

        self.assertEqual(str(ex.exception), f"ServiceNow tool exception. Status: 400. Response: {MOCK_RESPONSE}")

    @patch('codemie_tools.itsm.servicenow.tools.requests.request')
    def test_healthcheck(self, mock_request):
        mock_request.return_value = Mock(status_code=200, text=MOCK_RESPONSE)
        tool = ServiceNowTableTool(
            config=ServiceNowConfig(url='https://example.local', api_key='fake_api_key'),
        )

        tool._healthcheck()
        mock_request.assert_called_once()

    @patch('codemie_tools.itsm.servicenow.tools.requests.request')
    def test_healthcheck_failure(self, mock_request):
        mock_request.return_value = Mock(status_code=400, text=MOCK_RESPONSE)
        tool = ServiceNowTableTool(
            config=ServiceNowConfig(url='https://example.local', api_key='fake_api_key'),
        )

        with self.assertRaises(ToolException):
            tool._healthcheck()

    def test_empty_config(self):
        # Arrange - create config with empty api_key (default value)
        tool = ServiceNowTableTool(
            config=ServiceNowConfig(
                url='https://example.local',
                api_key='',  # Empty api_key
            ),
        )

        # Act - invoke() returns error message string when handle_tool_error=True
        # Use invoke() instead of execute() to go through _run() which validates
        result = tool.invoke({"table": "incident", "method": "GET"})

        # Assert - should return error message string (not raise exception)
        self.assertIn("Tool config is not set", result)
