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

"""Tests for retrieval-backend gating in codemie.workflows.utils.utils."""

from unittest.mock import Mock, patch

import pytest

from codemie.workflows.utils.utils import get_documents_tree_by_datasource_id


@patch("codemie.workflows.utils.utils.ElasticSearchClient")
@patch("codemie.workflows.utils.utils.IndexInfo")
def test_get_documents_tree_by_datasource_id_raises_when_retrieval_unavailable(mock_index_info, mock_es_client):
    mock_es_client.is_configured.return_value = False

    with pytest.raises(ValueError):
        get_documents_tree_by_datasource_id("ds-id")

    mock_index_info.find_by_id.assert_not_called()
    mock_es_client.get_client.assert_not_called()


@patch("codemie.workflows.utils.utils.ElasticSearchClient")
@patch("codemie.workflows.utils.utils.IndexInfo")
def test_get_documents_tree_by_datasource_id_returns_documents_when_retrieval_available(
    mock_index_info, mock_es_client
):
    mock_es_client.is_configured.return_value = True

    datasource = Mock()
    datasource.get_index_identifier.return_value = "repo-1"
    datasource.is_code_index.return_value = False
    datasource.is_google_doc_index.return_value = False
    mock_index_info.find_by_id.return_value = datasource

    mock_es_client.get_client.return_value.search.return_value = {
        "hits": {
            "hits": [
                {"_source": {"metadata": {"source": "doc1"}}},
            ]
        }
    }

    result = get_documents_tree_by_datasource_id("ds-id")

    assert result == [{"source": "doc1"}]
    mock_index_info.find_by_id.assert_called_once_with("ds-id")
