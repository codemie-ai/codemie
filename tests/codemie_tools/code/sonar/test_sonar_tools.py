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
from unittest import mock

from codemie_tools.code.sonar.tools import SonarTool, ToolException, parse_payload_params
from codemie_tools.code.models import SonarConfig


class TestParsePayloadParams(unittest.TestCase):
    def test_parse_valid_json(self):
        """Test parsing of valid JSON."""
        valid_json = '{"key": "value"}'
        expected_result = {"key": "value"}
        self.assertEqual(parse_payload_params(valid_json), expected_result)

    def test_parse_invalid_json(self):
        """Test parsing of invalid JSON."""
        invalid_json = '{"key": "value"'
        with self.assertRaises(ToolException):
            parse_payload_params(invalid_json)

    def test_parse_none_params(self):
        """Test parsing when params is None."""
        self.assertEqual(parse_payload_params(None), {})


class TestSonarToolExecute(unittest.TestCase):
    @mock.patch('codemie_tools.code.sonar.tools.requests.get')
    def test_url_construction(self, mock_get):
        """Test URL construction in execute method."""
        mock_get.return_value.json.return_value = {"success": True}
        tool = SonarTool(config=SonarConfig(url='test', token="test", sonar_project_name='test'))
        tool.execute(relative_url='http://example.com/api', params='')
        mock_get.assert_called_once()
        self.assertIn('http://example.com/api', mock_get.call_args[1]['url'])

    @mock.patch('codemie_tools.code.sonar.tools.requests.get')
    def test_parameter_parsing(self, mock_get):
        """Test parameter parsing in execute method."""
        mock_get.return_value.json.return_value = {"success": True}
        tool = SonarTool(config=SonarConfig(url='test', token="test", sonar_project_name='test'))
        tool.execute(relative_url='', params='{"key": "value"}')
        self.assertEqual({'key': 'value', 'componentKeys': 'test'}, mock_get.call_args[1]['params'])

    @mock.patch('codemie_tools.code.sonar.tools.requests.get')
    def test_component_keys_adding(self, mock_get):
        """Test adding componentKeys in execute method."""
        mock_get.return_value.json.return_value = {"success": True}
        tool = SonarTool(config=SonarConfig(url='test', token="test", sonar_project_name='my_project'))
        tool.execute(relative_url='', params='{"componentKeys": "my_project"}')
        self.assertIn('my_project', mock_get.call_args[1]['params'].values())

    @mock.patch('codemie_tools.code.sonar.tools.requests.get')
    def test_authentication_handling(self, mock_get):
        """Test authentication handling in execute method."""
        mock_get.return_value.json.return_value = {"success": True}
        tool = SonarTool(config=SonarConfig(url='test', token="test", sonar_project_name='test'))
        tool.execute(relative_url='', params='')
        self.assertEqual(mock_get.call_args[1]['auth'], ('test', ''))

    @mock.patch('codemie_tools.code.sonar.tools.requests.get')
    def test_json_response_parsing(self, mock_get):
        """Test JSON response parsing in execute method."""
        expected_response = {"key": "value"}
        mock_get.return_value.json.return_value = expected_response
        tool = SonarTool(config=SonarConfig(url='test', token="test", sonar_project_name='test'))
        response = tool.execute(relative_url='', params='')
        self.assertEqual(response, expected_response)


if __name__ == '__main__':
    unittest.main()
