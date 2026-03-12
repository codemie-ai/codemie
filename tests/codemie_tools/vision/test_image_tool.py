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

from codemie_tools.base.file_object import FileObject
from codemie_tools.vision.image_tool import ImageTool
from codemie_tools.vision.tool_vars import IMAGE_TOOL


def test_image_tool_initialization():
    file = FileObject(name="test_image.png", mime_type="image/png", owner="owner")
    file.content = b"image_content"
    tool = ImageTool(files=[file], chat_model='gpt-4o')
    assert tool.name == IMAGE_TOOL.name
    assert tool.label == IMAGE_TOOL.label
    assert tool.description == IMAGE_TOOL.description
    assert len(tool.files) == 1


def test_image_tool_execute_with_single_file():
    mock_chat_model = MagicMock()
    result = MagicMock()
    result.content = "Description of the image"
    mock_chat_model.invoke.return_value = result

    # Create mock file
    mock_file = MagicMock(spec=FileObject)
    mock_file.name = "test_image.png"
    mock_file.mime_type = "image/png"
    mock_file.to_image_base64 = MagicMock(return_value=b"image_content")

    tool = ImageTool(files=[mock_file], chat_model=mock_chat_model)
    query = "Describe this image"

    result = tool.execute(query=query)
    assert result == "Description of the image"
    mock_chat_model.invoke.assert_called_once()
    mock_file.to_image_base64.assert_called_once()


def test_image_tool_execute_with_multiple_files():
    mock_chat_model = MagicMock()

    # Setup return values for multiple calls
    result1 = MagicMock()
    result1.content = "Description of image 1"
    result2 = MagicMock()
    result2.content = "Description of image 2"
    mock_chat_model.invoke.side_effect = [result1, result2]

    # Create mock files
    mock_file1 = MagicMock(spec=FileObject)
    mock_file1.name = "test_image1.png"
    mock_file1.mime_type = "image/png"
    mock_file1.to_image_base64 = MagicMock(return_value=b"image_content_1")

    mock_file2 = MagicMock(spec=FileObject)
    mock_file2.name = "test_image2.jpg"
    mock_file2.mime_type = "image/jpeg"
    mock_file2.to_image_base64 = MagicMock(return_value=b"image_content_2")

    tool = ImageTool(files=[mock_file1, mock_file2], chat_model=mock_chat_model)
    query = "Describe these images"

    result = tool.execute(query=query)

    # Check the concatenated output format
    assert "### IMAGE 1: test_image1.png ###" in result
    assert "Description of image 1" in result
    assert "### IMAGE 2: test_image2.jpg ###" in result
    assert "Description of image 2" in result

    # Verify interactions
    assert mock_chat_model.invoke.call_count == 2
    mock_file1.to_image_base64.assert_called_once()
    mock_file2.to_image_base64.assert_called_once()


def test_image_tool_execute_fails_without_files():
    tool = ImageTool(files=[], chat_model=MagicMock())

    try:
        tool.execute(query="test")
        raise AssertionError("Should have raised ValueError")
    except ValueError as e:
        assert "No files provided" in str(e)
