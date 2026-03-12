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

from codemie_tools.base.file_object import FileObject
from codemie_tools.vision.toolkit import VisionToolkit


def test_get_tools_ui_info():
    ui_info = VisionToolkit.get_tools_ui_info()
    expected_info = {"toolkit": "Vision", "tools": [{"name": "image_tool", "label": "Image Recognition"}]}
    assert ui_info['toolkit'] == expected_info['toolkit']
    assert len(ui_info['tools']) == len(expected_info['tools'])
    for tool_info in ui_info['tools']:
        assert tool_info['name'] in [tool['name'] for tool in expected_info['tools']]
        assert tool_info['label'] in [tool['label'] for tool in expected_info['tools']]


def test_get_tools_with_files():
    # Create files with image mime types

    mock_file1 = FileObject(name="test_image1.png", mime_type="image/png", owner="owner")
    mock_file1.content = b"test_image_content1"

    mock_file2 = FileObject(name="test_image2.jpg", mime_type="image/jpeg", owner="owner")
    mock_file2.content = b"test_image_content2"

    # Create a non-image file that should be filtered out
    mock_file3 = FileObject(name="test.txt", mime_type="text/plain", owner="owner")
    mock_file3.content = "test content"

    files = [mock_file1, mock_file2, mock_file3]

    # Test with files
    tools = VisionToolkit.get_toolkit(files=files).get_tools()
    assert len(tools) == 1
    assert len(tools[0].files) == 2  # Only image files should be included
    assert tools[0].files[0] == mock_file1
    assert tools[0].files[1] == mock_file2


def test_get_tools_no_files():
    # Test with no files
    tools = VisionToolkit.get_toolkit(files=[]).get_tools()
    assert len(tools) == 0
