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
from codemie.core.exceptions import ValidationException
from codemie.service.filter.filter_services import IndexInfoFilter
from codemie.service.filter.compose_filter_functions import (
    compose_term_filter,
    compose_combined_date_range_filter,
    compose_status_filter,
    compose_wildcard_filter,
    compose_json_array_filter,
    compose_credential_type_filter,
    compose_workflow_integration_type_filter,
)
from codemie.rest_api.models.index import IndexInfo
from codemie.rest_api.models.assistant import Assistant
from codemie.core.workflow_models.workflow_config import WorkflowConfig
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


def _compile_sql(statement) -> str:
    """Compile a SQLModel/SQLAlchemy statement to SQL text with literal values inlined,
    so assertions can check for actual key/value literals instead of bind-parameter
    placeholders (str(statement) renders JSON path keys and IN-list values as
    :param_N / __[POSTCOMPILE_param_N], not their literal text)."""
    from sqlalchemy.dialects import postgresql

    return str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def test_compose_credential_type_filter_internal_toolkit_matches_by_toolkit_name():
    """Git ("internal" toolkit) never populates settings.credential_type for its own
    dedicated toolkit entry — even with an explicit credential attached — so it must also
    be matched by toolkits[].toolkit directly, not only via resolved tool names.

    Regression test for the reported bug: an assistant with a Git toolkit using
    auto_credentials_lookup (settings=null) was invisible to integration_type=Git because
    the old implementation only matched settings.credential_type.
    """
    query = select(Assistant)
    result = compose_credential_type_filter(query, Assistant, "toolkits", "Git")
    query_str = _compile_sql(result)

    assert "EXISTS" in query_str
    assert "jsonb_array_elements" in query_str
    assert "'toolkit'" in query_str
    assert "'Git'" in query_str


def test_compose_credential_type_filter_provider_toolkit_matches_by_tool_name():
    """Non-internal credential types (Jira, Confluence, ...) are resolved to their known
    tool names via ToolMetadataService and matched against toolkits[].tools[].name — this
    is what lets toolkits that bundle several providers under one toolkit name (e.g. Jira
    and Confluence both live under "Project Management") be told apart."""
    query = select(Assistant)
    result = compose_credential_type_filter(query, Assistant, "toolkits", "Jira")
    query_str = _compile_sql(result)

    assert "EXISTS" in query_str
    assert "jsonb_array_elements" in query_str
    assert "LATERAL" in query_str
    assert "'name'" in query_str
    assert "generic_jira_tool" in query_str
    # Jira has no dedicated toolkit-name entry — only the tool-name match applies.
    assert query_str.count("EXISTS") == 1


def test_compose_credential_type_filter_git_also_matches_by_resolved_tool_names():
    """ "Git" isn't purely an internal-toolkit lookup: the separate discoverable "VCS"
    toolkit exposes github/gitlab tools whose credential_type is also "Git". Both match
    strategies must be present and OR-combined for a single "Git" filter value."""
    query = select(Assistant)
    result = compose_credential_type_filter(query, Assistant, "toolkits", "Git")
    query_str = _compile_sql(result)

    assert query_str.count("EXISTS") == 2
    assert " OR " in query_str
    assert "github" in query_str
    assert "gitlab" in query_str


def test_compose_credential_type_filter_multiple_values_or_combined():
    """An internal type (Git) and a provider type (Jira) OR-combine their two match
    strategies into a single WHERE clause."""
    query = select(Assistant)
    result = compose_credential_type_filter(query, Assistant, "toolkits", ["Git", "Jira"])
    query_str = _compile_sql(result)

    assert query_str.count("EXISTS") == 2
    assert " OR " in query_str
    assert "'toolkit'" in query_str
    assert "generic_jira_tool" in query_str


def test_compose_credential_type_filter_unknown_value_is_rejected():
    """A credential type that no tool resolves to must be rejected explicitly instead of
    silently returning an empty list."""
    with pytest.raises(ValidationException):
        compose_credential_type_filter(select(Assistant), Assistant, "toolkits", "NotARealIntegration")


def test_compose_credential_type_filter_plugin_matches_by_toolkit_name():
    """Plugin ("internal" toolkit) is matched via toolkits[].toolkit directly, same as
    Git — unlike compose_workflow_integration_type_filter, this path has an explicit
    toolkit field to match against, so "Plugin" is fully supported here. Locks in the
    intentional asymmetry: Assistant filtering supports Plugin, workflow filtering
    rejects it (see test_compose_workflow_integration_type_filter_plugin_is_rejected)."""
    query = select(Assistant)
    result = compose_credential_type_filter(query, Assistant, "toolkits", "Plugin")
    query_str = _compile_sql(result)

    assert "EXISTS" in query_str
    assert "'toolkit'" in query_str
    assert "'Plugin'" in query_str


def test_compose_credential_type_filter_empty_list():
    """An empty value list is a no-op — returns the query unchanged."""
    query = select(Assistant)
    result = compose_credential_type_filter(query, Assistant, "toolkits", [])
    assert result is query


def test_compose_credential_type_filter_none_value_is_no_op():
    """filter_value=[None] must not generate IS NULL — it should be a no-op."""
    query = select(Assistant)
    result = compose_credential_type_filter(query, Assistant, "toolkits", [None])
    assert result is query


def test_compose_credential_type_filter_mixed_none_skips_nulls():
    """None elements are stripped; valid values still generate the EXISTS clause."""
    query = select(Assistant)
    result = compose_credential_type_filter(query, Assistant, "toolkits", [None, "Git"])
    query_str = str(result)
    assert "EXISTS" in query_str
    assert "NULL" not in query_str.upper().replace("ISNULL", "")


# ---------------------------------------------------------------------------
# compose_workflow_integration_type_filter
# ---------------------------------------------------------------------------


def test_compose_workflow_integration_type_filter_has_two_exists_paths():
    """Both standalone-tools and assistant-embedded-tools EXISTS paths appear."""
    query = select(WorkflowConfig)
    result = compose_workflow_integration_type_filter(query, WorkflowConfig, ["tools", "assistants"], "Git")
    query_str = _compile_sql(result)

    assert query_str.count("EXISTS") == 2
    assert " OR " in query_str
    assert "jsonb_array_elements" in query_str


def test_compose_workflow_integration_type_filter_standalone_path_uses_tool_key():
    """Standalone path checks 'tool' key, not 'name'."""
    query = select(WorkflowConfig)
    result = compose_workflow_integration_type_filter(query, WorkflowConfig, ["tools", "assistants"], "Git")
    query_str = _compile_sql(result)
    assert "'tool'" in query_str


def test_compose_workflow_integration_type_filter_embedded_path_uses_name_key():
    """Embedded assistant-tools path checks 'name' key."""
    query = select(WorkflowConfig)
    result = compose_workflow_integration_type_filter(query, WorkflowConfig, ["tools", "assistants"], "Git")
    query_str = _compile_sql(result)
    assert "'name'" in query_str


def test_compose_workflow_integration_type_filter_git_resolves_tool_names():
    """'Git' resolves to real Git tool names (e.g. create_branch)."""
    query = select(WorkflowConfig)
    result = compose_workflow_integration_type_filter(query, WorkflowConfig, ["tools", "assistants"], "Git")
    query_str = _compile_sql(result)
    assert "create_branch" in query_str


def test_compose_workflow_integration_type_filter_empty_list_is_noop():
    query = select(WorkflowConfig)
    result = compose_workflow_integration_type_filter(query, WorkflowConfig, ["tools", "assistants"], [])
    assert result is query


def test_compose_workflow_integration_type_filter_unknown_value_is_rejected():
    with pytest.raises(ValidationException):
        compose_workflow_integration_type_filter(
            select(WorkflowConfig), WorkflowConfig, ["tools", "assistants"], "NotARealIntegration"
        )


def test_compose_workflow_integration_type_filter_plugin_is_rejected():
    """Workflow tool records (WorkflowTool.tool / WorkflowAssistantTool.name) store only a
    flat tool-name string — there's no toolkit field to match "Plugin" by name, and Plugin
    tool names are dynamic/per-instance so no static tool-name set can be built either.
    Filtering must reject the value explicitly (400) instead of silently matching zero
    workflows, since a caller could otherwise mistake the empty result for "no matches"."""
    query = select(WorkflowConfig)
    with pytest.raises(ValidationException, match="Plugin"):
        compose_workflow_integration_type_filter(query, WorkflowConfig, ["tools", "assistants"], "Plugin")


def test_compose_workflow_integration_type_filter_plugin_among_multiple_values_is_rejected():
    query = select(WorkflowConfig)
    with pytest.raises(ValidationException, match="Plugin"):
        compose_workflow_integration_type_filter(query, WorkflowConfig, ["tools", "assistants"], ["Git", "Plugin"])


def test_compose_workflow_integration_type_filter_string_field_name_raises():
    """field_name must be [tools_field, assistants_field] (2-element list/tuple) — every
    other FILTER_CONFIG entry for this function passes it that way, but field_name[0] /
    field_name[1] are indexed unconditionally, so a copy-paste config mistake that passes
    a plain str must fail fast with a clear message instead of an opaque error deep in
    SQLAlchemy expression building."""
    query = select(WorkflowConfig)
    with pytest.raises(ValueError, match="field_name"):
        compose_workflow_integration_type_filter(query, WorkflowConfig, "tools", "Git")


def test_compose_workflow_integration_type_filter_wrong_arity_field_name_raises():
    query = select(WorkflowConfig)
    with pytest.raises(ValueError, match="field_name"):
        compose_workflow_integration_type_filter(query, WorkflowConfig, ["tools"], "Git")


def test_compose_workflow_integration_type_filter_none_values_skipped():
    """None values are stripped; a valid value still generates the EXISTS clauses."""
    query = select(WorkflowConfig)
    result = compose_workflow_integration_type_filter(query, WorkflowConfig, ["tools", "assistants"], [None, "Git"])
    query_str = _compile_sql(result)
    assert "EXISTS" in query_str


# ---------------------------------------------------------------------------
# WorkflowFilter.FILTER_CONFIG wiring
# ---------------------------------------------------------------------------


def test_workflow_filter_config_has_integration_type():
    from codemie.service.filter.filter_services import WorkflowFilter

    config = WorkflowFilter.FILTER_CONFIG["integration_type"]
    assert config["field_name"] == ["tools", "assistants"]
    assert config["filter_compose_func"] is compose_workflow_integration_type_filter


def test_workflow_filter_applies_integration_type_via_add_sql_filters():
    """WorkflowFilter.add_sql_filters dispatches integration_type to the compose function."""
    from codemie.service.filter.filter_services import WorkflowFilter

    query = select(WorkflowConfig)
    result = WorkflowFilter.add_sql_filters(query, model_class=WorkflowConfig, raw_filters={"integration_type": "Git"})
    query_str = _compile_sql(result)

    assert query_str.count("EXISTS") == 2
    assert "create_branch" in query_str


def test_workflow_filter_integration_type_with_deferred_columns():
    """defer() on tools/assistants columns must not prevent the EXISTS WHERE clause."""
    from sqlalchemy.orm import defer as sa_defer
    from codemie.service.filter.filter_services import WorkflowFilter

    query = select(WorkflowConfig).options(
        sa_defer(WorkflowConfig.tools),
        sa_defer(WorkflowConfig.assistants),
    )
    result = WorkflowFilter.add_sql_filters(query, model_class=WorkflowConfig, raw_filters={"integration_type": "Git"})
    query_str = _compile_sql(result)

    assert "EXISTS" in query_str
    assert "create_branch" in query_str
