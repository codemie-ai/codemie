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

from unittest.mock import Mock, patch

from langchain_core.tools import BaseTool

from codemie.service.tools.tool_execution_service import (
    ToolExecutionService,
)


def test_validate_tool_args_valid():
    """Test validating tool args with valid arguments."""
    # Setup mock
    tool = Mock(spec=BaseTool)
    schema_mock = Mock()
    schema_mock.__annotations__ = {'arg1': str, 'arg2': int}
    tool.args_schema = schema_mock

    # Valid args
    tool_args = {"arg1": "test", "arg2": 123}

    # Validate with patch to avoid calling the actual schema constructor
    with patch.object(tool.args_schema, '__call__', return_value=None):  # Mock successful validation
        result = ToolExecutionService.validate_tool_args(tool, tool_args)

    # Assert
    assert result == tool


def test_validate_tool_attributes_valid():
    """Test validating tool attributes with valid attributes."""
    # Setup mock
    tool = Mock(spec=BaseTool)
    tool.__annotations__ = {"name": str, "description": str, "custom_attr": str}
    tool.model_validate = Mock(return_value=tool)

    # Valid tool attributes
    tool_attributes = {"custom_attr": "new value"}

    # Validate
    result = ToolExecutionService.validate_tool_attributes(tool, tool_attributes)

    # Assert the tool was validated
    tool.model_validate.assert_called_once()
    assert result == tool
