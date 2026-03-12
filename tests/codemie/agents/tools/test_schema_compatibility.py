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

"""Unit tests for schema_compatibility module."""

import json
import pytest

from codemie.agents.tools.schema_compatibility import (
    sanitize_tool_response_for_gcp,
    _remove_schema_refs_recursive,
    transform_schema_for_gcp_compatibility,
)


class TestSanitizeToolResponseForGCP:
    """Tests for sanitize_tool_response_for_gcp function."""

    def test_returns_none_for_none_input(self):
        """Test that None input returns None."""
        result = sanitize_tool_response_for_gcp(None)
        assert result is None

    def test_returns_empty_string_for_empty_input(self):
        """Test that empty string returns empty string."""
        result = sanitize_tool_response_for_gcp("")
        assert result == ""

    def test_returns_non_string_input_unchanged(self):
        """Test that non-string input is returned unchanged."""
        result = sanitize_tool_response_for_gcp(123)
        assert result == 123

    def test_content_without_schema_refs_unchanged(self):
        """Test that content without schema references is not modified."""
        test_cases = [
            '{"result": "success", "data": {"id": 123}}',
            '{"name": "test", "value": 42}',
            'Plain text response without any JSON',
            '{"nested": {"deeply": {"object": "value"}}}',
            '{"items": [1, 2, 3], "count": 3}',
        ]

        for content in test_cases:
            result = sanitize_tool_response_for_gcp(content)
            assert result == content, f"Content was modified: {content}"

    def test_vscode_edit_file_arguments_preserved(self):
        """Test that VS Code edit_file tool arguments are not corrupted.

        (edits, dryRun) were being corrupted by the sanitization.
        """
        edit_file_args = json.dumps(
            {
                "path": "alweb/web/creditcard.jsp",
                "edits": [{"oldText": "some old code", "newText": "some new code"}],
                "dryRun": False,
            }
        )

        result = sanitize_tool_response_for_gcp(edit_file_args)

        # Verify structure is preserved
        original_data = json.loads(edit_file_args)
        result_data = json.loads(result)

        assert "path" in result_data, "Missing 'path' field after sanitization"
        assert "edits" in result_data, "Missing 'edits' field after sanitization"
        assert "dryRun" in result_data, "Missing 'dryRun' field after sanitization"
        assert original_data == result_data, "Data was modified when it shouldn't have been"

    def test_removes_simple_schema_ref(self):
        """Test that simple $ref is removed from JSON."""
        content = json.dumps({"result": "success", "$ref": "#/components/schemas/Assistant", "data": {"id": 123}})

        result = sanitize_tool_response_for_gcp(content)
        result_data = json.loads(result)

        assert "$ref" not in result_data, "$ref was not removed"
        assert "result" in result_data, "result field was lost"
        assert "data" in result_data, "data field was lost"
        assert result_data["result"] == "success"
        assert result_data["data"]["id"] == 123

    def test_removes_nested_schema_refs(self):
        """Test that nested schema refs are removed."""
        content = json.dumps(
            {
                "assistant": {"$ref": "#/components/schemas/Assistant", "name": "MyAssistant", "id": 42},
                "tools": [{"$ref": "#/components/schemas/Tool", "name": "tool1"}, {"name": "tool2", "enabled": True}],
            }
        )

        result = sanitize_tool_response_for_gcp(content)
        result_data = json.loads(result)

        # Verify $ref keys are gone
        assert "$ref" not in str(result_data), "$ref still present in result"

        # Verify other data is preserved
        assert result_data["assistant"]["name"] == "MyAssistant"
        assert result_data["assistant"]["id"] == 42
        assert result_data["tools"][0]["name"] == "tool1"
        assert result_data["tools"][1]["name"] == "tool2"
        assert result_data["tools"][1]["enabled"] is True

    def test_result_is_valid_json_after_sanitization(self):
        """Test that result is always valid JSON if input was valid JSON."""
        test_cases = [
            '{"$ref": "#/components/schemas/Test", "data": "value"}',
            '{"nested": {"$ref": "#/components/schemas/Test"}}',
            '[{"$ref": "#/components/schemas/Test"}, {"name": "item2"}]',
            '{"a": {"$ref": "#/components/schemas/A"}, "b": {"c": {"$ref": "#/components/schemas/C"}}}',
        ]

        for content in test_cases:
            result = sanitize_tool_response_for_gcp(content)
            try:
                json.loads(result)  # Should not raise
            except json.JSONDecodeError as e:
                pytest.fail(f"Result is not valid JSON: {e}\nOriginal: {content}\nResult: {result}")

    def test_handles_non_json_content_with_schema_refs(self):
        """Test that non-JSON content with schema refs is handled gracefully."""
        content = 'Some text with "$ref": "#/components/schemas/Test" in it'

        result = sanitize_tool_response_for_gcp(content)

        # Should not contain the $ref pattern
        assert '"$ref"' not in result
        # Should still contain some of the original text
        assert "Some text" in result

    def test_preserves_complex_nested_structures(self):
        """Test that complex nested structures without refs are preserved."""
        complex_data = {
            "metadata": {"timestamp": "2025-11-21T10:00:00Z", "user": {"id": 123, "name": "test_user"}},
            "results": [
                {"id": 1, "value": "first", "nested": {"a": 1, "b": 2}},
                {"id": 2, "value": "second", "nested": {"c": 3, "d": 4}},
            ],
            "counts": {"total": 2, "processed": 2, "failed": 0},
        }
        content = json.dumps(complex_data)

        result = sanitize_tool_response_for_gcp(content)
        result_data = json.loads(result)

        assert result_data == complex_data, "Complex data structure was modified"

    def test_early_exit_optimization(self):
        """Test that function exits early when no refs present (optimization check)."""
        # This test verifies the optimization that skips processing
        # when there are clearly no schema references
        content = '{"large": "data", "with": ["lots", "of", "nested"], "content": true}'

        result = sanitize_tool_response_for_gcp(content)

        # Should be exactly the same object (not just equal content)
        assert result == content
        assert result is content  # Same object reference (early return)

    def test_removes_ref_with_trailing_comma(self):
        """Test removal of $ref with trailing comma."""
        content = '{"$ref": "#/components/schemas/Test", "name": "value"}'

        result = sanitize_tool_response_for_gcp(content)
        result_data = json.loads(result)

        assert "$ref" not in result_data
        assert result_data["name"] == "value"

    def test_removes_ref_without_trailing_comma(self):
        """Test removal of $ref without trailing comma (last item)."""
        content = '{"name": "value", "$ref": "#/components/schemas/Test"}'

        result = sanitize_tool_response_for_gcp(content)
        result_data = json.loads(result)

        assert "$ref" not in result_data
        assert result_data["name"] == "value"

    def test_handles_single_quotes_in_non_json(self):
        """Test handling of single-quoted refs in non-JSON content."""
        content = "Some string with '$ref': '#/components/schemas/Test' pattern"

        result = sanitize_tool_response_for_gcp(content)

        assert "'$ref'" not in result


class TestRemoveSchemaRefsRecursive:
    """Tests for _remove_schema_refs_recursive helper function."""

    def test_removes_ref_from_dict(self):
        """Test that $ref is removed from dictionary."""
        obj = {"name": "test", "$ref": "#/components/schemas/Test", "value": 42}

        result = _remove_schema_refs_recursive(obj)

        assert "$ref" not in result
        assert result["name"] == "test"
        assert result["value"] == 42

    def test_processes_nested_dicts(self):
        """Test that nested dictionaries are processed."""
        obj = {
            "outer": {
                "$ref": "#/components/schemas/Outer",
                "inner": {"$ref": "#/components/schemas/Inner", "value": "nested"},
            }
        }

        result = _remove_schema_refs_recursive(obj)

        assert "$ref" not in str(result)
        assert result["outer"]["inner"]["value"] == "nested"

    def test_processes_lists(self):
        """Test that lists are processed."""
        obj = [{"$ref": "#/components/schemas/Item1", "id": 1}, {"$ref": "#/components/schemas/Item2", "id": 2}]

        result = _remove_schema_refs_recursive(obj)

        assert "$ref" not in str(result)
        assert result[0]["id"] == 1
        assert result[1]["id"] == 2

    def test_returns_primitives_unchanged(self):
        """Test that primitive values are returned unchanged."""
        assert _remove_schema_refs_recursive("string") == "string"
        assert _remove_schema_refs_recursive(123) == 123
        assert _remove_schema_refs_recursive(True) is True
        assert _remove_schema_refs_recursive(None) is None


class TestTransformSchemaForGCPCompatibility:
    """Tests for transform_schema_for_gcp_compatibility function."""

    def test_converts_integer_to_number(self):
        """Test that integer types are converted to number for GCP."""
        schema = {"type": "object", "properties": {"age": {"type": "integer"}, "name": {"type": "string"}}}

        result = transform_schema_for_gcp_compatibility(schema)

        assert result["properties"]["age"]["type"] == "number"
        assert result["properties"]["name"]["type"] == "string"

    def test_simplifies_anyof_with_null(self):
        """Test that anyOf with null type is simplified."""
        schema = {"type": "object", "properties": {"optional_value": {"anyOf": [{"type": "string"}, {"type": "null"}]}}}

        result = transform_schema_for_gcp_compatibility(schema)

        # Should simplify to just string type
        assert "anyOf" not in result["properties"]["optional_value"]
        assert result["properties"]["optional_value"]["type"] == "string"

    def test_converts_integer_in_anyof(self):
        """Test that integer in anyOf is converted to number."""
        schema = {"anyOf": [{"type": "integer"}, {"type": "null"}]}

        result = transform_schema_for_gcp_compatibility(schema)

        # Should simplify and convert integer to number
        assert result["type"] == "number"

    def test_returns_non_dict_unchanged(self):
        """Test that non-dictionary schemas are returned unchanged."""
        assert transform_schema_for_gcp_compatibility("string") == "string"
        assert transform_schema_for_gcp_compatibility(None) is None
        assert transform_schema_for_gcp_compatibility(123) == 123

    def test_preserves_nested_structure(self):
        """Test that nested schema structure is preserved."""
        schema = {
            "type": "object",
            "properties": {
                "user": {"type": "object", "properties": {"id": {"type": "integer"}, "name": {"type": "string"}}}
            },
        }

        result = transform_schema_for_gcp_compatibility(schema)

        assert result["type"] == "object"
        assert result["properties"]["user"]["type"] == "object"
        assert result["properties"]["user"]["properties"]["id"]["type"] == "number"
        assert result["properties"]["user"]["properties"]["name"]["type"] == "string"
