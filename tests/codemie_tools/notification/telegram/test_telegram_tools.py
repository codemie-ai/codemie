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

from codemie_tools.notification.telegram.tools import TelegramTool, TelegramConfig


class TestTelegramTool(unittest.TestCase):
    def setUp(self):
        self.bot_token = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
        self.telegram_config = TelegramConfig(token=self.bot_token)
        self.telegram_tool = TelegramTool(config=self.telegram_config)

    def test_execute_without_config(self):
        # Note: We can no longer create a tool with config=None due to pydantic validation
        # Instead, we'll test by temporarily setting config to None
        original_config = self.telegram_tool.config
        try:
            # Type: ignore for type checking
            self.telegram_tool.config = None  # type: ignore
            with self.assertRaises(ValueError) as context:
                self.telegram_tool.execute("GET", "/sendMessage")
            self.assertEqual(
                str(context.exception), "Telegram config is provided set. Please set it before using the tool."
            )
        finally:
            # Restore the config
            self.telegram_tool.config = original_config

    @patch('requests.request')
    def test_execute_get_method(self, mock_request):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = '{"ok":true,"result":{}}'
        mock_request.return_value = mock_response

        method = "GET"
        relative_url = "/getMe"
        response = self.telegram_tool.execute(method, relative_url)

        self.assertEqual(response, '{"ok":true,"result":{}}')
        mock_request.assert_called_once_with(
            method,
            f"https://api.telegram.org/bot{self.bot_token}/getMe",
            headers={'Content-Type': 'application/json'},
            json={},
        )

    @patch('requests.request')
    def test_execute_post_method(self, mock_request):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = '{"ok":true,"result":{}}'
        mock_request.return_value = mock_response

        method = "POST"
        relative_url = "/sendMessage"
        params = '{"chat_id": "123456", "text": "Hello, World!"}'
        response = self.telegram_tool.execute(method, relative_url, params)

        self.assertEqual(response, '{"ok":true,"result":{}}')
        mock_request.assert_called_once_with(
            method,
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            headers={'Content-Type': 'application/json'},
            json={'chat_id': '123456', 'text': 'Hello, World!'},
        )

    def test_parse_payload_params(self):
        params = '{"chat_id": "123456", "text": "Hello, World!"}'
        expected_result = {'chat_id': '123456', 'text': 'Hello, World!'}
        result = self.telegram_tool._parse_payload_params(params)
        self.assertEqual(result, expected_result)

    def test_parse_payload_params_empty(self):
        params = ""
        expected_result = {}
        result = self.telegram_tool._parse_payload_params(params)
        self.assertEqual(result, expected_result)

    def test_empty_config(self):
        # Arrange - create config with empty token (default value)
        config = TelegramConfig(token="")
        tool = TelegramTool(config=config)

        # Act & Assert - should raise ValueError with empty token
        with self.assertRaises(ValueError) as context:
            tool.execute("GET", "/getMe")

        self.assertIn("Telegram token is not set", str(context.exception))
