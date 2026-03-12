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

import pytest
from unittest.mock import Mock, patch, call
from codemie.service.filter.filter_services import IndexInfoFilter
from codemie.service.filter.compose_filter_functions import (
    compose_term_filter,
    compose_combined_date_range_filter,
    compose_status_filter,
    compose_wildcard_filter,
    compose_json_array_filter,
)
from codemie.rest_api.models.index import IndexInfo
from codemie.rest_api.models.assistant import Assistant
from sqlmodel import select
from codemie.service.filter.filter_models import SearchFields, IndexInfoStatus


class TestIndexInfoFilter:
    @pytest.fixture
    def filter_config(self):
        return IndexInfoFilter.FILTER_CONFIG

    def test_filter_config(self, filter_config):
        assert isinstance(filter_config, dict)
        assert filter_config["name"]["field_name"] == SearchFields.REPO_NAME.value
        assert filter_config["name"]["filter_compose_func"] == compose_wildcard_filter

    def test_add_filters(self):
        query = select(IndexInfo)
        raw_filters = {
            "name": "mock_name",
            "status": IndexInfoStatus.COMPLETED,
            "date_range": {"start_date": "2024-01-01", "end_date": "2024-12-31"},
        }

        updated_query = IndexInfoFilter.add_sql_filters(query, IndexInfo, raw_filters)
        query_str = str(updated_query)

        # Check that each filter was properly applied
        assert 'lower(index_info.repo_name) LIKE lower(:repo_name_1)' in query_str
        assert 'index_info.completed = true' in query_str
        assert 'index_info.date BETWEEN :date_1 AND :date_2' in query_str


class TestComposeFilterFunctions:
    def test_compose_combined_date_range_filter(self):
        filters = {"start_date": "2024-01-01", "end_date": "2024-12-31"}
        query = select(IndexInfo)
        query = compose_combined_date_range_filter(query, IndexInfo, "date", filters)
        assert 'WHERE index_info.date BETWEEN :date_1 AND :date_2' in str(query)

    def test_compose_status_filter_completed(self):
        query = select(IndexInfo)
        query = compose_status_filter(query, IndexInfo, ["completed", "error"], IndexInfoStatus.COMPLETED)
        assert 'WHERE index_info.completed = true' in str(query)

    def test_compose_status_filter_failed(self):
        query = select(IndexInfo)
        query = compose_status_filter(query, IndexInfo, ["completed", "error"], IndexInfoStatus.FAILED)
        assert 'WHERE index_info.completed = false AND index_info.error = true' in str(query)

    def test_compose_term_filter(self):
        query = select(IndexInfo)
        query = compose_term_filter(query, IndexInfo, "project_name", "test")
        assert 'WHERE index_info.project_name = :project_name_1' in str(query)

    def test_compose_wildcard_filter(self):
        query = select(IndexInfo)
        query = compose_wildcard_filter(query, IndexInfo, "project_name", "test")
        assert 'WHERE lower(index_info.project_name) LIKE lower(:project_name_1)' in str(query)


def test_compose_json_array_filter_single_value():
    """Test JSON array filter with single value."""
    mock_query = Mock()
    mock_model = Mock()
    mock_field = Mock()
    mock_condition = Mock()

    mock_model.get_field_expression.return_value = mock_field
    mock_op_method = Mock(return_value=mock_condition)
    mock_field.op.return_value = mock_op_method
    mock_query.where.return_value = "modified_query"

    result = compose_json_array_filter(mock_query, mock_model, "categories", "engineering")
    mock_model.get_field_expression.assert_called_once_with("categories")
    mock_field.op.assert_called_once_with('@>')
    mock_op_method.assert_called_once_with('["engineering"]')
    mock_query.where.assert_called_once_with(mock_condition)
    assert result == "modified_query"


def test_compose_json_array_filter_multiple_values():
    """Test JSON array filter with multiple values."""
    mock_query = Mock()
    mock_model = Mock()
    mock_field = Mock()
    mock_condition1 = Mock()
    mock_condition2 = Mock()
    mock_model.get_field_expression.return_value = mock_field

    mock_op_method1 = Mock(return_value=mock_condition1)
    mock_op_method2 = Mock(return_value=mock_condition2)
    mock_field.op.side_effect = [mock_op_method1, mock_op_method2]
    mock_query.where.return_value = "modified_query"

    with patch('codemie.service.filter.compose_filter_functions.or_') as mock_or:
        mock_or_result = Mock()
        mock_or.return_value = mock_or_result

        result = compose_json_array_filter(mock_query, mock_model, "categories", ["engineering", "data-analytics"])
        mock_model.get_field_expression.assert_called_once_with("categories")
        assert mock_field.op.call_count == 2
        mock_field.op.assert_has_calls([call('@>'), call('@>')])
        mock_op_method1.assert_called_once_with('["engineering"]')
        mock_op_method2.assert_called_once_with('["data-analytics"]')
        mock_or.assert_called_once_with(mock_condition1, mock_condition2)
        mock_query.where.assert_called_once_with(mock_or_result)
        assert result == "modified_query"


def test_compose_json_array_filter_empty_list():
    """Test JSON array filter with empty list."""
    query = select(Assistant)
    original_query = query
    result = compose_json_array_filter(query, Assistant, "categories", [])
    assert result == original_query
