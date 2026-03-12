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

import json
import unittest
from unittest.mock import patch

from codemie_tools.data_management.file_system.generate_image_tool import (
    GenerateImageTool,
    GenerateImagesToolInput,
    AzureDalleAIConfig,
)


class TestGenerateImageTool(unittest.TestCase):
    @patch('codemie_tools.data_management.file_system.generate_image_tool.AzureOpenAI')
    def test_execute_single_image(self, mock_azure_openai):
        mock_client = mock_azure_openai.return_value
        mock_client.images.generate.return_value.model_dump_json.return_value = json.dumps(
            {'data': [{'url': 'https://example.com/image.png'}]}
        )

        tool = GenerateImageTool(
            azure_dalle_config=AzureDalleAIConfig(
                api_version="test",
                api_key="key",
                azure_endpoint="test_endpoint",
            )
        )
        input_data = GenerateImagesToolInput(image_description="A beautiful sunrise over the mountains.")
        result = tool.execute(**input_data.model_dump())

        self.assertEqual(result, 'https://example.com/image.png')
        mock_client.images.generate.assert_called_once_with(
            model=tool.model_id, prompt=input_data.image_description, n=1
        )

    @patch('codemie_tools.data_management.file_system.generate_image_tool.AzureOpenAI')
    def test_execute_empty_description(self, mock_azure_openai):
        mock_client = mock_azure_openai.return_value
        mock_client.images.generate.return_value.model_dump_json.return_value = json.dumps(
            {'data': [{'url': 'https://example.com/image.png'}]}
        )

        tool = GenerateImageTool(
            azure_dalle_config=AzureDalleAIConfig(
                api_version="test",
                api_key="key",
                azure_endpoint="test_endpoint",
            )
        )
        input_data = GenerateImagesToolInput(image_description="")
        result = tool.execute(**input_data.model_dump())

        self.assertEqual(result, 'https://example.com/image.png')
        mock_client.images.generate.assert_called_once_with(
            model=tool.model_id, prompt=input_data.image_description, n=1
        )

    @patch('codemie_tools.data_management.file_system.generate_image_tool.AzureOpenAI')
    def test_execute_long_description(self, mock_azure_openai):
        mock_client = mock_azure_openai.return_value
        mock_client.images.generate.return_value.model_dump_json.return_value = json.dumps(
            {'data': [{'url': 'https://example.com/image.png'}]}
        )

        tool = GenerateImageTool(
            azure_dalle_config=AzureDalleAIConfig(
                api_version="test",
                api_key="key",
                azure_endpoint="test_endpoint",
            )
        )
        long_description = "A" * 1000  # Very long description
        input_data = GenerateImagesToolInput(image_description=long_description)
        result = tool.execute(**input_data.model_dump())

        self.assertEqual(result, 'https://example.com/image.png')
        mock_client.images.generate.assert_called_once_with(
            model=tool.model_id, prompt=input_data.image_description, n=1
        )

    @patch('codemie_tools.data_management.file_system.generate_image_tool.AzureOpenAI')
    def test_execute_error_response(self, mock_azure_openai):
        mock_client = mock_azure_openai.return_value
        mock_client.images.generate.side_effect = Exception("Service error")

        tool = GenerateImageTool(
            azure_dalle_config=AzureDalleAIConfig(
                api_version="test",
                api_key="key",
                azure_endpoint="test_endpoint",
            )
        )
        input_data = GenerateImagesToolInput(image_description="A beautiful sunrise over the mountains.")

        with self.assertRaises(Exception) as context:
            tool.execute(**input_data.model_dump())

        self.assertIn('Service error', str(context.exception))
        mock_client.images.generate.assert_called_once_with(
            model=tool.model_id, prompt=input_data.image_description, n=1
        )

    @patch('codemie_tools.data_management.file_system.generate_image_tool.AzureOpenAI')
    def test_execute_unexpected_response(self, mock_azure_openai):
        mock_client = mock_azure_openai.return_value
        mock_client.images.generate.return_value.model_dump_json.return_value = json.dumps(
            {'unexpected_key': 'unexpected_value'}
        )

        tool = GenerateImageTool(
            azure_dalle_config=AzureDalleAIConfig(
                api_version="test",
                api_key="key",
                azure_endpoint="test_endpoint",
            )
        )
        input_data = GenerateImagesToolInput(image_description="A beautiful sunrise over the mountains.")

        with self.assertRaises(KeyError) as context:
            tool.execute(**input_data.model_dump())

        self.assertIn('data', str(context.exception))
        mock_client.images.generate.assert_called_once_with(
            model=tool.model_id, prompt=input_data.image_description, n=1
        )
