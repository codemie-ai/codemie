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

from unittest.mock import Mock

from langchain_core.tools import BaseTool

from codemie.service.tools.tool_execution_service import ToolExecutionService


def test_update_tool_attributes_empty():
    """Test updating tool attributes with empty attributes dict."""
    # Setup mock
    tool = Mock(spec=BaseTool)

    # Empty attributes
    tool_attributes = {}

    # Update
    result = ToolExecutionService.update_tool_attributes(tool, tool_attributes)

    # Assert original tool returned without validation
    # No validation should be called since attributes is empty
    assert result == tool
