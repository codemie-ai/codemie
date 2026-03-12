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

from typing import Literal, get_args, get_origin
import pytest
from pydantic import ValidationError

from codemie.core.json_schema_utils import json_schema_to_model


def test_string_enum_schema():
    """Test handling of string enum schema conversion to Literal type."""
    # Define schema with string enum
    string_enum_schema = {
        "type": "object",
        "properties": {
            "color": {"type": "string", "enum": ["red", "green", "blue"], "description": "Primary color selection"}
        },
        "required": ["color"],
    }

    # Convert schema to model
    color_model = json_schema_to_model(string_enum_schema)

    # Check model field annotations
    color_field = color_model.model_fields["color"]
    color_annotation = color_field.annotation

    # Verify Literal type usage
    assert get_origin(color_annotation) is Literal
    assert get_args(color_annotation) == ("red", "green", "blue")
    assert color_field.description == "Primary color selection"

    # Test validation with valid value
    valid_instance = color_model(color="red")
    assert valid_instance.color == "red"

    # Test validation with invalid value
    with pytest.raises(ValidationError):
        color_model(color="yellow")


def test_mixed_type_enum_schema():
    """Test handling of mixed type enum schema conversion to Literal type."""
    # Define schema with mixed type enum
    mixed_enum_schema = {
        "type": "object",
        "properties": {"value": {"enum": ["allowed", 42, True, None], "description": "Mixed type enumeration"}},
        "required": ["value"],
    }

    # Convert schema to model
    mixed_model = json_schema_to_model(mixed_enum_schema)

    # Check model field annotations
    value_field = mixed_model.model_fields["value"]
    value_annotation = value_field.annotation

    # Verify Literal type usage with mixed types
    assert get_origin(value_annotation) is Literal
    assert set(get_args(value_annotation)) == {"allowed", 42, True, None}
    assert value_field.description == "Mixed type enumeration"

    # Test validation with each valid value
    valid_values = ["allowed", 42, True, None]
    for val in valid_values:
        instance = mixed_model(value=val)
        assert instance.value == val

    # Test validation with invalid values
    invalid_values = ["not_allowed", 43, False, ""]
    for val in invalid_values:
        with pytest.raises(ValidationError):
            mixed_model(value=val)


def test_single_value_enum_schema():
    """Test handling of single value enum schema (constants)."""
    # Define schema with single value enum
    single_enum_schema = {
        "type": "object",
        "properties": {"constant": {"enum": ["fixed_value"], "description": "A constant value"}},
        "required": ["constant"],
    }

    # Convert schema to model
    constant_model = json_schema_to_model(single_enum_schema)

    # Check model field annotations
    constant_field = constant_model.model_fields["constant"]
    constant_annotation = constant_field.annotation

    # Verify Literal type usage with single value
    assert get_origin(constant_annotation) is Literal
    assert get_args(constant_annotation) == ("fixed_value",)
    assert constant_field.description == "A constant value"

    # Test validation with valid value
    valid_instance = constant_model(constant="fixed_value")
    assert valid_instance.constant == "fixed_value"

    # Test validation with invalid value
    with pytest.raises(ValidationError):
        constant_model(constant="different_value")


def test_empty_enum_schema():
    """Test handling of empty enum schema (should raise ValueError)."""
    # Define schema with empty enum
    empty_enum_schema = {"type": "object", "properties": {"invalid_field": {"enum": []}}}

    # Attempt to convert schema to model - should raise ValueError
    with pytest.raises(ValueError) as exc_info:
        json_schema_to_model(empty_enum_schema)

    # Verify error message
    assert "empty" in str(exc_info.value).lower()


def test_unicode_enum_schema():
    """Test handling of enum with Unicode characters."""
    # Define schema with Unicode enum values
    unicode_enum_schema = {
        "type": "object",
        "properties": {
            "language": {"enum": ["English", "Español", "Français", "日本語"], "description": "Language selection"}
        },
        "required": ["language"],
    }

    # Convert schema to model
    language_model = json_schema_to_model(unicode_enum_schema)

    # Check model field annotations
    language_field = language_model.model_fields["language"]
    language_annotation = language_field.annotation

    # Verify Literal type with Unicode values
    assert get_origin(language_annotation) is Literal
    assert set(get_args(language_annotation)) == {"English", "Español", "Français", "日本語"}

    # Test validation with valid Unicode values
    for lang in ["English", "Español", "Français", "日本語"]:
        instance = language_model(language=lang)
        assert instance.language == lang

    # Test validation with invalid value
    with pytest.raises(ValidationError):
        language_model(language="German")


def test_array_enum_schema():
    """Test handling of enum inside array items."""
    # Define schema with array of enum values
    array_enum_schema = {
        "type": "object",
        "properties": {"tags": {"type": "array", "items": {"enum": ["low", "medium", "high"]}}},
    }

    # Convert schema to model
    tags_model = json_schema_to_model(array_enum_schema)

    # Check model field annotations
    tags_field = tags_model.model_fields["tags"]
    tags_annotation = tags_field.annotation

    # Handle both list and UnionType (list | None)
    # Extract the non-None type from Union
    args = get_args(tags_annotation)
    list_type = next((arg for arg in args if arg is not type(None)), None)
    # Now check the list and its item type
    list_origin = get_origin(list_type)
    assert list_origin is list, f"Expected list but got {list_origin}"

    # Get the item type
    item_type = get_args(list_type)[0]

    # Check if the item type is a Literal
    item_origin = get_origin(item_type)
    assert item_origin is Literal, f"Expected Literal but got {item_origin}"

    # Check the values in the Literal
    literal_values = set(get_args(item_type))
    assert literal_values == {"low", "medium", "high"}

    # Test validation with valid values
    valid_instance = tags_model(tags=["low", "medium", "high"])
    assert valid_instance.tags == ["low", "medium", "high"]

    # Test validation with some valid values
    valid_instance2 = tags_model(tags=["low", "high"])
    assert valid_instance2.tags == ["low", "high"]

    # Test validation with invalid values
    with pytest.raises(ValidationError):
        tags_model(tags=["low", "critical"])  # "critical" not in enum


def test_nullable_enum_schema():
    """Test handling of nullable enum schema."""
    # Define schema with nullable enum
    nullable_enum_schema = {
        "type": "object",
        "properties": {"priority": {"type": ["null", "string"], "enum": ["low", "medium", "high", None]}},
    }

    # Convert schema to model
    priority_model = json_schema_to_model(nullable_enum_schema)

    # Check model field annotations
    priority_field = priority_model.model_fields["priority"]
    priority_annotation = priority_field.annotation

    # For nullable enums, we should have Literal with None as one option
    assert get_origin(priority_annotation) is Literal
    assert set(get_args(priority_annotation)) == {"low", "medium", "high", None}

    # Test validation with valid values including None
    for val in ["low", "medium", "high", None]:
        instance = priority_model(priority=val)
        assert instance.priority == val

    # Test validation with invalid value
    with pytest.raises(ValidationError):
        priority_model(priority="critical")


def test_large_enum_schema():
    """Test handling of enum with large number of values."""
    # Generate large enum with 100 values
    large_values = [f"value_{i}" for i in range(100)]

    # Define schema with large enum
    large_enum_schema = {"type": "object", "properties": {"selection": {"enum": large_values}}}

    # Convert schema to model
    large_enum_model = json_schema_to_model(large_enum_schema)

    # Check model field annotations
    selection_field = large_enum_model.model_fields["selection"]
    selection_annotation = selection_field.annotation

    union_args = get_args(selection_annotation)
    literal_type = next((arg for arg in union_args if arg is not type(None)), None)

    # Now check if the extracted type is a Literal
    literal_origin = get_origin(literal_type)
    assert literal_origin is Literal, f"Expected Literal but got {literal_origin}"

    # Check the values in the Literal
    enum_values = set(get_args(literal_type))

    # Verify all values are included
    assert enum_values == set(large_values)

    # Test validation with a valid value
    instance = large_enum_model(selection="value_42")
    assert instance.selection == "value_42"

    # Test validation with invalid value
    with pytest.raises(ValidationError):
        large_enum_model(selection="value_not_in_enum")
