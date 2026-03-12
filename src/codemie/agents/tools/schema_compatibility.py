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

"""Schema compatibility utilities for Google Vertex AI integration."""

import copy
import json
import re
from typing import Any, Dict, List


def _convert_integer_to_number(obj: Dict[str, Any]) -> None:
    """Convert integer type to number for GCP compatibility."""
    if obj.get("type") == "integer":
        obj["type"] = "number"


def _get_non_null_types(any_of_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract non-null types from anyOf items."""
    return [item for item in any_of_items if item.get("type") != "null"]


def _simplify_single_any_of(obj: Dict[str, Any], non_null_types: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Simplify anyOf with single non-null type."""
    result = copy.deepcopy(non_null_types[0])
    _convert_integer_to_number(result)

    # Keep other properties from the original object
    for key, value in obj.items():
        if key != "anyOf":
            result[key] = _transform_value(value)
    return result


def _transform_any_of(obj: Dict[str, Any]) -> None:
    """Transform anyOf constructs in the object."""
    any_of_items = obj["anyOf"]
    non_null_types = _get_non_null_types(any_of_items)

    if len(non_null_types) == 1:
        # Replace the entire object with the simplified version
        simplified = _simplify_single_any_of(obj, non_null_types)
        obj.clear()
        obj.update(simplified)
    else:
        # Transform each item in anyOf
        obj["anyOf"] = [_transform_value(item) for item in any_of_items]


def _transform_dict(obj: Dict[str, Any]) -> Dict[str, Any]:
    """Transform dictionary objects."""
    if "anyOf" in obj:
        _transform_any_of(obj)
        return obj

    _convert_integer_to_number(obj)

    # Recursively transform all values
    for key, value in obj.items():
        obj[key] = _transform_value(value)

    return obj


def _transform_value(obj: Any) -> Any:
    """Transform a value recursively based on its type."""
    if isinstance(obj, dict):
        return _transform_dict(obj)
    elif isinstance(obj, list):
        return [_transform_value(item) for item in obj]
    return obj


def transform_schema_for_gcp_compatibility(schema: Dict[str, Any]) -> Dict[str, Any]:
    """
    Transform JSON schema to be compatible with Google Vertex AI.

    Fixes issues with:
    - anyOf constructs that contain integer types
    - Converts 'integer' type to 'number' for GCP compatibility
    """
    if not isinstance(schema, dict):
        return schema

    # Work on a deep copy to avoid modifying the original
    schema_copy = copy.deepcopy(schema)
    return _transform_value(schema_copy)


def _remove_schema_refs_recursive(obj: Any) -> Any:
    """
    Recursively remove all $ref occurrences from the object.

    GCP Vertex AI rejects tool responses containing schema references like
    '#/components/schemas/Assistant'. This function removes all such references.
    """
    if isinstance(obj, dict):
        result = {}
        for key, value in obj.items():
            if key == "$ref":
                # Skip $ref keys entirely - GCP doesn't accept them in tool responses
                continue
            result[key] = _remove_schema_refs_recursive(value)
        return result
    elif isinstance(obj, list):
        return [_remove_schema_refs_recursive(item) for item in obj]
    return obj


def sanitize_tool_response_for_gcp(content: str) -> str:
    """
    Sanitize tool response content for GCP Vertex AI compatibility.

    GCP Vertex AI rejects tool responses containing OpenAPI schema references
    like '#/components/schemas/Assistant'. This function removes such references.

    IMPORTANT: Only sanitizes content that actually contains schema references
    to avoid corrupting valid JSON/tool responses.

    Args:
        content: Tool response content (may be JSON or plain text)

    Returns:
        Sanitized content with schema references removed (or original if no refs found)
    """
    if not content or not isinstance(content, str):
        return content

    # Quick check: if there are no schema references, return original content unchanged
    # This prevents unnecessary processing and potential corruption of valid responses
    if "$ref" not in content and "#/components/schemas" not in content:
        return content

    # Try to parse as JSON and remove refs structurally
    try:
        data = json.loads(content)
        # Remove all $ref keys recursively
        sanitized_data = _remove_schema_refs_recursive(data)
        # Validate that sanitization didn't corrupt the structure
        sanitized_json = json.dumps(sanitized_data)
        # Verify the sanitized JSON is still valid
        json.loads(sanitized_json)
        return sanitized_json
    except (json.JSONDecodeError, ValueError, TypeError):
        # Not valid JSON - try regex-based cleanup on the raw string
        # Only apply if we're confident there are schema references
        if not ('"$ref"' in content or "'$ref'" in content):
            # No clear schema references in string format, return unchanged
            return content

        sanitized = content

        # Pattern 1: Remove "$ref" key-value pairs (with proper JSON structure handling)
        # Match: "$ref": "#/..." including optional trailing comma
        sanitized = re.sub(r'"\$ref"\s*:\s*"#/[^"]*"(\s*,\s*)?', '', sanitized)

        # Pattern 2: Remove '$ref' key-value pairs (single quotes)
        sanitized = re.sub(r"'\$ref'\s*:\s*'#/[^']*'(\s*,\s*)?", '', sanitized)

        # Clean up resulting artifacts
        # Remove trailing commas before closing braces/brackets
        sanitized = re.sub(r',(\s*[}\]])', r'\1', sanitized)
        # Remove leading commas after opening braces/brackets
        sanitized = re.sub(r'([{\[])\s*,', r'\1', sanitized)
        # Remove empty objects that might have been created
        sanitized = re.sub(r'\{\s*\}', '{}', sanitized)

        return sanitized


def patch_langchain_google_vertexai():
    """
    Monkey patch langchain_google_vertexai to apply GCP compatibility fixes.

    This patches:
    1. Schema conversion for tool definitions
    2. Tool/Function message content sanitization to remove schema references
    """
    try:
        import langchain_google_vertexai.functions_utils as functions_utils
        from langchain_core.messages import FunctionMessage, ToolMessage

        # Patch 1: Schema conversion for tool definitions
        original_dict_to_gapic_schema = functions_utils._dict_to_gapic_schema

        def patched_dict_to_gapic_schema(json_schema: Dict[str, Any], **kwargs):
            """Patched version that applies GCP compatibility transforms."""
            transformed_schema = transform_schema_for_gcp_compatibility(json_schema)
            return original_dict_to_gapic_schema(transformed_schema, **kwargs)

        functions_utils._dict_to_gapic_schema = patched_dict_to_gapic_schema

        # Patch 2: Tool response message content sanitization
        original_tool_init = ToolMessage.__init__
        original_function_init = FunctionMessage.__init__

        def patched_tool_init(self, *args, **kwargs):
            """Patched ToolMessage.__init__ that sanitizes content for GCP."""
            original_tool_init(self, *args, **kwargs)
            if isinstance(self.content, str):
                self.content = sanitize_tool_response_for_gcp(self.content)

        def patched_function_init(self, *args, **kwargs):
            """Patched FunctionMessage.__init__ that sanitizes content for GCP."""
            original_function_init(self, *args, **kwargs)
            if isinstance(self.content, str):
                self.content = sanitize_tool_response_for_gcp(self.content)

        ToolMessage.__init__ = patched_tool_init
        FunctionMessage.__init__ = patched_function_init

    except ImportError:
        # langchain_google_vertexai not available, skip patching
        pass
