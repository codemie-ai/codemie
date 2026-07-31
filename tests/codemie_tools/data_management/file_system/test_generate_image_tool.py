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

import base64
import unittest
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage

from codemie.configs import config
from codemie_tools.data_management.file_system.image_generator import (
    ChatModelImageGenerator,
    LiteLLMImageConfig,
    LiteLLMImageGenerator,
)
from codemie_tools.data_management.file_system.generate_image_tool import (
    GenerateImageTool,
    GenerateImagesToolInput,
)

_CONFIG = LiteLLMImageConfig(
    api_base="https://litellm.example.com",
    api_key="test-key",
    api_version="2025-04-01-preview",
    model_id=config.IMAGE_GENERATION_MODEL,
)
_IMAGE_URL = "https://example.com/image.png"
_B64_DATA = base64.b64encode(b"fake-png-bytes").decode()
_DATA_URL = f"data:image/png;base64,{_B64_DATA}"


class TestGenerateImageToolNoConfig(unittest.TestCase):
    def test_execute_no_generator_raises(self):
        tool = GenerateImageTool()
        with self.assertRaises(ValueError) as ctx:
            tool.execute(image_description="A beautiful landscape.")
        self.assertIn("not configured", str(ctx.exception))


class TestLiteLLMImageGenerator(unittest.TestCase):
    """Unit tests for LiteLLMImageGenerator."""

    @patch("codemie_tools.data_management.file_system.image_generator.AzureOpenAI")
    def test_generate_returns_url(self, mock_azure_cls):
        item = MagicMock(url=_IMAGE_URL, b64_json=None)
        mock_azure_cls.return_value.images.generate.return_value.data = [item]

        gen = LiteLLMImageGenerator(_CONFIG)
        url, b64 = gen.generate("A sunrise.")

        self.assertEqual(url, _IMAGE_URL)
        self.assertIsNone(b64)

    @patch("codemie_tools.data_management.file_system.image_generator.AzureOpenAI")
    def test_generate_returns_b64(self, mock_azure_cls):
        item = MagicMock(url=None, b64_json=_B64_DATA)
        mock_azure_cls.return_value.images.generate.return_value.data = [item]

        gen = LiteLLMImageGenerator(_CONFIG)
        url, b64 = gen.generate("A landscape.")

        self.assertIsNone(url)
        self.assertEqual(b64, _B64_DATA)

    @patch("codemie_tools.data_management.file_system.image_generator.AzureOpenAI")
    def test_generate_normalises_data_url(self, mock_azure_cls):
        item = MagicMock(url=_DATA_URL, b64_json=None)
        mock_azure_cls.return_value.images.generate.return_value.data = [item]

        gen = LiteLLMImageGenerator(_CONFIG)
        url, b64 = gen.generate("A landscape.")

        self.assertIsNone(url)
        self.assertEqual(b64, _B64_DATA)

    @patch("codemie_tools.data_management.file_system.image_generator.AzureOpenAI")
    def test_client_built_with_correct_params(self, mock_azure_cls):
        item = MagicMock(url=_IMAGE_URL, b64_json=None)
        mock_azure_cls.return_value.images.generate.return_value.data = [item]

        LiteLLMImageGenerator(_CONFIG).generate("test")

        mock_azure_cls.assert_called_once_with(
            azure_endpoint=_CONFIG.api_base,
            api_key=_CONFIG.api_key,
            api_version=_CONFIG.api_version,
            timeout=_CONFIG.timeout,
        )

    @patch("codemie_tools.data_management.file_system.image_generator.AzureOpenAI")
    def test_generate_passes_size_and_output_format(self, mock_azure_cls):
        item = MagicMock(url=None, b64_json=_B64_DATA)
        mock_azure_cls.return_value.images.generate.return_value.data = [item]

        gen = LiteLLMImageGenerator(_CONFIG)
        gen.generate("A landscape.", size="1536x1024", output_format="png")

        mock_azure_cls.return_value.images.generate.assert_called_once_with(
            model=_CONFIG.model_id,
            prompt="A landscape.",
            n=1,
            size="1536x1024",
            output_format="png",
        )

    @patch("codemie_tools.data_management.file_system.image_generator.AzureOpenAI")
    def test_edit_passes_extra_body(self, mock_azure_cls):
        item = MagicMock(url=None, b64_json=_B64_DATA)
        mock_azure_cls.return_value.images.edit.return_value.data = [item]

        gen = LiteLLMImageGenerator(_CONFIG)
        gen.edit(
            prompt="Edit this image.",
            image=b"image-bytes",
            extra_body={"response_format": {"image": {"aspect_ratio": "16:9", "image_size": "1K"}}},
        )

        edit_call = mock_azure_cls.return_value.images.edit.call_args.kwargs
        self.assertEqual(
            edit_call["extra_body"],
            {"response_format": {"image": {"aspect_ratio": "16:9", "image_size": "1K"}}},
        )

    @patch("codemie_tools.data_management.file_system.image_generator.AzureOpenAI")
    def test_edit_passes_image_mask_size_and_output_format(self, mock_azure_cls):
        item = MagicMock(url=None, b64_json=_B64_DATA)
        mock_azure_cls.return_value.images.edit.return_value.data = [item]

        gen = LiteLLMImageGenerator(_CONFIG)
        url, b64 = gen.edit(
            prompt="Edit this image.",
            image=b"image-bytes",
            mask=b"mask-bytes",
            size="1536x1024",
            output_format="png",
        )

        self.assertIsNone(url)
        self.assertEqual(b64, _B64_DATA)
        edit_call = mock_azure_cls.return_value.images.edit.call_args.kwargs
        self.assertEqual(edit_call["model"], _CONFIG.model_id)
        self.assertEqual(edit_call["prompt"], "Edit this image.")
        self.assertEqual(edit_call["size"], "1536x1024")
        self.assertEqual(edit_call["output_format"], "png")
        self.assertEqual(edit_call["image"][0], "image.png")
        self.assertEqual(edit_call["mask"][0], "mask.png")


class TestChatModelImageGenerator(unittest.TestCase):
    """Unit tests for ChatModelImageGenerator."""

    def _make_model(self, content):
        model = MagicMock()
        model.invoke.return_value = AIMessage(content=content)
        return model

    def test_generate_image_url(self):
        gen = ChatModelImageGenerator(self._make_model([{"type": "image_url", "image_url": {"url": _IMAGE_URL}}]))
        url, b64 = gen.generate("A mountain.")
        self.assertEqual(url, _IMAGE_URL)
        self.assertIsNone(b64)

    def test_generate_data_url_normalised(self):
        gen = ChatModelImageGenerator(self._make_model([{"type": "image_url", "image_url": {"url": _DATA_URL}}]))
        url, b64 = gen.generate("A mountain.")
        self.assertIsNone(url)
        self.assertEqual(b64, _B64_DATA)

    def test_generate_inline_data(self):
        gen = ChatModelImageGenerator(self._make_model([{"type": "media", "data": _B64_DATA}]))
        url, b64 = gen.generate("A mountain.")
        self.assertIsNone(url)
        self.assertEqual(b64, _B64_DATA)

    def test_generate_no_image_returns_none_tuple(self):
        gen = ChatModelImageGenerator(self._make_model([]))
        url, b64 = gen.generate("A mountain.")
        self.assertIsNone(url)
        self.assertIsNone(b64)

    def test_edit_not_supported(self):
        gen = ChatModelImageGenerator(self._make_model([]))
        with self.assertRaises(NotImplementedError):
            gen.edit("prompt", b"image-bytes")

    def test_model_id_defaults_to_model_name(self):
        model = self._make_model([])
        model.model_name = "gemini-3.1-flash-image-preview"
        gen = ChatModelImageGenerator(model)
        self.assertEqual(gen.model_id, "gemini-3.1-flash-image-preview")


class TestGenerateImageToolResolveOutput(unittest.TestCase):
    """Integration tests for GenerateImageTool with mocked generators."""

    def _tool_with_generator(self, url=None, b64=None, **kwargs):
        gen = MagicMock()
        gen.generate.return_value = (url, b64)
        return GenerateImageTool(image_generator=gen, **kwargs)

    def test_returns_url(self):
        tool = self._tool_with_generator(url=_IMAGE_URL)
        self.assertEqual(tool.execute(**GenerateImagesToolInput(image_description="test").model_dump()), _IMAGE_URL)

    def test_raises_when_b64_and_no_file_repository(self):
        tool = self._tool_with_generator(b64=_B64_DATA)
        with self.assertRaises(ValueError) as ctx:
            tool.execute(image_description="test")
        self.assertIn("no file repository is configured", str(ctx.exception))

    def test_stores_b64_with_file_repository(self):
        mock_stored = MagicMock()
        mock_stored.to_encoded_url.return_value = "encoded-file-id"
        mock_repo = MagicMock()
        mock_repo.write_file.return_value = mock_stored

        tool = self._tool_with_generator(b64=_B64_DATA, file_repository=mock_repo, user_id="user-1")
        result = tool.execute(image_description="test")

        self.assertEqual(result, "sandbox:/v1/files/encoded-file-id")
        call_kwargs = mock_repo.write_file.call_args.kwargs
        self.assertEqual(call_kwargs["mime_type"], "image/png")
        self.assertEqual(call_kwargs["content"], base64.b64decode(_B64_DATA))
        self.assertEqual(call_kwargs["owner"], "user-1")

    def test_raises_when_no_image_data(self):
        tool = self._tool_with_generator()
        with self.assertRaises(ValueError) as ctx:
            tool.execute(image_description="test")
        self.assertIn("no image data", str(ctx.exception))
