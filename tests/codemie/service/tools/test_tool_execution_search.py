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

from codemie.core.models import CodeFields
from codemie.rest_api.models.index import IndexInfo
from codemie.rest_api.models.tool import DatasourceSearchInvokeRequest, CodeDatasourceSearchParams
from codemie.service.tools.tool_execution_service import ToolExecutionService


def test_invoke_datasource_search():
    """Test invoking datasource search."""
    # Create mock index and request
    datasource = Mock(spec=IndexInfo)
    request = Mock(spec=DatasourceSearchInvokeRequest)
    request.query = "test query"
    request.llm_model = "gpt-4"

    # Create mock search tool
    mock_search_tool = Mock()
    mock_search_tool.metadata = {}
    mock_search_tool.execute = Mock(return_value="search results")

    # Mock get_search_tool
    with patch.object(ToolExecutionService, 'get_search_tool', return_value=mock_search_tool) as mock_get_tool:
        result = ToolExecutionService.invoke_datasource_search(datasource, request)

    # Assert
    mock_get_tool.assert_called_once_with(datasource, request)
    mock_search_tool.execute.assert_called_once_with(query=request.query)
    assert mock_search_tool.metadata == {'llm_model': request.llm_model}
    assert result == "search results"


def test_get_search_tool_kb_index():
    """Test getting search tool for KB index."""
    # Create mock KB index and request
    datasource = Mock(spec=IndexInfo)
    datasource.is_code_index = Mock(return_value=False)

    request = Mock(spec=DatasourceSearchInvokeRequest)
    request.llm_model = "gpt-4"

    # Mock SearchKBTool constructor
    with patch('codemie.service.tools.tool_execution_service.SearchKBTool', return_value="kb_tool") as mock_kb_tool:
        result = ToolExecutionService.get_search_tool(datasource, request)

    # Assert
    mock_kb_tool.assert_called_once_with(kb_index=datasource, llm_model="gpt-4")
    assert result == "kb_tool"


def test_get_search_tool_code_index():
    """Test getting search tool for code index."""
    # Create mock code index and request
    datasource = Mock(spec=IndexInfo)
    datasource.is_code_index = Mock(return_value=True)
    datasource.project_name = "test-project"
    datasource.repo_name = "test-repo"
    datasource.index_type = "code"

    request = Mock(spec=DatasourceSearchInvokeRequest)
    request.llm_model = "gpt-4"
    request.query = "search query"

    # Create code search parameters
    code_params = Mock(spec=CodeDatasourceSearchParams)
    code_params.user_input = "custom input"
    code_params.top_k = 5
    code_params.with_filtering = True
    request.code_search_params = code_params

    # Mock CodeToolkit.search_code_tool
    with patch(
        'codemie.service.tools.tool_execution_service.CodeToolkit.search_code_tool', return_value="code_tool"
    ) as mock_code_tool:
        result = ToolExecutionService.get_search_tool(datasource, request)

    # Assert
    mock_code_tool.assert_called_once()
    code_fields_arg = mock_code_tool.call_args.kwargs['code_fields']
    assert isinstance(code_fields_arg, CodeFields)
    assert code_fields_arg.app_name == "test-project"
    assert code_fields_arg.repo_name == "test-repo"
    assert code_fields_arg.index_type == "code"
    assert mock_code_tool.call_args.kwargs['user_input'] == "custom input"
    assert mock_code_tool.call_args.kwargs['top_k'] == 5
    assert mock_code_tool.call_args.kwargs['with_filtering']
    assert result == "code_tool"


def test_get_search_tool_code_index_default_params():
    """Test getting search tool for code index with default params."""
    # Create mock code index and request
    datasource = Mock(spec=IndexInfo)
    datasource.is_code_index = Mock(return_value=True)
    datasource.project_name = "test-project"
    datasource.repo_name = "test-repo"
    datasource.index_type = "code"

    request = Mock(spec=DatasourceSearchInvokeRequest)
    request.llm_model = "gpt-4"
    request.query = "search query"
    request.code_search_params = None

    # Mock CodeToolkit.search_code_tool
    with patch(
        'codemie.service.tools.tool_execution_service.CodeToolkit.search_code_tool', return_value="code_tool"
    ) as mock_code_tool:
        result = ToolExecutionService.get_search_tool(datasource, request)

    # Assert
    mock_code_tool.assert_called_once()
    code_fields_arg = mock_code_tool.call_args.kwargs['code_fields']
    assert isinstance(code_fields_arg, CodeFields)
    assert code_fields_arg.app_name == "test-project"
    assert code_fields_arg.repo_name == "test-repo"
    assert code_fields_arg.index_type == "code"
    assert mock_code_tool.call_args.kwargs['user_input'] == "search query"  # Falls back to request.query
    assert result == "code_tool"
