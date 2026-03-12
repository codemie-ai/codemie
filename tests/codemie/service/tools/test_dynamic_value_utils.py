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

"""
Tests for dynamic_value_utils module.

This module tests the dynamic value processing functionality that enables
template resolution and context-aware value processing in workflows.
"""

from unittest.mock import patch

from codemie.service.tools.dynamic_value_utils import (
    process_string,
    process_values,
    _resolve_dynamic_values,
)


class TestProcessString:
    """Tests for the process_string function."""

    def test_process_string_with_no_context(self):
        """Test process_string with no context returns original string."""
        source = "Hello World"
        result = process_string(source, [])
        assert result == "Hello World"

    def test_process_string_with_none_context(self):
        """Test process_string with None context returns original string."""
        source = "Hello World"
        result = process_string(source, None)
        assert result == "Hello World"

    def test_process_string_with_simple_template(self):
        """Test process_string with simple Jinja2 template."""
        source = "Hello {{name}}"
        context = [{"name": "World"}]
        result = process_string(source, context)
        assert result == "Hello World"

    def test_process_string_with_complex_template(self):
        """Test process_string with flattened context variables."""
        source = "User {{name}} has {{score}} points"
        context = [{"name": "Alice", "score": 100}]
        result = process_string(source, context)
        assert result == "User Alice has 100 points"

    def test_process_string_with_missing_template_var(self):
        """Test process_string with missing template variable renders empty."""
        source = "Hello {{missing_var}}"
        context = [{"name": "World"}]
        result = process_string(source, context)
        assert result == "Hello "

    def test_process_string_with_multiple_context_items(self):
        """Test process_string with multiple context items."""
        source = "{{greeting}} {{name}}"
        context = [{"greeting": "Hello"}, {"name": "World"}]
        result = process_string(source, context)
        assert result == "Hello World"

    def test_process_string_with_json_dict_in_context(self):
        """Test process_string with dictionary values in context."""
        source = "Data: {{data}}"
        context = [{"data": {"key": "value", "number": 42}}]
        result = process_string(source, context)
        # Jinja2 renders dicts with Python's str() representation (single quotes)
        expected = "Data: {'key': 'value', 'number': 42}"
        assert result == expected


class TestProcessValues:
    """Tests for the process_values function."""

    def test_process_values_empty_source(self):
        """Test process_values with empty source values."""
        result = process_values({}, [{"key": "value"}])
        assert result == {}

    def test_process_values_empty_context(self):
        """Test process_values with empty context returns copy of source."""
        source = {"key1": "value1", "key2": "value2"}
        result = process_values(source, [])
        assert result == source
        assert result is not source  # Should be a copy

    def test_process_values_none_context(self):
        """Test process_values with None context returns copy of source."""
        source = {"key1": "value1", "key2": "value2"}
        result = process_values(source, None)
        assert result == source
        assert result is not source  # Should be a copy

    def test_process_values_simple_template_resolution(self):
        """Test process_values with simple template resolution."""
        source = {"greeting": "Hello {{name}}", "static": "unchanged"}
        context = [{"name": "World"}]
        result = process_values(source, context)
        expected = {"greeting": "Hello World", "static": "unchanged"}
        assert result == expected

    def test_process_values_context_value_override(self):
        """Test process_values where context provides direct value override."""
        source = {"key1": "original", "key2": "{{template}}"}
        context = [{"key1": "overridden", "template": "resolved"}]
        result = process_values(source, context)
        expected = {"key1": "overridden", "key2": "resolved"}
        assert result == expected

    def test_process_values_json_serialization(self):
        """Test process_values with dict values in templates."""
        source = {"data": "Value: {{info}}"}
        complex_data = {"nested": {"array": [1, 2, 3], "bool": True}}
        context = [{"info": complex_data}]
        result = process_values(source, context)
        # Jinja2 renders dicts with Python's str() representation (single quotes)
        expected_repr = "{'nested': {'array': [1, 2, 3], 'bool': True}}"
        assert result == {"data": f"Value: {expected_repr}"}

    def test_process_values_multiple_context_sources(self):
        """Test process_values with multiple context dictionaries."""
        source = {"msg": "{{greeting}} {{name}} {{punctuation}}"}
        context = [{"greeting": "Hello"}, {"name": "Alice"}, {"punctuation": "!"}]
        result = process_values(source, context)
        assert result == {"msg": "Hello Alice !"}

    def test_process_values_later_context_overrides_earlier(self):
        """Test that later context items override earlier ones."""
        source = {"value": "{{key}}"}
        context = [{"key": "first"}, {"key": "second"}]
        result = process_values(source, context)
        assert result == {"value": "second"}

    def test_process_values_non_string_values(self):
        """Test process_values handles non-string values correctly."""
        source = {"number": 42, "list": [1, 2, 3], "dict": {"key": "val"}}
        context = [{"number": 100}]  # Try to override
        result = process_values(source, context)
        # Non-string values should be overridden by context if key matches
        expected = {"number": 100, "list": [1, 2, 3], "dict": {"key": "val"}}
        assert result == expected

    @patch('codemie.service.tools.dynamic_value_utils.logger')
    def test_process_values_error_handling(self, mock_logger):
        """Test process_values error handling with invalid template."""
        source = {"bad_template": "{{invalid syntax"}
        context = [{"some": "context"}]
        result = process_values(source, context)
        # Should keep original value on template error
        assert result == {"bad_template": "{{invalid syntax"}
        mock_logger.warning.assert_called_once()


class TestResolveynamicValues:
    """Tests for the _resolve_dynamic_values helper function."""

    def test_resolve_with_context_value_override(self):
        """Test _resolve_dynamic_values with direct context override."""
        result = _resolve_dynamic_values("key", "original", {"key": "overridden"})
        assert result == "overridden"

    def test_resolve_with_empty_value_and_context(self):
        """Test _resolve_dynamic_values with None value and context available."""
        result = _resolve_dynamic_values("key", None, {"key": "from_context"})
        assert result == "from_context"

    def test_resolve_with_empty_value_no_context(self):
        """Test _resolve_dynamic_values with None value and no matching context."""
        result = _resolve_dynamic_values("key", None, {"other": "value"})
        assert result is None

    def test_resolve_non_string_passthrough(self):
        """Test _resolve_dynamic_values handles non-string values."""
        # Test with context override (happens first for any key match)
        result = _resolve_dynamic_values("key", 42, {"key": "overridden"})
        assert result == "overridden"  # Context override takes precedence

        # Test without context override
        result = _resolve_dynamic_values("key", 42, {"other": "value"})
        assert result == 42  # Non-string returned as-is when no context override

        # Test None with context
        result = _resolve_dynamic_values("key", None, {"key": "from_context"})
        assert result == "from_context"

        # Test None without context
        result = _resolve_dynamic_values("key", None, {"other": "value"})
        assert result is None

    def test_resolve_template_rendering(self):
        """Test _resolve_dynamic_values renders Jinja2 templates."""
        template = "Hello {{name}} from {{place}}"
        context = {"name": "Alice", "place": "Wonderland"}
        result = _resolve_dynamic_values("greeting", template, context)
        assert result == "Hello Alice from Wonderland"

    def test_resolve_template_with_filters(self):
        """Test _resolve_dynamic_values with Jinja2 filters."""
        template = "{{name | upper}} lives in {{city | lower}}"
        context = {"name": "alice", "city": "NEW YORK"}
        result = _resolve_dynamic_values("info", template, context)
        assert result == "ALICE lives in new york"

    def test_resolve_template_with_conditionals(self):
        """Test _resolve_dynamic_values with Jinja2 conditionals."""
        template = "Status: {% if active %}Active{% else %}Inactive{% endif %}"

        # Test with active=True
        result = _resolve_dynamic_values("status", template, {"active": True})
        assert result == "Status: Active"

        # Test with active=False
        result = _resolve_dynamic_values("status", template, {"active": False})
        assert result == "Status: Inactive"

    @patch('codemie.service.tools.dynamic_value_utils.logger')
    def test_resolve_template_error_handling(self, mock_logger):
        """Test _resolve_dynamic_values handles template errors gracefully."""
        bad_template = "{{ undefined_function() }}"
        result = _resolve_dynamic_values("key", bad_template, {})

        # Should return original template on error
        assert result == bad_template
        mock_logger.warning.assert_called_once()
        assert "Failed to render template" in str(mock_logger.warning.call_args)


class TestIntegrationScenarios:
    """Integration tests for common usage scenarios."""

    def test_workflow_node_argument_processing(self):
        """Test scenario similar to workflow node argument processing."""
        tool_args = {
            "url": "{{base_url}}/api/{{endpoint}}",
            "method": "{{http_method}}",
            "headers": "Bearer {{token}}",  # Non-dict to allow template processing
            "static_param": "unchanged",
        }

        input_messages = [
            {"base_url": "https://api.example.com", "endpoint": "users", "http_method": "GET", "token": "abc123"}
        ]

        result = process_values(tool_args, input_messages)

        expected = {
            "url": "https://api.example.com/api/users",
            "method": "GET",
            "headers": "Bearer abc123",
            "static_param": "unchanged",
        }
        assert result == expected

    def test_mcp_server_argument_preprocessing(self):
        """Test scenario for MCP server argument preprocessing."""
        server_args = ["--config={{config_path}}", "--port={{port}}", "--debug={{debug_mode}}"]

        context = [{"config_path": "/app/config.json", "port": "8080", "debug_mode": "true"}]

        # Process each arg individually (simulating MCP preprocessing)
        processed_args = [process_string(arg, context) for arg in server_args]

        expected = ["--config=/app/config.json", "--port=8080", "--debug=true"]
        assert processed_args == expected

    def test_agent_prompt_processing(self):
        """Test scenario for agent prompt dynamic processing."""
        prompt_template = """
        You are working on project {{project_name}}.
        Current task: {{current_task}}
        Available data: {{data_summary}}
        Instructions: {{instructions}}
        """.strip()

        context = [
            {
                "project_name": "AI Assistant",
                "current_task": "Process user request",
                "data_summary": "3 files, 2 databases",
                "instructions": "Be helpful and accurate",
            }
        ]

        result = process_string(prompt_template, context)

        expected = """You are working on project AI Assistant.
        Current task: Process user request
        Available data: 3 files, 2 databases
        Instructions: Be helpful and accurate"""

        assert result == expected

    def test_complex_nested_data_handling(self):
        """Test handling of complex nested data structures."""
        source = {
            "config": "Environment: {{env_name}} ({{env_type}})",
            "connection": "{{db_host}}:{{db_port}}/{{db_name}}",
            "metadata": "{{metadata}}",
        }

        context = [
            {
                "env_name": "production",
                "env_type": "cloud",
                "db_host": "db.example.com",
                "db_port": 5432,
                "db_name": "myapp",
                "metadata": {"version": "1.0", "tags": ["api", "service"]},
            }
        ]

        result = process_values(source, context)

        # The metadata field is a direct context override (not a template variable in a string),
        # so it gets the dict value directly from context
        expected = {
            "config": "Environment: production (cloud)",
            "connection": "db.example.com:5432/myapp",
            "metadata": {"version": "1.0", "tags": ["api", "service"]},
        }
        assert result == expected


class TestRecursiveDynamicValues:
    """Tests for recursive dynamic value resolution functionality."""

    def test_simple_recursive_resolution(self):
        """Test basic recursive resolution."""
        source_values = {"output": "Final: {{resolved_value}}"}
        context = [{"base": "hello", "resolved_value": "{{base}} world"}]

        result = process_values(source_values, context)

        expected = {"output": "Final: hello world"}
        assert result == expected

    def test_multi_level_recursion(self):
        """Test multiple levels of recursive resolution."""
        source_values = {"final": "{{level3}}"}
        context = [{"level1": "base", "level2": "{{level1}}_middle", "level3": "{{level2}}_top"}]

        result = process_values(source_values, context)

        expected = {"final": "base_middle_top"}
        assert result == expected

    def test_complex_recursive_scenario(self):
        """Test the example from the requirements."""
        source_values = {"result": "{{d}}"}
        context = [
            {
                'a': 'value1',
                'b': 'value2',
                'c': '{{a}} some text1 {{b}}',
                'd': '{{c}} some text2 {{a}} some text 3 {{b}}',
            }
        ]

        result = process_values(source_values, context)

        expected = {"result": "value1 some text1 value2 some text2 value1 some text 3 value2"}
        assert result == expected

    def test_circular_dependency_handling(self):
        """Test graceful handling of circular dependencies."""
        source_values = {"output": "{{a}}"}
        context = [{"a": "{{b}}", "b": "{{a}}", "c": "independent"}]

        # Should not crash and should handle gracefully
        result = process_values(source_values, context)

        # Circular dependencies result in empty values when template can't be resolved
        # This is expected behavior - the system handles it gracefully without crashing
        assert result["output"] == ""  # Circular dependency results in empty value
        assert isinstance(result, dict)  # Function completes successfully

    def test_recursive_resolution_disabled(self):
        """Test that recursive resolution can be disabled."""
        source_values = {"output": "{{resolved}}"}
        context = [{"base": "hello", "resolved": "{{base}} world"}]

        # Disable recursive resolution
        result = process_values(source_values, context, enable_recursive_resolution=False)

        # Should not resolve the nested template
        expected = {"output": "{{base}} world"}
        assert result == expected


class TestDependencyAnalysis:
    """Tests for the internal dependency analysis functions."""

    def test_extract_template_dependencies(self):
        """Test template dependency extraction."""
        from codemie.service.tools.dynamic_value_utils import _extract_template_dependencies

        # Simple variable
        deps = _extract_template_dependencies("{{var}}")
        assert deps == {"var"}

        # Multiple variables
        deps = _extract_template_dependencies("{{a}} and {{b}}")
        assert deps == {"a", "b"}

        # No variables
        deps = _extract_template_dependencies("no templates here")
        assert deps == set()

    def test_build_dependency_graph(self):
        """Test dependency graph building."""
        from codemie.service.tools.dynamic_value_utils import _build_dependency_graph

        dynamic_values = {
            "a": "base_value",
            "b": "{{a}}_suffix",
            "c": "{{b}}_more",
            "d": 42,  # non-string
        }

        graph = _build_dependency_graph(dynamic_values)

        expected = {"a": set(), "b": {"a"}, "c": {"b"}, "d": set()}
        assert graph == expected

    def test_topological_sort(self):
        """Test topological sorting."""
        from codemie.service.tools.dynamic_value_utils import _topological_sort

        # Simple dependency chain
        graph = {"a": set(), "b": {"a"}, "c": {"b"}}
        order = _topological_sort(graph)

        # a should come before b, b should come before c
        assert order.index("a") < order.index("b")
        assert order.index("b") < order.index("c")
