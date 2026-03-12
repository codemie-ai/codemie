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
from unittest.mock import patch, MagicMock

from codemie_tools.qa.zephyr.tools import ZephyrGenericTool
from codemie_tools.qa.zephyr.models import ZephyrConfig


class TestZephyrGenericTool(unittest.TestCase):
    def setUp(self):
        self.tool = ZephyrGenericTool(config=ZephyrConfig(url="http://url", token='fake_token'))

    def test_no_zephyr_config(self):
        self.tool.config = None
        with self.assertRaises(ValueError):
            self.tool.execute("entity", "method")

    @patch('codemie_tools.qa.zephyr.tools.ZephyrScale')
    def test_url_correction(self, mock_zephyr):
        self.tool.execute("entity", "method")

        mock_zephyr.assert_called_with(base_url="http://url/", token="fake_token")

    @patch('codemie_tools.qa.zephyr.tools.ZephyrScale')
    def test_entity_dir_called(self, mock_zephyr):
        mock_zephyr.return_value.api.entity = MagicMock()
        mock_zephyr.return_value.api.entity.method = MagicMock()

        result = self.tool.execute("entity", "dir")

        self.assertIsInstance(result, list)
        self.assertIn('method', result)

    @patch('codemie_tools.qa.zephyr.tools.ZephyrScale')
    def test_method_with_body_called(self, mock_zephyr):
        mock_method = MagicMock(return_value="mocked response")
        mock_zephyr.return_value.api.entity.method = mock_method

        result = self.tool.execute("entity", "method", '{"key": "value"}')

        mock_method.assert_called_once_with(key="value")
        self.assertEqual(result, "mocked response")

    @patch('codemie_tools.qa.zephyr.tools.ZephyrScale')
    def test_generator_handling(self, mock_zephyr):
        mock_method = MagicMock(return_value=(x for x in range(3)))  # Simulate generator return
        mock_zephyr.return_value.api.entity.method = mock_method

        result = self.tool.execute("entity", "method")

        self.assertIsInstance(result, list)
        self.assertEqual(result, [0, 1, 2])
        self.assertEqual(mock_method.call_count, 1)
