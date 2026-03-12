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

from typing import get_type_hints

import pytest
from pydantic import ValidationError

from codemie.core.json_schema_utils import json_schema_to_model


def test_required_and_optional_field_handling():
    """
    Test that json_schema_to_model correctly handles both required and optional fields
    during JSON schema to Pydantic model conversion. This includes:
    1. Proper enforcement of required fields
    2. Correct configuration of optional fields with and without defaults
    3. Handling of required vs optional field status regardless of default values
    """
    # Schema with a mix of required and optional fields
    schema_with_mixed_fields = {
        "type": "object",
        "properties": {
            "required_string": {"type": "string", "description": "A required string field"},
            "required_integer": {"type": "integer", "description": "A required integer field"},
            "optional_number": {"type": "number", "description": "An optional number field"},
            "optional_boolean": {"type": "boolean", "description": "An optional boolean field"},
            "optional_with_default": {
                "type": "string",
                "default": "default value",
                "description": "An optional string with default",
            },
        },
        "required": ["required_string", "required_integer"],
    }

    # Generate Pydantic model from the schema
    mixed_fields_model = json_schema_to_model(schema_with_mixed_fields)

    # Test 1: Verify field properties
    model_fields = mixed_fields_model.model_fields

    # Required fields should not have default values
    assert model_fields["required_string"].is_required()
    assert model_fields["required_integer"].is_required()

    # Optional fields should not be required
    assert not model_fields["optional_number"].is_required()
    assert not model_fields["optional_boolean"].is_required()
    assert not model_fields["optional_with_default"].is_required()

    # Optional field with default should have the default value
    assert model_fields["optional_with_default"].default == "default value"

    # Test 2: Verify type annotations
    type_hints = get_type_hints(mixed_fields_model)
    assert type_hints["required_string"] is str
    assert type_hints["required_integer"] is int
    assert type_hints["optional_number"] == float | None
    assert type_hints["optional_boolean"] == bool | None
    assert type_hints["optional_with_default"] is str

    # Test 3: Valid instantiation with only required fields
    valid_mixed_data = {
        "required_string": "test",
        "required_integer": 42,
    }
    instance = mixed_fields_model(**valid_mixed_data)
    assert instance.required_string == "test"
    assert instance.required_integer == 42
    assert instance.optional_number is None
    assert instance.optional_boolean is None
    assert instance.optional_with_default == "default value"  # Default value should be used

    # Test 4: Invalid data missing required fields
    invalid_mixed_data_missing_required = {
        "required_string": "test",
        # Missing required_integer
        "optional_number": 3.14,
    }
    with pytest.raises(ValidationError) as exc_info:
        mixed_fields_model(**invalid_mixed_data_missing_required)
    # Verify error message mentions missing required field
    error_dict = exc_info.value.errors()
    error_fields = {error["loc"][0] for error in error_dict}
    assert "required_integer" in error_fields

    # Test 5: Valid instantiation with all fields including optionals
    valid_mixed_data_with_optionals = {
        "required_string": "test",
        "required_integer": 42,
        "optional_number": 3.14,
        "optional_boolean": True,
        "optional_with_default": "provided value",  # Override default
    }
    instance_with_optionals = mixed_fields_model(**valid_mixed_data_with_optionals)
    assert instance_with_optionals.required_string == "test"
    assert instance_with_optionals.required_integer == 42
    assert instance_with_optionals.optional_number == 3.14
    assert instance_with_optionals.optional_boolean is True
    assert instance_with_optionals.optional_with_default == "provided value"


def test_required_fields_with_schema_defaults():
    """
    Test that json_schema_to_model preserves required status for fields
    that have default values in the JSON Schema but are listed in 'required'.
    These fields should still be required in the Pydantic model.
    """
    # Schema with default value for a required field
    schema_with_default_in_required = {
        "type": "object",
        "properties": {
            "required_with_default": {
                "type": "string",
                "default": "default for required",
                "description": "Required with default",
            },
            "optional_field": {"type": "string", "description": "Optional field"},
        },
        "required": ["required_with_default"],
    }

    # Generate model from schema
    default_required_model = json_schema_to_model(schema_with_default_in_required)

    # Test 1: Verify required status is maintained despite schema default
    assert default_required_model.model_fields["required_with_default"].is_required()
    assert not default_required_model.model_fields["optional_field"].is_required()

    # Test 2: Successful instantiation with required field provided explicitly
    valid_default_required_data_explicit = {"required_with_default": "explicit value"}
    instance1 = default_required_model(**valid_default_required_data_explicit)
    assert instance1.required_with_default == "explicit value"
    assert instance1.optional_field is None

    # Test 3: Validation error when required field is missing
    # Despite having a default in schema, it should still be required
    valid_default_required_data_missing = {}
    with pytest.raises(ValidationError) as exc_info:
        default_required_model(**valid_default_required_data_missing)
    # Verify error message mentions missing required field
    error_dict = exc_info.value.errors()
    error_fields = {error["loc"][0] for error in error_dict}
    assert "required_with_default" in error_fields


def test_all_optional_fields():
    """
    Test how json_schema_to_model handles schemas where all fields are optional,
    either explicitly (empty 'required' array) or implicitly (no 'required' property).
    """
    # Schema with empty required array (explicitly all optional)
    schema_all_optional = {
        "type": "object",
        "properties": {
            "field1": {"type": "string", "description": "Optional field 1"},
            "field2": {"type": "integer", "description": "Optional field 2"},
            "field3": {"type": "boolean", "default": True, "description": "Optional field 3 with default"},
        },
        "required": [],  # Explicitly empty required array
    }

    # Schema with no required array (implicitly all optional)
    schema_implicit_optional = {
        "type": "object",
        "properties": {
            "field1": {"type": "string", "description": "Implicitly optional field 1"},
            "field2": {"type": "integer", "description": "Implicitly optional field 2"},
        },
        # No 'required' property at all
    }

    # Generate models from both schemas
    all_optional_model = json_schema_to_model(schema_all_optional)
    implicit_optional_model = json_schema_to_model(schema_implicit_optional)

    # Test 1: Verify all fields are optional in both models
    for field_name in all_optional_model.model_fields:
        assert not all_optional_model.model_fields[field_name].is_required()

    for field_name in implicit_optional_model.model_fields:
        assert not implicit_optional_model.model_fields[field_name].is_required()

    # Test 2: Both models can be instantiated with no fields
    instance1 = all_optional_model()
    instance2 = implicit_optional_model()

    assert instance1.field1 is None
    assert instance1.field2 is None
    assert instance1.field3 is True  # Default value should be applied

    assert instance2.field1 is None
    assert instance2.field2 is None

    # Test 3: Test with explicit values for optional fields
    instance3 = all_optional_model(field1="test", field2=42, field3=False)
    assert instance3.field1 == "test"
    assert instance3.field2 == 42
    assert instance3.field3 is False

    instance4 = implicit_optional_model(field1="test", field2=42)
    assert instance4.field1 == "test"
    assert instance4.field2 == 42


def test_edge_cases_required_optional():
    """
    Test edge cases for required vs optional field handling:
    1. Empty schema (no properties)
    2. All fields required
    3. Nullable fields
    4. Empty string and zero defaults
    """
    # Edge case 1: Empty schema (no properties)
    empty_schema = {"type": "object", "properties": {}}
    empty_model = json_schema_to_model(empty_schema)
    assert len(empty_model.model_fields) == 0
    empty_model()  # Should instantiate without error

    # Edge case 2: All fields required
    all_required_schema = {
        "type": "object",
        "properties": {"field1": {"type": "string"}, "field2": {"type": "integer"}},
        "required": ["field1", "field2"],
    }
    all_required_model = json_schema_to_model(all_required_schema)
    assert all_required_model.model_fields["field1"].is_required()
    assert all_required_model.model_fields["field2"].is_required()

    with pytest.raises(ValidationError):
        all_required_model(field1="test")  # Missing field2

    # Edge case 3: Nullable fields (type: ["string", "null"])
    nullable_schema = {
        "type": "object",
        "properties": {
            "required_nullable": {"type": ["string", "null"]},
            "optional_nullable": {"type": ["integer", "null"]},
        },
        "required": ["required_nullable"],
    }
    nullable_model = json_schema_to_model(nullable_schema)

    # required_nullable is required but can be None
    assert nullable_model.model_fields["required_nullable"].is_required()
    instance1 = nullable_model(required_nullable=None)
    assert instance1.required_nullable is None

    # But it can't be omitted
    with pytest.raises(ValidationError):
        nullable_model()

    # Edge case 4: Empty string and zero defaults
    special_defaults_schema = {
        "type": "object",
        "properties": {
            "empty_string_default": {"type": "string", "default": ""},
            "zero_default": {"type": "integer", "default": 0},
            "false_default": {"type": "boolean", "default": False},
        },
    }
    special_defaults_model = json_schema_to_model(special_defaults_schema)
    instance = special_defaults_model()

    # Empty string, zero, and false should be preserved as defaults, not treated as None/missing
    assert instance.empty_string_default == ""
    assert instance.zero_default == 0
    assert instance.false_default is False
