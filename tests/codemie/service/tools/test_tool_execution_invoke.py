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

import pytest
from langchain_core.tools import BaseTool

from codemie.rest_api.models.tool import ToolInvokeRequest
from codemie.rest_api.security.user import User
from codemie.service.tools.tool_execution_service import ToolExecutionService


def test_invoke_with_direct_creds():
    """Test invoking tool with direct credentials."""
    # Setup
    mock_request = Mock(spec=ToolInvokeRequest)
    mock_request.tool_creds = {"api_key": "test-key"}  # No integration_alias
    mock_user = Mock(spec=User)

    # Mock direct creds method and file dependency check
    with patch.object(ToolExecutionService, "_is_file_dependent_tool", return_value=False):
        with patch.object(
            ToolExecutionService, "invoke_tool_with_direct_creds", return_value="direct result"
        ) as mock_direct:
            result = ToolExecutionService.invoke(mock_request, "test-tool", mock_user)

    # Assert
    mock_direct.assert_called_once_with(mock_request, "test-tool")
    assert result == "direct result"


def test_invoke_with_system_integration():
    """Test invoking tool with system integration."""
    # Setup
    mock_request = Mock(spec=ToolInvokeRequest)
    mock_request.tool_creds = {"integration_alias": "test-integration"}
    mock_user = Mock(spec=User)

    # Mock system integration method and file dependency check
    with patch.object(ToolExecutionService, "_is_file_dependent_tool", return_value=False):
        with patch.object(
            ToolExecutionService, "invoke_tool_with_system_integration", return_value="system result"
        ) as mock_system:
            result = ToolExecutionService.invoke(mock_request, "test-tool", mock_user)

    # Assert
    mock_system.assert_called_once_with(mock_request, "test-tool", mock_user)
    assert result == "system result"


def test_invoke_tool_with_direct_creds_success():
    """Test successful tool invocation with direct credentials."""
    # Setup
    mock_request = Mock(spec=ToolInvokeRequest)
    mock_request.tool_args = {"arg1": "value"}
    mock_request.tool_attributes = {"attr1": "value"}

    mock_tool = Mock(spec=BaseTool)
    mock_tool.execute = Mock(return_value="Success result")

    # Mock required methods
    with patch.object(ToolExecutionService, "get_tool_with_direct_creds", return_value=mock_tool) as mock_get_tool:
        with patch.object(ToolExecutionService, "validate_tool_args", return_value=mock_tool) as mock_validate:
            with patch.object(ToolExecutionService, "update_tool_attributes", return_value=mock_tool) as mock_update:
                with patch('codemie.service.tools.tool_execution_service.logger'):  # Mock logger
                    result = ToolExecutionService.invoke_tool_with_direct_creds(mock_request, "test-tool")

    # Assert
    mock_get_tool.assert_called_once_with(mock_request, "test-tool")
    mock_validate.assert_called_once_with(mock_tool, mock_request.tool_args)
    mock_update.assert_called_once_with(mock_tool, mock_request.tool_attributes)
    mock_tool.execute.assert_called_once_with(**mock_request.tool_args)
    assert result == "Success result"


def test_invoke_tool_with_direct_creds_error():
    """Test error handling during tool invocation with direct credentials."""
    # Setup
    mock_request = Mock(spec=ToolInvokeRequest)
    mock_request.tool_args = {"arg1": "value"}
    mock_request.tool_attributes = {"attr1": "value"}

    mock_tool = Mock(spec=BaseTool)
    mock_tool.execute = Mock(side_effect=ValueError("Tool execution error"))

    # Mock required methods with error condition
    with patch.object(ToolExecutionService, "get_tool_with_direct_creds", return_value=mock_tool):
        with patch.object(ToolExecutionService, "validate_tool_args", return_value=mock_tool):
            with patch.object(ToolExecutionService, "update_tool_attributes", return_value=mock_tool):
                with patch('codemie.service.tools.tool_execution_service.logger') as mock_logger:
                    # Expect exception to be re-raised
                    with pytest.raises(ValueError) as excinfo:
                        ToolExecutionService.invoke_tool_with_direct_creds(mock_request, "test-tool")

    # Assert
    assert "Tool execution error" in str(excinfo.value)
    mock_logger.error.assert_called_once()
    assert "Error occurred on tool invocation" in mock_logger.error.call_args[0][0]
