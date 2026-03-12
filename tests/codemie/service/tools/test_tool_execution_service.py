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

from unittest.mock import patch, Mock

import pytest
from langchain_core.tools import BaseTool

from codemie.rest_api.models.index import IndexInfo
from codemie.rest_api.models.tool import ToolInvokeRequest, DatasourceSearchInvokeRequest
from codemie.rest_api.security.user import User
from codemie.service.tools.tool_execution_service import (
    ToolExecutionService,
)


def test_update_tool_attributes_empty():
    """Test updating tool attributes with empty attributes dict."""
    # Setup mock
    tool = Mock(spec=BaseTool)

    # Empty attributes
    attributes = {}

    # Call method
    result = ToolExecutionService.update_tool_attributes(tool, attributes)

    # Assert original returned unchanged
    assert result == tool


def test_invoke_with_direct_creds():
    """Test invoking tool with direct credentials."""
    # Setup
    mock_request = Mock(spec=ToolInvokeRequest)
    mock_request.tool_creds = {"api_key": "test-key"}  # No integration_alias
    mock_user = Mock(spec=User)

    # Mock direct creds method and file dependency check
    with patch.object(ToolExecutionService, "_is_file_dependent_tool", return_value=False):
        with patch.object(ToolExecutionService, "invoke_tool_with_direct_creds", return_value="result") as mock_direct:
            result = ToolExecutionService.invoke(mock_request, "test-tool", mock_user)

    # Assert
    mock_direct.assert_called_once_with(mock_request, "test-tool")
    assert result == "result"


def test_invoke_with_system_integration():
    """Test invoking tool with system integration."""
    # Setup
    mock_request = Mock(spec=ToolInvokeRequest)
    mock_request.tool_creds = {"integration_alias": "test-integration"}
    mock_user = Mock(spec=User)

    # Mock system integration method and file dependency check
    with patch.object(ToolExecutionService, "_is_file_dependent_tool", return_value=False):
        with patch.object(
            ToolExecutionService, "invoke_tool_with_system_integration", return_value="result"
        ) as mock_system:
            result = ToolExecutionService.invoke(mock_request, "test-tool", mock_user)

    # Assert
    mock_system.assert_called_once_with(mock_request, "test-tool", mock_user)
    assert result == "result"


def test_map_params_to_method_signature():
    """Test mapping parameters to method signature."""
    # Method params
    method_params = {"param1": "default1", "param2": "default2"}

    # Input params with complete override
    input_params = {"param1": "override1", "param2": "override2"}

    # Call method
    result = ToolExecutionService.map_params_to_method_signature(method_params, input_params)

    # Assert all overridden
    assert result == {"param1": "override1", "param2": "override2"}


def test_get_search_tool_kb_index():
    """Test getting search tool for KB index."""
    # Setup
    datasource = Mock(spec=IndexInfo)
    datasource.is_code_index = Mock(return_value=False)

    request = Mock(spec=DatasourceSearchInvokeRequest)
    request.llm_model = "gpt-4"

    # Mock SearchKBTool
    with patch("codemie.service.tools.tool_execution_service.SearchKBTool", return_value="kb_tool") as mock_kb:
        result = ToolExecutionService.get_search_tool(datasource, request)

    # Assert
    mock_kb.assert_called_once()
    assert result == "kb_tool"


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
                # Call method
                result = ToolExecutionService.invoke_tool_with_direct_creds(mock_request, "test-tool")

    # Assert
    mock_get_tool.assert_called_once_with(mock_request, "test-tool")
    mock_validate.assert_called_once_with(mock_tool, mock_request.tool_args)
    mock_update.assert_called_once_with(mock_tool, mock_request.tool_attributes)
    mock_tool.execute.assert_called_once_with(**mock_request.tool_args)
    assert result == "Success result"


def test_invoke_file_analysis_tool_with_image():
    """Test invoking file analysis tool with image file (VisionToolkit)."""
    # Setup
    mock_request = Mock(spec=ToolInvokeRequest)
    mock_request.tool_args = {"file_names": ["test_image.jpg"], "query": "analyze this"}
    mock_request.tool_attributes = None
    mock_request.llm_model = "gpt-4"

    mock_file_object = Mock()
    mock_file_object.is_image = Mock(return_value=True)

    mock_tool = Mock(spec=BaseTool)
    mock_tool.execute = Mock(return_value="Image analysis result")

    mock_toolkit = Mock()
    mock_toolkit.get_tools = Mock(return_value=[mock_tool])

    # Mock required dependencies
    with patch(
        "codemie.service.tools.tool_execution_service.build_unique_file_objects_list", return_value=[mock_file_object]
    ):
        with patch("codemie.service.tools.tool_execution_service.get_llm_by_credentials", return_value="mock_llm"):
            with patch(
                "codemie.service.tools.tool_execution_service.VisionToolkit.get_toolkit", return_value=mock_toolkit
            ):
                with patch(
                    "codemie.service.tools.tool_execution_service.ToolsService.find_tool", return_value=mock_tool
                ):
                    with patch.object(ToolExecutionService, "validate_tool_args", return_value=mock_tool):
                        with patch("codemie.service.tools.tool_execution_service.logger"):
                            result = ToolExecutionService.invoke_file_analysis_tool(mock_request, "image_tool")

    # Assert
    assert result == "Image analysis result"
    assert "file_names" not in mock_request.tool_args  # Should be popped
    mock_tool.execute.assert_called_once_with(query="analyze this")


def test_invoke_file_analysis_tool_with_document():
    """Test invoking file analysis tool with document file (FileAnalysisToolkit)."""
    # Setup
    mock_request = Mock(spec=ToolInvokeRequest)
    mock_request.tool_args = {"file_names": ["test_document.pdf"], "query": "extract text"}
    mock_request.tool_attributes = None
    mock_request.llm_model = "gpt-4"

    mock_file_object = Mock()
    mock_file_object.is_image = Mock(return_value=False)

    mock_tool = Mock(spec=BaseTool)
    mock_tool.execute = Mock(return_value="Document analysis result")

    mock_toolkit = Mock()
    mock_toolkit.get_tools = Mock(return_value=[mock_tool])

    # Mock required dependencies
    with patch(
        "codemie.service.tools.tool_execution_service.build_unique_file_objects_list", return_value=[mock_file_object]
    ):
        with patch("codemie.service.tools.tool_execution_service.get_llm_by_credentials", return_value="mock_llm"):
            with patch(
                "codemie.service.tools.tool_execution_service.FileAnalysisToolkit.get_toolkit",
                return_value=mock_toolkit,
            ):
                with patch(
                    "codemie.service.tools.tool_execution_service.ToolsService.find_tool", return_value=mock_tool
                ):
                    with patch.object(ToolExecutionService, "validate_tool_args", return_value=mock_tool):
                        with patch("codemie.service.tools.tool_execution_service.logger"):
                            result = ToolExecutionService.invoke_file_analysis_tool(mock_request, "pdf_tool")

    # Assert
    assert result == "Document analysis result"
    assert "file_names" not in mock_request.tool_args  # Should be popped
    mock_tool.execute.assert_called_once_with(query="extract text")


def test_invoke_file_analysis_tool_with_attributes():
    """Test invoking file analysis tool with tool attributes."""
    # Setup
    mock_request = Mock(spec=ToolInvokeRequest)
    mock_request.tool_args = {"file_names": ["test.pdf"]}
    mock_request.tool_attributes = {"attr1": "value1"}
    mock_request.llm_model = "gpt-4"

    mock_file_object = Mock()
    mock_file_object.is_image = Mock(return_value=False)

    mock_tool = Mock(spec=BaseTool)
    mock_tool.execute = Mock(return_value="Result with attributes")

    mock_toolkit = Mock()
    mock_toolkit.get_tools = Mock(return_value=[mock_tool])

    # Mock required dependencies
    with patch(
        "codemie.service.tools.tool_execution_service.build_unique_file_objects_list", return_value=[mock_file_object]
    ):
        with patch("codemie.service.tools.tool_execution_service.get_llm_by_credentials", return_value="mock_llm"):
            with patch(
                "codemie.service.tools.tool_execution_service.FileAnalysisToolkit.get_toolkit",
                return_value=mock_toolkit,
            ):
                with patch(
                    "codemie.service.tools.tool_execution_service.ToolsService.find_tool", return_value=mock_tool
                ):
                    with patch.object(ToolExecutionService, "validate_tool_args", return_value=mock_tool):
                        with patch.object(
                            ToolExecutionService, "update_tool_attributes", return_value=mock_tool
                        ) as mock_update:
                            with patch("codemie.service.tools.tool_execution_service.logger"):
                                result = ToolExecutionService.invoke_file_analysis_tool(mock_request, "pdf_tool")

    # Assert
    assert result == "Result with attributes"
    mock_update.assert_called_once_with(mock_tool, mock_request.tool_attributes)


def test_invoke_file_analysis_tool_missing_file_name():
    """Test invoking file analysis tool when file_names is missing."""
    # Setup
    mock_request = Mock(spec=ToolInvokeRequest)
    mock_request.tool_args = {}  # No file_names

    # Call method and expect ValueError
    with pytest.raises(ValueError) as excinfo:
        ToolExecutionService.invoke_file_analysis_tool(mock_request, "pdf_tool")

    # Assert
    assert "Tool requires uploaded file" in str(excinfo.value)


def test_invoke_file_analysis_tool_none_file_name():
    """Test invoking file analysis tool when file_names is empty list."""
    # Setup
    mock_request = Mock(spec=ToolInvokeRequest)
    mock_request.tool_args = {"file_names": []}

    # Call method and expect ValueError
    with pytest.raises(ValueError) as excinfo:
        ToolExecutionService.invoke_file_analysis_tool(mock_request, "pdf_tool")

    # Assert
    assert "Tool requires uploaded file" in str(excinfo.value)


def test_invoke_file_analysis_tool_execution_error():
    """Test error handling during file analysis tool execution."""
    # Setup
    mock_request = Mock(spec=ToolInvokeRequest)
    mock_request.tool_args = {"file_names": ["test.pdf"]}
    mock_request.tool_attributes = None
    mock_request.llm_model = "gpt-4"

    mock_file_object = Mock()
    mock_file_object.is_image = Mock(return_value=False)

    mock_tool = Mock(spec=BaseTool)
    mock_tool.execute = Mock(side_effect=ValueError("Tool execution error"))

    mock_toolkit = Mock()
    mock_toolkit.get_tools = Mock(return_value=[mock_tool])

    # Mock required dependencies
    with patch(
        "codemie.service.tools.tool_execution_service.build_unique_file_objects_list", return_value=[mock_file_object]
    ):
        with patch("codemie.service.tools.tool_execution_service.get_llm_by_credentials", return_value="mock_llm"):
            with patch(
                "codemie.service.tools.tool_execution_service.FileAnalysisToolkit.get_toolkit",
                return_value=mock_toolkit,
            ):
                with patch(
                    "codemie.service.tools.tool_execution_service.ToolsService.find_tool", return_value=mock_tool
                ):
                    with patch.object(ToolExecutionService, "validate_tool_args", return_value=mock_tool):
                        with patch("codemie.service.tools.tool_execution_service.logger") as mock_logger:
                            # Expect exception to be re-raised
                            with pytest.raises(ValueError) as excinfo:
                                ToolExecutionService.invoke_file_analysis_tool(mock_request, "pdf_tool")

    # Assert
    assert "Tool execution error" in str(excinfo.value)
    mock_logger.error.assert_called_once()
    assert "Error occurred on tool invocation" in mock_logger.error.call_args[0][0]
