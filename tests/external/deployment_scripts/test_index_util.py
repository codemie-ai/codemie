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

import json
import pytest
from unittest.mock import patch, mock_open
from codemie.rest_api.models.index import IndexInfo
from codemie.clients.elasticsearch import ElasticSearchClient

from external.deployment_scripts.index_util import create_index_from_dump


@pytest.fixture
def mock_elasticsearch_client():
    with patch.object(ElasticSearchClient, 'get_client') as mock_client:
        yield mock_client.return_value


@pytest.fixture
def mock_index_info():
    return {
        "repo_name": "will not be used",
        "project_name": "will not be used",
        "index_type": "ff",
        # Add other necessary fields for IndexInfo
    }


@pytest.fixture
def mock_dump_data(mock_index_info):
    return {
        "settings": {"index": {"number_of_shards": "1"}},
        "mapping": {"properties": {"field": {"type": "text"}}},
        "documents": [{"_id": "1", "_source": {"field": "value"}, "_index": "demo_project-codemie-onboarding"}],
        "index_info": json.dumps(mock_index_info),
    }


def test_create_index_from_dump(mock_elasticsearch_client, mock_dump_data, mock_index_info):
    project_name = "demo_project"
    index_name = "codemie-onboarding"
    full_index_name = f"{project_name}-{index_name}"

    # Mock the file open and json.load
    with (
        patch("builtins.open", mock_open(read_data=json.dumps(mock_dump_data))),
        patch("json.load", return_value=mock_dump_data),
        patch.object(IndexInfo, "get_by_fields", return_value=None),
        patch.object(IndexInfo, "save"),
    ):
        # Call the function
        result = create_index_from_dump(project_name, index_name)

        # Assertions
        mock_elasticsearch_client.indices.create.assert_called_once_with(
            index=full_index_name, body={'settings': mock_dump_data['settings']}, ignore=400
        )
        mock_elasticsearch_client.indices.put_mapping.assert_called_once_with(
            index=full_index_name, body=mock_dump_data['mapping']
        )
        mock_elasticsearch_client.index.assert_called_once_with(
            index=full_index_name,
            id=mock_dump_data['documents'][0]['_id'],
            body=mock_dump_data['documents'][0]['_source'],
        )

        # Assert result params
        assert result.repo_name == index_name
        assert result.project_name == project_name
        result.save.assert_called_once()


def test_create_index_from_dump_file_not_found():
    project_name = "demo_project"
    index_name = "codemie-onboarding"

    with patch("builtins.open", mock_open()) as mock_file:
        mock_file.side_effect = IOError("File not found")

    with pytest.raises(IOError):
        create_index_from_dump(project_name, index_name)
