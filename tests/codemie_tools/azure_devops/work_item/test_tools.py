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

from unittest.mock import Mock, MagicMock

import pytest

from codemie_tools.azure_devops.work_item.models import AzureDevOpsWorkItemConfig
from codemie_tools.azure_devops.work_item.tools import (
    SearchWorkItemsTool,
    CreateWorkItemTool,
    UpdateWorkItemTool,
    GetWorkItemTool,
    GetRelationTypesTool,
)


@pytest.fixture
def mock_config():
    return AzureDevOpsWorkItemConfig(
        organization_url="https://dev.azure.com/org", project="test-project", token="fake-token", limit=5
    )


@pytest.fixture
def mock_client():
    return Mock()


@pytest.fixture
def search_tool(mock_config, mock_client):
    tool = SearchWorkItemsTool(config=mock_config)
    tool._client = mock_client
    return tool


@pytest.fixture
def create_tool(mock_config, mock_client):
    tool = CreateWorkItemTool(config=mock_config)
    tool._client = mock_client
    return tool


@pytest.fixture
def update_tool(mock_config, mock_client):
    tool = UpdateWorkItemTool(config=mock_config)
    tool._client = mock_client
    return tool


@pytest.fixture
def get_tool(mock_config, mock_client):
    tool = GetWorkItemTool(config=mock_config)
    tool._client = mock_client
    return tool


class TestSearchWorkItemsTool:
    def test_search_work_items_success(self, search_tool, mock_client):
        # Arrange
        query = "SELECT [System.Id] FROM WorkItems"
        mock_work_item = MagicMock()
        mock_work_item.id = 1
        mock_client.query_by_wiql.return_value.work_items = [mock_work_item]

        mock_full_item = MagicMock()
        mock_full_item.id = 1
        mock_full_item.fields = {"System.Title": "Test Item"}
        mock_client.get_work_item.return_value = mock_full_item

        # Act
        result = search_tool.execute(query=query)

        # Assert
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["id"] == 1
        mock_client.query_by_wiql.assert_called_once()

    def test_search_work_items_no_results(self, search_tool, mock_client):
        # Arrange
        query = "SELECT [System.Id] FROM WorkItems"
        mock_client.query_by_wiql.return_value.work_items = []

        # Act
        result = search_tool.execute(query=query)

        # Assert
        assert result == "No work items found."


class TestCreateWorkItemTool:
    def test_create_work_item_success(self, create_tool, mock_client):
        # Arrange
        work_item_json = '{"fields": {"System.Title": "Test Item"}}'
        mock_response = MagicMock()
        mock_response.id = 1
        mock_response.url = "http://test-url"
        mock_client.create_work_item.return_value = mock_response

        # Act
        result = create_tool.execute(work_item_json=work_item_json)

        # Assert
        assert "created successfully" in result
        assert "1" in result
        mock_client.create_work_item.assert_called_once()


class TestUpdateWorkItemTool:
    def test_update_work_item_success(self, update_tool, mock_client):
        # Arrange
        work_item_json = '{"fields": {"System.Title": "Updated Title"}}'
        mock_response = MagicMock()
        mock_response.id = 1
        mock_client.update_work_item.return_value = mock_response

        # Act
        result = update_tool.execute(id=1, work_item_json=work_item_json)

        # Assert
        assert "was updated" in result
        mock_client.update_work_item.assert_called_once()


class TestGetWorkItemTool:
    def test_get_work_item_success(self, get_tool, mock_client):
        # Arrange
        mock_work_item = MagicMock()
        mock_work_item.id = 1
        mock_work_item.fields = {"System.Title": "Test Item"}
        mock_work_item.relations = []
        mock_client.get_work_item.return_value = mock_work_item

        # Act
        result = get_tool.execute(id=1)

        # Assert
        assert isinstance(result, dict)
        assert result["id"] == 1
        assert "System.Title" in result
        mock_client.get_work_item.assert_called_once()


class TestGetRelationTypesTool:
    def test_get_relation_types_success(self, mock_config, mock_client):
        # Arrange
        tool = GetRelationTypesTool(config=mock_config)
        tool._client = mock_client

        mock_relation = MagicMock()
        mock_relation.name = "Relates"
        mock_relation.reference_name = "System.LinkTypes.Related"
        mock_client.get_relation_types.return_value = [mock_relation]

        # Act
        result = tool.execute()

        # Assert
        assert isinstance(result, dict)
        assert "Relates" in result
        assert result["Relates"] == "System.LinkTypes.Related"
