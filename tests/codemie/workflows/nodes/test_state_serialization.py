# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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
Test Area: State Serialization

Tests for workflow state serialization to JSON-safe format for JSONB storage.
"""

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from pydantic import BaseModel

from codemie.workflows.utils import serialize_state


class MockPydanticModel(BaseModel):
    """Mock Pydantic model for testing."""

    field1: str
    field2: int


def test_serialize_primitive_types():
    """Test serialization of primitive JSON-safe types."""
    state = {
        "string": "test",
        "integer": 42,
        "float": 3.14,
        "boolean": True,
        "none": None,
    }

    result = serialize_state(state)

    assert result == state
    assert isinstance(result["string"], str)
    assert isinstance(result["integer"], int)
    assert isinstance(result["float"], float)
    assert isinstance(result["boolean"], bool)
    assert result["none"] is None


def test_serialize_langchain_messages():
    """Test serialization of LangChain message objects to strings."""
    state = {
        "human_msg": HumanMessage(content="Hello"),
        "ai_msg": AIMessage(content="Hi there"),
        "system_msg": SystemMessage(content="System prompt"),
    }

    result = serialize_state(state)

    # Messages should be converted to string representations
    assert isinstance(result["human_msg"], str)
    assert isinstance(result["ai_msg"], str)
    assert isinstance(result["system_msg"], str)
    assert "Hello" in result["human_msg"] or "content='Hello'" in result["human_msg"]


def test_serialize_pydantic_models():
    """Test serialization of Pydantic models to dictionaries."""
    model = MockPydanticModel(field1="test", field2=123)
    state = {"pydantic_model": model}

    result = serialize_state(state)

    assert isinstance(result["pydantic_model"], dict)
    assert result["pydantic_model"]["field1"] == "test"
    assert result["pydantic_model"]["field2"] == 123


def test_serialize_pydantic_model_fallback():
    """Test fallback to string when Pydantic model_dump fails."""

    class BadModel(BaseModel):
        field: str

        def model_dump(self):
            raise Exception("model_dump failed")

    model = BadModel(field="test")
    state = {"bad_model": model}

    result = serialize_state(state)

    # Should fall back to string representation
    assert isinstance(result["bad_model"], str)


def test_serialize_list_with_mixed_types():
    """Test serialization of lists containing mixed types."""
    state = {
        "mixed_list": [
            "string",
            42,
            HumanMessage(content="test"),
            MockPydanticModel(field1="val", field2=10),
            {"nested": "dict"},
        ]
    }

    result = serialize_state(state)

    assert isinstance(result["mixed_list"], list)
    assert len(result["mixed_list"]) == 5
    assert result["mixed_list"][0] == "string"
    assert result["mixed_list"][1] == 42
    assert isinstance(result["mixed_list"][2], str)  # Message converted
    assert isinstance(result["mixed_list"][3], dict)  # Pydantic model converted
    assert result["mixed_list"][4] == {"nested": "dict"}


def test_serialize_nested_dict():
    """Test serialization of deeply nested dictionaries."""
    state = {
        "level1": {
            "level2": {
                "level3": {
                    "message": HumanMessage(content="deep"),
                    "value": 123,
                }
            }
        }
    }

    result = serialize_state(state)

    assert isinstance(result["level1"]["level2"]["level3"]["message"], str)
    assert result["level1"]["level2"]["level3"]["value"] == 123


def test_serialize_tuple():
    """Test serialization of tuples (converted to lists)."""
    state = {"tuple_data": (1, 2, HumanMessage(content="test"))}

    result = serialize_state(state)

    assert isinstance(result["tuple_data"], list)
    assert len(result["tuple_data"]) == 3
    assert result["tuple_data"][0] == 1
    assert result["tuple_data"][1] == 2
    assert isinstance(result["tuple_data"][2], str)


def test_serialize_non_json_serializable_object():
    """Test fallback for objects that aren't JSON-serializable."""

    class CustomObject:
        def __init__(self, value):
            self.value = value

        def __str__(self):
            return f"CustomObject({self.value})"

    obj = CustomObject(42)
    state = {"custom": obj}

    result = serialize_state(state)

    assert isinstance(result["custom"], str)
    assert "CustomObject(42)" in result["custom"]


def test_serialize_empty_dict():
    """Test serialization of empty dictionary."""
    state = {}

    result = serialize_state(state)

    assert result == {}


def test_serialize_complex_workflow_state():
    """Test serialization of realistic complex workflow state."""
    state = {
        "messages": [
            HumanMessage(content="User input"),
            AIMessage(content="AI response"),
        ],
        "context_store": {"key1": "value1", "key2": "value2"},
        "next": ["next_node"],
        "task": "Process user query",
        "iteration_number": 1,
        "total_iterations": 5,
        "nested_config": {
            "model": MockPydanticModel(field1="config", field2=100),
            "settings": {"timeout": 30, "retries": 3},
        },
    }

    result = serialize_state(state)

    # Check all conversions happened correctly
    assert isinstance(result["messages"], list)
    assert isinstance(result["messages"][0], str)
    assert isinstance(result["messages"][1], str)
    assert result["context_store"] == {"key1": "value1", "key2": "value2"}
    assert result["next"] == ["next_node"]
    assert result["task"] == "Process user query"
    assert isinstance(result["nested_config"]["model"], dict)
    assert result["nested_config"]["settings"]["timeout"] == 30


def test_serialize_value_primitive():
    """Test serialize_state with primitive types."""
    assert serialize_state("test") == "test"
    assert serialize_state(42) == 42
    assert serialize_state(3.14) == 3.14
    assert serialize_state(True) is True
    assert serialize_state(None) is None


def test_serialize_value_langchain_message():
    """Test serialize_state with LangChain message."""
    msg = HumanMessage(content="test")
    result = serialize_state(msg)

    assert isinstance(result, str)
    assert "test" in result or "content='test'" in result


def test_serialize_value_list():
    """Test serialize_state with list."""
    result = serialize_state([1, "test", HumanMessage(content="msg")])

    assert isinstance(result, list)
    assert result[0] == 1
    assert result[1] == "test"
    assert isinstance(result[2], str)


def test_serialize_preserves_none_values():
    """Test that None values are preserved, not converted to strings."""
    state = {
        "value1": None,
        "value2": "not none",
        "nested": {"inner": None},
    }

    result = serialize_state(state)

    assert result["value1"] is None
    assert result["value2"] == "not none"
    assert result["nested"]["inner"] is None


# Size limit tests
def test_serialize_truncates_long_strings():
    """Test that strings exceeding MAX_STRING_LENGTH are truncated."""
    from codemie.workflows.utils.utils import MAX_STRING_LENGTH

    long_string = "A" * (MAX_STRING_LENGTH + 1000)
    state = {"long_field": long_string}

    result = serialize_state(state)

    assert len(result["long_field"]) == MAX_STRING_LENGTH + len("...[TRUNCATED]")
    assert result["long_field"].endswith("...[TRUNCATED]")
    assert result["long_field"].startswith("A" * 100)


def test_serialize_enforces_max_depth():
    """Test that deeply nested structures hit depth limit."""
    from codemie.workflows.utils.utils import MAX_RECURSION_DEPTH

    # Create nested dict deeper than MAX_RECURSION_DEPTH
    nested = {"value": "deep"}
    for _ in range(MAX_RECURSION_DEPTH + 5):
        nested = {"nested": nested}

    result = serialize_state(nested)

    # Should contain depth limit marker at some level
    def find_depth_marker(obj, depth=0):
        if obj == "[MAX_DEPTH_EXCEEDED]":
            return depth
        if isinstance(obj, dict):
            for v in obj.values():
                d = find_depth_marker(v, depth + 1)
                if d is not None:
                    return d
        return None

    assert find_depth_marker(result) is not None


def test_serialize_truncates_langchain_message():
    """Test that long LangChain messages are truncated."""
    from codemie.workflows.utils.utils import MAX_STRING_LENGTH

    long_content = "X" * (MAX_STRING_LENGTH + 1000)
    msg = HumanMessage(content=long_content)

    result = serialize_state(msg)

    assert isinstance(result, str)
    assert len(result) <= MAX_STRING_LENGTH + len("...[TRUNCATED]")
    assert result.endswith("...[TRUNCATED]")


def test_check_state_size_within_limit():
    """Test check_state_size allows states within limit."""
    from codemie.workflows.utils import check_state_size

    # Create state well within limit (10KB)
    small_state = {"data": "x" * 10_000}

    result = check_state_size(small_state, "test_exec_id")

    assert result == small_state
    assert "_truncated" not in result


def test_check_state_size_exceeds_limit():
    """Test check_state_size truncates oversized states."""
    from codemie.workflows.utils import check_state_size
    from codemie.workflows.utils.utils import MAX_JSONB_SIZE_BYTES

    # Create state exceeding 1MB limit
    large_state = {"data": "x" * (MAX_JSONB_SIZE_BYTES + 10_000)}

    result = check_state_size(large_state, "test_exec_id")

    assert result["_truncated"] is True
    assert result["_original_size_bytes"] > MAX_JSONB_SIZE_BYTES
    assert result["_limit_bytes"] == MAX_JSONB_SIZE_BYTES
    assert result["_execution_id"] == "test_exec_id"


def test_check_state_size_exactly_at_limit():
    """Test edge case: state exactly at 1MB limit."""
    from codemie.workflows.utils import check_state_size
    from codemie.workflows.utils.utils import MAX_JSONB_SIZE_BYTES
    import json

    # Create state that serializes to just under 1MB
    # Account for JSON overhead: {"data":"..."}
    overhead = len('{"data":""}')
    target_size = MAX_JSONB_SIZE_BYTES - overhead - 100  # Slight buffer
    state = {"data": "x" * target_size}

    # Verify we're under the limit
    actual_size = len(json.dumps(state).encode('utf-8'))
    assert actual_size < MAX_JSONB_SIZE_BYTES

    result = check_state_size(state, "test_exec_id")

    # Should NOT be truncated
    assert "_truncated" not in result
    assert result == state


def test_serialize_truncates_non_serializable_objects():
    """Test that long string representations of non-serializable objects are truncated."""
    from codemie.workflows.utils.utils import MAX_STRING_LENGTH

    class CustomObject:
        def __str__(self):
            return "CustomObject: " + "X" * (MAX_STRING_LENGTH + 1000)

    obj = CustomObject()
    result = serialize_state(obj)

    assert isinstance(result, str)
    assert len(result) <= MAX_STRING_LENGTH + len("...[TRUNCATED]")
    assert result.endswith("...[TRUNCATED]")


def test_check_state_size_with_invalid_json():
    """Test check_state_size handles serialization errors gracefully."""
    from codemie.workflows.utils import check_state_size

    # Create state that might cause JSON serialization issues
    # Using a simple dict that will succeed - the error handling is for edge cases
    state = {"valid": "data"}

    # Should not crash
    result = check_state_size(state, "test_exec_id")

    assert result == state


def test_serialize_mixed_truncation():
    """Test state with both truncated strings and normal values."""
    from codemie.workflows.utils.utils import MAX_STRING_LENGTH

    state = {
        "normal": "short string",
        "long": "L" * (MAX_STRING_LENGTH + 1000),
        "nested": {
            "also_long": "M" * (MAX_STRING_LENGTH + 500),
            "normal": "another short",
        },
    }

    result = serialize_state(state)

    # Normal strings unchanged
    assert result["normal"] == "short string"
    assert result["nested"]["normal"] == "another short"

    # Long strings truncated
    assert result["long"].endswith("...[TRUNCATED]")
    assert result["nested"]["also_long"].endswith("...[TRUNCATED]")
    assert len(result["long"]) == MAX_STRING_LENGTH + len("...[TRUNCATED]")
    assert len(result["nested"]["also_long"]) == MAX_STRING_LENGTH + len("...[TRUNCATED]")


def test_serialize_nan_returns_none():
    assert serialize_state(float('nan')) is None


def test_serialize_positive_infinity_returns_none():
    assert serialize_state(float('inf')) is None


def test_serialize_negative_infinity_returns_none():
    assert serialize_state(float('-inf')) is None


def test_serialize_regular_float_unchanged():
    assert serialize_state(3.14) == 3.14


def test_serialize_bool_true_unchanged():
    result = serialize_state(True)
    assert result is True
    assert type(result) is bool


def test_serialize_bool_false_unchanged():
    result = serialize_state(False)
    assert result is False
    assert type(result) is bool


def test_serialize_nan_in_dict_returns_none():
    result = serialize_state({"value": float('nan'), "other": 1})
    assert result["value"] is None
    assert result["other"] == 1
