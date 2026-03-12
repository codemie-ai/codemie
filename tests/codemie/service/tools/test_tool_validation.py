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

"""
Test for validation methods in ToolExecutionService.
"""

from unittest.mock import Mock

import pytest
from langchain_core.tools import BaseTool

from codemie.service.tools.tool_execution_service import ToolExecutionService


@pytest.fixture
def mock_tool():
    """Create a mock tool for testing."""
    tool = Mock(spec=BaseTool)
    tool.name = "test_tool"
    tool.description = "Test tool"
    return tool


def test_validate_tool_args_valid(mock_tool):
    """Test validating tool args with valid args."""
    # Setup schema
    mock_tool.args_schema = Mock()
    mock_tool.args_schema.__annotations__ = {"arg1": str, "arg2": int}
    # Mock successful validation
    mock_tool.args_schema.side_effect = lambda **kwargs: None

    # Valid args
    args = {"arg1": "test", "arg2": 123}

    # Test
    result = ToolExecutionService.validate_tool_args(mock_tool, args)

    # Verify
    assert result == mock_tool


def test_update_tool_attributes_empty(mock_tool):
    """Test updating tool attributes with empty attributes."""
    # Empty attributes
    attributes = {}

    # Test
    result = ToolExecutionService.update_tool_attributes(mock_tool, attributes)

    # Verify original tool returned
    assert result == mock_tool
