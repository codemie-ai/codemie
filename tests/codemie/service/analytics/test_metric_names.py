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

"""Unit tests for metric_names.py.

Tests the MetricName enum and its helper methods for converting metric names
to lists for use in analytics queries.
"""

from __future__ import annotations

from codemie.service.analytics.metric_names import MetricName


class TestMetricNameEnum:
    """Test MetricName enum structure and values."""

    def test_all_metric_names_have_valid_string_values(self):
        """Verify all enum members have non-empty, unique string values.

        Arrange: Get all MetricName enum members
        Act: Check each member's value
        Assert: All values are non-empty strings with no duplicates
        """
        # Arrange & Act
        all_values = [member.value for member in MetricName]

        # Assert
        assert len(all_values) > 0, "Enum should have at least one member"

        for value in all_values:
            assert isinstance(value, str), f"Value {value} should be a string"
            assert len(value) > 0, f"Value {value} should be non-empty"

        # Check for duplicates
        assert len(all_values) == len(set(all_values)), "Enum values should be unique"

    def test_metric_name_values_match_expected_format(self):
        """Verify metric names follow snake_case naming convention.

        Arrange: Get all MetricName values
        Act: Check format compliance
        Assert: Values use snake_case (only lowercase, numbers, underscores)
        """
        # Arrange & Act
        all_values = [member.value for member in MetricName]

        # Assert
        for value in all_values:
            # Check for snake_case: only lowercase letters, numbers, and underscores
            assert (
                value.replace('_', '')
                .replace('0', '')
                .replace('1', '')
                .replace('2', '')
                .replace('3', '')
                .replace('4', '')
                .replace('5', '')
                .replace('6', '')
                .replace('7', '')
                .replace('8', '')
                .replace('9', '')
                .islower()
            ), f"Value '{value}' should use snake_case format"

            # No spaces or special characters (except underscore)
            assert ' ' not in value, f"Value '{value}' should not contain spaces"


class TestMetricNameToList:
    """Test the to_list method for converting metrics to string lists."""

    def test_to_list_converts_single_metric_to_list(self):
        """Verify single metric conversion.

        Arrange: Single MetricName enum value
        Act: Call to_list
        Assert: Returns list with one string value
        """
        # Arrange & Act
        result = MetricName.to_list(MetricName.CONVERSATION_ASSISTANT_USAGE)

        # Assert
        assert result == ["conversation_assistant_usage"]
        assert isinstance(result, list)
        assert all(isinstance(item, str) for item in result)

    def test_to_list_converts_multiple_metrics_to_list(self):
        """Verify multiple metrics conversion with order preservation.

        Arrange: Multiple MetricName enum values
        Act: Call to_list
        Assert: Returns ordered list of string values
        """
        # Arrange & Act
        result = MetricName.to_list(MetricName.CONVERSATION_ASSISTANT_USAGE, MetricName.WORKFLOW_EXECUTION_TOTAL)

        # Assert
        assert result == ["conversation_assistant_usage", "workflow_execution_total"]
        assert len(result) == 2

    def test_to_list_with_no_arguments_returns_empty_list(self):
        """Verify edge case with no arguments.

        Arrange: No arguments
        Act: Call to_list()
        Assert: Returns empty list
        """
        # Arrange & Act
        result = MetricName.to_list()

        # Assert
        assert result == []
        assert isinstance(result, list)

    def test_to_list_with_cli_metrics(self):
        """Verify CLI-specific metrics conversion.

        Arrange: CLI metric enum values (including both old and new)
        Act: Call to_list
        Assert: Returns correct CLI metric strings
        """
        # Arrange & Act
        result = MetricName.to_list(
            MetricName.CLI_TOOL_USAGE_TOTAL,
            MetricName.CLI_COMMAND_EXECUTION_TOTAL,
            MetricName.CLI_ERROR_TOTAL,
        )

        # Assert
        assert "codemie_cli_tool_usage_total" in result  # New metric
        assert "codemie_cli_usage_total" in result  # Old metric
        assert "cli_error_total" in result
        assert len(result) == 3

    def test_to_list_preserves_order_for_many_metrics(self):
        """Verify order preservation with multiple metrics.

        Arrange: Five different metrics in specific order
        Act: Call to_list
        Assert: Order is preserved in result
        """
        # Arrange & Act
        result = MetricName.to_list(
            MetricName.CONVERSATION_ASSISTANT_USAGE,
            MetricName.DATASOURCE_TOKENS_USAGE,
            MetricName.WORKFLOW_EXECUTION_TOTAL,
            MetricName.AGENT_EXECUTION_TOTAL,
            MetricName.WEBHOOK_INVOCATION_TOTAL,
        )

        # Assert
        expected = [
            "conversation_assistant_usage",
            "datasource_tokens_usage",
            "workflow_execution_total",
            "agent_execution_total",
            "webhook_invocation_total",
        ]
        assert result == expected


class TestMetricNameToListFromGroup:
    """Test the to_list_from_group method for converting metric groups."""

    def test_to_list_from_group_summary_metrics(self):
        """Verify SUMMARY_METRICS group conversion.

        Arrange: SUMMARY_METRICS group
        Act: Call to_list_from_group
        Assert: Returns correct list of 6 summary metric strings (includes both old and new CLI metrics)
        """
        # Arrange & Act
        result = MetricName.to_list_from_group(MetricName.SUMMARY_METRICS)

        # Assert
        assert len(result) == 6
        assert "conversation_assistant_usage" in result
        assert "datasource_tokens_usage" in result
        assert "workflow_execution_total" in result
        assert "codemie_cli_tool_usage_total" in result  # NEW CLI metric (primary)
        assert "codemie_cli_usage_total" in result  # OLD CLI metric (backward compatibility)
        assert "codemie_litellm_proxy_usage" in result  # CLI LLM proxy metric

    def test_to_list_from_group_tools_metrics(self):
        """Verify TOOLS_METRICS group conversion.

        Arrange: TOOLS_METRICS group
        Act: Call to_list_from_group
        Assert: Returns correct list of 3 tools metric strings
        """
        # Arrange & Act
        result = MetricName.to_list_from_group(MetricName.TOOLS_METRICS)

        # Assert
        assert len(result) == 3
        assert "codemie_tools_usage_total" in result
        assert "codemie_tools_usage_tokens" in result
        assert "codemie_tools_usage_errors_total" in result

    def test_to_list_from_group_mcp_metrics(self):
        """Verify MCP_METRICS group conversion.

        Arrange: MCP_METRICS group
        Act: Call to_list_from_group
        Assert: Returns correct list of 2 MCP metric strings
        """
        # Arrange & Act
        result = MetricName.to_list_from_group(MetricName.MCP_METRICS)

        # Assert
        assert len(result) == 2
        assert "create_assistant" in result
        assert "update_assistant" in result

    def test_to_list_from_group_usage_metrics(self):
        """Verify USAGE_METRICS group conversion.

        Arrange: USAGE_METRICS group
        Act: Call to_list_from_group
        Assert: Returns correct list of 2 usage metric strings
        """
        # Arrange & Act
        result = MetricName.to_list_from_group(MetricName.USAGE_METRICS)

        # Assert
        assert len(result) == 2
        assert "conversation_assistant_usage" in result
        assert "workflow_execution_total" in result

    def test_to_list_from_group_with_empty_list(self):
        """Verify edge case with empty list.

        Arrange: Empty list
        Act: Call to_list_from_group
        Assert: Returns empty list
        """
        # Arrange & Act
        result = MetricName.to_list_from_group([])

        # Assert
        assert result == []
        assert isinstance(result, list)

    def test_to_list_from_group_handles_mixed_types(self):
        """Verify robustness when list contains non-enum values.

        Arrange: List with MetricName enum and plain string
        Act: Call to_list_from_group
        Assert: Extracts .value from enum, preserves plain strings
        """
        # Arrange
        mixed_list = [MetricName.CONVERSATION_ASSISTANT_USAGE, "plain_string"]

        # Act
        result = MetricName.to_list_from_group(mixed_list)

        # Assert
        assert len(result) == 2
        assert "conversation_assistant_usage" in result
        assert "plain_string" in result


class TestCLIToolUsageTotalConstant:
    """Test new CLI_TOOL_USAGE_TOTAL constant."""

    def test_cli_tool_usage_total_constant_exists(self):
        """Verify CLI_TOOL_USAGE_TOTAL constant is defined.

        Assert: Constant exists with correct value
        """
        # Assert
        assert hasattr(MetricName, 'CLI_TOOL_USAGE_TOTAL')
        assert MetricName.CLI_TOOL_USAGE_TOTAL.value == "codemie_cli_tool_usage_total"

    def test_cli_tool_usage_total_in_summary_metrics(self):
        """Verify CLI_TOOL_USAGE_TOTAL is included in SUMMARY_METRICS.

        Assert: New metric is in SUMMARY_METRICS group
        """
        # Assert
        assert MetricName.CLI_TOOL_USAGE_TOTAL in MetricName.SUMMARY_METRICS

    def test_both_cli_metrics_coexist(self):
        """Verify both old and new CLI metrics coexist for backward compatibility.

        Assert: Both CLI_TOOL_USAGE_TOTAL and CLI_COMMAND_EXECUTION_TOTAL are defined
        """
        # Assert
        assert MetricName.CLI_TOOL_USAGE_TOTAL.value == "codemie_cli_tool_usage_total"
        assert MetricName.CLI_COMMAND_EXECUTION_TOTAL.value == "codemie_cli_usage_total"
        # Both should be in SUMMARY_METRICS
        assert MetricName.CLI_TOOL_USAGE_TOTAL in MetricName.SUMMARY_METRICS
        assert MetricName.CLI_COMMAND_EXECUTION_TOTAL in MetricName.SUMMARY_METRICS


class TestMetricGroups:
    """Test metric group constant definitions."""

    def test_summary_metrics_group_is_defined(self):
        """Verify SUMMARY_METRICS constant structure.

        Assert: Exists, is a list, contains correct 6 MetricName members (includes both old and new CLI metrics)
        """
        # Assert
        assert hasattr(MetricName, 'SUMMARY_METRICS')
        assert isinstance(MetricName.SUMMARY_METRICS, list)
        assert len(MetricName.SUMMARY_METRICS) == 6

        assert MetricName.CONVERSATION_ASSISTANT_USAGE in MetricName.SUMMARY_METRICS
        assert MetricName.DATASOURCE_TOKENS_USAGE in MetricName.SUMMARY_METRICS
        assert MetricName.WORKFLOW_EXECUTION_TOTAL in MetricName.SUMMARY_METRICS
        assert MetricName.CLI_TOOL_USAGE_TOTAL in MetricName.SUMMARY_METRICS  # NEW CLI metric (primary)
        assert (
            MetricName.CLI_COMMAND_EXECUTION_TOTAL in MetricName.SUMMARY_METRICS
        )  # OLD CLI metric (backward compatibility)
        assert MetricName.CLI_LLM_USAGE_TOTAL in MetricName.SUMMARY_METRICS  # CLI LLM proxy metric

    def test_tools_metrics_group_is_defined(self):
        """Verify TOOLS_METRICS constant structure.

        Assert: Exists, contains correct tool metrics
        """
        # Assert
        assert hasattr(MetricName, 'TOOLS_METRICS')
        assert isinstance(MetricName.TOOLS_METRICS, list)
        assert len(MetricName.TOOLS_METRICS) == 3

        assert MetricName.CODEMIE_TOOLS_USAGE_TOTAL in MetricName.TOOLS_METRICS
        assert MetricName.CODEMIE_TOOLS_USAGE_TOKENS in MetricName.TOOLS_METRICS
        assert MetricName.CODEMIE_TOOLS_USAGE_ERRORS in MetricName.TOOLS_METRICS

    def test_mcp_metrics_group_is_defined(self):
        """Verify MCP_METRICS constant structure.

        Assert: Exists, contains correct MCP metrics
        """
        # Assert
        assert hasattr(MetricName, 'MCP_METRICS')
        assert isinstance(MetricName.MCP_METRICS, list)
        assert len(MetricName.MCP_METRICS) == 2

        assert MetricName.MCP_CREATE_ASSISTANT in MetricName.MCP_METRICS
        assert MetricName.MCP_UPDATE_ASSISTANT in MetricName.MCP_METRICS

    def test_usage_metrics_group_is_defined(self):
        """Verify USAGE_METRICS constant structure.

        Assert: Exists, contains correct usage metrics
        """
        # Assert
        assert hasattr(MetricName, 'USAGE_METRICS')
        assert isinstance(MetricName.USAGE_METRICS, list)
        assert len(MetricName.USAGE_METRICS) == 2

        assert MetricName.CONVERSATION_ASSISTANT_USAGE in MetricName.USAGE_METRICS
        assert MetricName.WORKFLOW_EXECUTION_TOTAL in MetricName.USAGE_METRICS

    def test_all_group_members_are_valid_metric_names(self):
        """Verify metric groups only contain valid MetricName enum members.

        Arrange: Get all metric groups
        Act: Check each member
        Assert: All members are valid MetricName enum instances
        """
        # Arrange
        all_groups = [
            MetricName.SUMMARY_METRICS,
            MetricName.TOOLS_METRICS,
            MetricName.MCP_METRICS,
            MetricName.USAGE_METRICS,
        ]

        # Act & Assert
        for group in all_groups:
            for member in group:
                assert isinstance(member, MetricName), f"Group member {member} should be a MetricName enum instance"

                # Verify it's a valid enum value
                assert member in MetricName, f"Member {member} should be a valid MetricName enum value"


class TestMetricNameIntegration:
    """Integration tests for metric groups and enum usage patterns."""

    def test_metric_groups_allow_overlapping_members(self):
        """Verify metric groups can have overlapping members (by design).

        Arrange: Check SUMMARY_METRICS and USAGE_METRICS
        Act: Find common metrics
        Assert: CONVERSATION_ASSISTANT_USAGE and WORKFLOW_EXECUTION_TOTAL appear in both
        """
        # Arrange & Act
        summary_set = set(MetricName.SUMMARY_METRICS)
        usage_set = set(MetricName.USAGE_METRICS)
        overlap = summary_set & usage_set

        # Assert
        assert (
            MetricName.CONVERSATION_ASSISTANT_USAGE in overlap
        ), "CONVERSATION_ASSISTANT_USAGE should appear in both groups"
        assert MetricName.WORKFLOW_EXECUTION_TOTAL in overlap, "WORKFLOW_EXECUTION_TOTAL should appear in both groups"
        assert len(overlap) == 2, "Exactly 2 metrics should overlap between groups"

    def test_all_enum_members_exist(self):
        """Verify critical enum members are defined.

        Assert: Key metrics from different categories exist
        """
        # Assert core usage metrics
        assert hasattr(MetricName, 'CONVERSATION_ASSISTANT_USAGE')
        assert hasattr(MetricName, 'DATASOURCE_TOKENS_USAGE')
        assert hasattr(MetricName, 'WORKFLOW_EXECUTION_TOTAL')

        # Assert tools metrics
        assert hasattr(MetricName, 'CODEMIE_TOOLS_USAGE_TOTAL')
        assert hasattr(MetricName, 'CODEMIE_TOOLS_USAGE_TOKENS')
        assert hasattr(MetricName, 'CODEMIE_TOOLS_USAGE_ERRORS')

        # Assert MCP metrics
        assert hasattr(MetricName, 'MCP_CREATE_ASSISTANT')
        assert hasattr(MetricName, 'MCP_UPDATE_ASSISTANT')

        # Assert CLI metrics
        assert hasattr(MetricName, 'CLI_TOOL_USAGE_TOTAL')  # New CLI metric
        assert hasattr(MetricName, 'CLI_COMMAND_EXECUTION_TOTAL')  # Old CLI metric
        assert hasattr(MetricName, 'CLI_ERROR_TOTAL')

        # Assert budget metrics
        assert hasattr(MetricName, 'BUDGET_SOFT_LIMIT_WARNING')
        assert hasattr(MetricName, 'BUDGET_HARD_LIMIT_VIOLATION')

    def test_to_list_and_to_list_from_group_produce_same_result_for_explicit_metrics(self):
        """Verify both methods produce equivalent results.

        Arrange: Use same metrics via to_list and via group
        Act: Call both methods
        Assert: Results are equivalent (order may differ)
        """
        # Arrange & Act - Include all CLI metrics to match SUMMARY_METRICS
        explicit_result = MetricName.to_list(
            MetricName.CONVERSATION_ASSISTANT_USAGE,
            MetricName.DATASOURCE_TOKENS_USAGE,
            MetricName.WORKFLOW_EXECUTION_TOTAL,
            MetricName.CLI_TOOL_USAGE_TOTAL,  # New CLI metric
            MetricName.CLI_COMMAND_EXECUTION_TOTAL,  # Old CLI metric
            MetricName.CLI_LLM_USAGE_TOTAL,
        )

        group_result = MetricName.to_list_from_group(MetricName.SUMMARY_METRICS)

        # Assert
        assert set(explicit_result) == set(group_result), "Both methods should produce equivalent metric sets"
        assert len(explicit_result) == len(group_result), "Both methods should produce same number of metrics"
