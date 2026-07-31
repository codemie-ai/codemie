# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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
import io
import json
import unittest
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

from PIL import Image

from codemie.core.exceptions import ValidationException
from codemie.rest_api.security.user import User
from codemie.service.agent_workspace_service import AgentWorkspaceService
from codemie_tools.data_management.workspace.generate_image_tool_v2 import (
    GenerateWorkspaceImageToolV2,
    build_image_generation_plan,
    build_workspace_image_path,
    calculate_target_dimensions,
    create_base_and_mask_images,
    find_closest_gemini_aspect_ratio,
    normalize_generated_image_bytes,
    parse_size_request,
    parse_size_string,
)
from codemie_tools.data_management.workspace.toolkit import AgentWorkspaceToolkit


def _make_png_bytes(size: tuple[int, int] = (2048, 2048), color: str = "white") -> bytes:
    image = Image.new("RGB", size, color=color)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class TestGenerateWorkspaceImageToolV2Helpers(unittest.TestCase):
    def test_parse_size_string(self):
        self.assertEqual(parse_size_string("1920x1080"), (1920.0, 1080.0))

    def test_parse_size_string_invalid(self):
        with self.assertRaises(ValidationException):
            parse_size_string("bad-size")

    def test_parse_size_request_marks_ratio_inputs(self):
        parsed = parse_size_request("5:12")
        self.assertEqual((parsed.width, parsed.height), (5.0, 12.0))
        self.assertTrue(parsed.is_ratio)

    def test_calculate_target_dimensions_landscape(self):
        self.assertEqual(calculate_target_dimensions(1920, 1080), (1536, 864))

    def test_calculate_target_dimensions_square_upscales_to_minimum_size(self):
        self.assertEqual(calculate_target_dimensions(512, 512), (1024, 1024))

    def test_build_workspace_image_path_uses_timestamp(self):
        now = datetime(2026, 5, 6, 12, 30, 45, 123456, tzinfo=timezone.utc)
        self.assertEqual(build_workspace_image_path(now), "images/20260506T123045.123456Z.png")

    @patch("codemie_tools.data_management.workspace.generate_image_tool_v2.requests.get")
    def test_normalize_generated_image_bytes_downloads_remote_url(self, mock_get):
        response = MagicMock()
        response.content = b"remote-image"
        mock_get.return_value = response

        result = normalize_generated_image_bytes(url="https://example.com/image.png", b64_data=None)

        self.assertEqual(result, b"remote-image")
        mock_get.assert_called_once()

    def test_normalize_generated_image_bytes_from_b64(self):
        encoded = base64.b64encode(b"png-bytes").decode("ascii")
        result = normalize_generated_image_bytes(url=None, b64_data=encoded)
        self.assertEqual(result, b"png-bytes")

    def test_create_base_and_mask_images_uses_canvas_and_target_dimensions(self):
        base_bytes, mask_bytes = create_base_and_mask_images(
            canvas_width=1536,
            canvas_height=1024,
            target_width=1200,
            target_height=800,
            background="light",
        )

        with Image.open(io.BytesIO(base_bytes)) as base_image:
            self.assertEqual(base_image.size, (1536, 1024))

        with Image.open(io.BytesIO(mask_bytes)) as mask_image:
            self.assertEqual(mask_image.size, (1536, 1024))
            left = (1536 - 1200) // 2
            top = (1024 - 800) // 2
            pixel: Any = mask_image.getpixel((left, top))
            self.assertEqual(pixel, (255, 255, 255, 0))

    def test_find_closest_gemini_aspect_ratio(self):
        self.assertEqual(find_closest_gemini_aspect_ratio(1920, 1080), "16:9")

    def test_build_image_generation_plan_for_gemini_exact_size(self):
        generator = MagicMock()
        generator.model_id = "gemini-3.1-flash-image"

        plan = build_image_generation_plan(parse_size_request("1920x1080"), generator)

        self.assertIsNone(plan.size)
        self.assertIsNone(plan.output_format)
        self.assertTrue(plan.preserve_generated_dimensions)
        self.assertEqual((plan.target_width, plan.target_height), (1376, 768))
        self.assertEqual(
            plan.extra_body,
            {"response_format": {"image": {"aspect_ratio": "16:9", "image_size": "1K"}}},
        )

    def test_build_image_generation_plan_for_gemini_ratio_input(self):
        generator = MagicMock()
        generator.model_id = "gemini-3.1-flash-image"

        plan = build_image_generation_plan(parse_size_request("5:12"), generator)

        self.assertEqual((plan.target_width, plan.target_height), (768, 1376))
        self.assertEqual(
            plan.extra_body,
            {"response_format": {"image": {"aspect_ratio": "9:16", "image_size": "1K"}}},
        )


class TestGenerateWorkspaceImageToolV2(unittest.TestCase):
    @staticmethod
    def _build_workspace_service() -> AgentWorkspaceService:
        service = object.__new__(AgentWorkspaceService)
        service.get_workspace = MagicMock(return_value=MagicMock(id="workspace-1"))
        service.upsert_binary_file = MagicMock(return_value=MagicMock(path="images/test.png"))
        service.get_file_sandbox_url = MagicMock(return_value="sandbox:/v1/files/encoded")
        return service

    def _build_tool(self, image_generator=None, workspace_service=None) -> GenerateWorkspaceImageToolV2:
        return GenerateWorkspaceImageToolV2(
            conversation_id="conversation-1",
            user=User(id="user-1", auth_token=None),
            workspace_service=workspace_service or self._build_workspace_service(),
            workspace_id="workspace-1",
            image_generator=image_generator,
        )

    def test_execute_raises_when_generator_missing(self):
        tool = self._build_tool(image_generator=None)
        with self.assertRaises(ValidationException):
            tool.execute(image_description="A lake", size="1024x768")

    def test_execute_saves_workspace_file_and_returns_links(self):
        generator = MagicMock()
        generator.edit.return_value = (None, base64.b64encode(_make_png_bytes()).decode("ascii"))
        workspace_service = self._build_workspace_service()

        tool = self._build_tool(image_generator=generator, workspace_service=workspace_service)
        result = json.loads(tool.execute(image_description="A lake", size="1920x1080"))

        self.assertEqual(result["workspace_path"].split("/")[0], "images")
        self.assertEqual(result["sandbox_url"], "sandbox:/v1/files/encoded")
        self.assertEqual(result["width"], 1536)
        self.assertEqual(result["height"], 864)

        saved_content = workspace_service.upsert_binary_file.call_args.args[2]
        with Image.open(io.BytesIO(saved_content)) as image:
            self.assertEqual(image.size, (1536, 864))

    def test_execute_uses_default_background(self):
        generator = MagicMock()
        generator.edit.return_value = (None, base64.b64encode(_make_png_bytes((1024, 1024))).decode("ascii"))
        workspace_service = self._build_workspace_service()

        tool = self._build_tool(image_generator=generator, workspace_service=workspace_service)
        tool.execute(image_description="A lake", size="800x600")

        base_image_bytes = generator.edit.call_args.kwargs["image"]
        with Image.open(io.BytesIO(base_image_bytes)) as image:
            self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))

    def test_execute_prefers_inpainting_when_edit_supported(self):
        generator = MagicMock()
        generator.edit.return_value = (None, base64.b64encode(_make_png_bytes((1536, 1024))).decode("ascii"))
        workspace_service = self._build_workspace_service()

        tool = self._build_tool(image_generator=generator, workspace_service=workspace_service)
        result = json.loads(tool.execute(image_description="A lake", size="1920x1080"))

        self.assertEqual(result["width"], 1536)
        self.assertEqual(result["height"], 864)
        generator.edit.assert_called_once()
        edit_kwargs = generator.edit.call_args.kwargs
        self.assertEqual(edit_kwargs["size"], "1536x1024")
        self.assertEqual(edit_kwargs["output_format"], "png")

    def test_execute_uses_gemini_native_response_format(self):
        generator = MagicMock()
        generator.model_id = "gemini-3.1-flash-image"
        generator.edit.return_value = (None, base64.b64encode(_make_png_bytes((1376, 768))).decode("ascii"))
        workspace_service = self._build_workspace_service()

        tool = self._build_tool(image_generator=generator, workspace_service=workspace_service)
        result = json.loads(tool.execute(image_description="A lake", size="1920x1080"))

        self.assertEqual(result["width"], 1376)
        self.assertEqual(result["height"], 768)
        edit_kwargs = generator.edit.call_args.kwargs
        self.assertNotIn("size", edit_kwargs)
        self.assertNotIn("output_format", edit_kwargs)
        self.assertEqual(
            edit_kwargs["extra_body"],
            {"response_format": {"image": {"aspect_ratio": "16:9", "image_size": "1K"}}},
        )


class TestAgentWorkspaceToolkit(unittest.TestCase):
    @patch("codemie_tools.data_management.workspace.toolkit.AgentWorkspaceService")
    def test_get_tools_includes_generate_workspace_image_v2(self, mock_service_cls):
        service = object.__new__(AgentWorkspaceService)
        service.get_workspace = MagicMock(return_value=MagicMock(id="workspace-1"))
        service.create_workspace = MagicMock(return_value=MagicMock(id="workspace-1"))
        mock_service_cls.return_value = service

        toolkit = AgentWorkspaceToolkit.get_toolkit(
            conversation_id="conversation-1",
            user=User(id="user-1", auth_token=None),
            image_generator=MagicMock(),
        )

        tools = toolkit.get_tools()
        assert any(tool.name == "generate_workspace_image_v2" for tool in tools)
