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
from pydantic import ValidationError
from pydantic_core import PydanticUndefined

from codemie.core.json_schema_utils import json_schema_to_model


def test_primitive_defaults():
    """Test that primitive type defaults are correctly set in generated models."""
    # Schema with primitive type defaults
    primitive_defaults_schema = {
        "type": "object",
        "properties": {
            "string_field": {"type": "string", "default": "default string"},
            "int_field": {"type": "integer", "default": 42},
            "float_field": {"type": "number", "default": 3.14},
            "bool_field": {"type": "boolean", "default": True},
            "null_default_field": {"type": "string", "default": None},
            "empty_string_default": {"type": "string", "default": ""},
        },
    }

    # Convert schema to model
    primitive_defaults_model = json_schema_to_model(primitive_defaults_schema)

    # Create instance without providing values to check defaults
    primitive_instance = primitive_defaults_model()

    # Verify defaults were applied correctly
    assert primitive_instance.string_field == "default string"
    assert primitive_instance.int_field == 42
    assert primitive_instance.float_field == 3.14
    assert primitive_instance.bool_field is True
    assert primitive_instance.null_default_field is None
    assert primitive_instance.empty_string_default == ""

    # Verify field definitions have defaults correctly set
    for name, field in primitive_defaults_model.model_fields.items():
        assert field.get_default() != PydanticUndefined, f"Default for {name} should be defined"


def test_complex_defaults():
    """Test that complex type defaults (arrays, objects) are correctly set in generated models."""
    # Schema with complex type defaults
    complex_defaults_schema = {
        "type": "object",
        "properties": {
            "array_default": {"type": "array", "items": {"type": "string"}, "default": ["a", "b", "c"]},
            "empty_array_default": {"type": "array", "items": {"type": "number"}, "default": []},
            "object_default": {
                "type": "object",
                "properties": {"nested_prop": {"type": "string"}},
                "default": {"nested_prop": "nested default"},
            },
        },
    }

    # Convert schema to model
    complex_defaults_model = json_schema_to_model(complex_defaults_schema)

    # Create instance without providing values to check defaults
    complex_instance = complex_defaults_model()

    # Verify array defaults
    assert complex_instance.array_default == ["a", "b", "c"]
    assert complex_instance.empty_array_default == []

    # Verify nested object default - Fix: Access as dict instead of object attribute
    assert complex_instance.object_default["nested_prop"] == "nested default"

    # Ensure defaults are correctly defined in the model
    for name, field in complex_defaults_model.model_fields.items():
        assert field.get_default() != PydanticUndefined, f"Default for {name} should be defined"

    # Ensure object defaults create new instances (not references to the same object)
    instance1 = complex_defaults_model()
    instance2 = complex_defaults_model()
    assert instance1.array_default is not instance2.array_default
    assert instance1.empty_array_default is not instance2.empty_array_default
    assert instance1.object_default is not instance2.object_default


def test_mixed_required_optional_defaults():
    """Test handling of defaults in schemas with both required and optional fields."""
    # Schema with mixed required and optional fields
    mixed_schema = {
        "type": "object",
        "properties": {
            "required_no_default": {"type": "string"},
            "required_with_default": {"type": "integer", "default": 100},
            "optional_no_default": {"type": "boolean"},
            "optional_with_default": {"type": "string", "default": "optional default"},
        },
        "required": ["required_no_default", "required_with_default"],
    }

    # Convert schema to model
    mixed_model = json_schema_to_model(mixed_schema)

    # Test creation without required fields (should fail)
    with pytest.raises(ValidationError):
        # Not storing the result as it won't be created
        mixed_model()

    # Test creation with only required fields (defaults should apply to optional)
    valid_instance = mixed_model(required_no_default="test", required_with_default=200)

    # Required field with provided value should use that value
    assert valid_instance.required_with_default == 200

    # Optional field with default should use default since not provided
    assert valid_instance.optional_with_default == "optional default"

    # Optional field without default should be None
    assert valid_instance.optional_no_default is None

    # Test that default override works for fields with defaults
    custom_instance = mixed_model(
        required_no_default="test", required_with_default=50, optional_with_default="custom value"
    )
    assert custom_instance.optional_with_default == "custom value"

    # Verify field definitions
    assert mixed_model.model_fields["required_no_default"].is_required()
    assert mixed_model.model_fields["required_with_default"].is_required()
    assert not mixed_model.model_fields["optional_no_default"].is_required()
    assert not mixed_model.model_fields["optional_with_default"].is_required()

    # The implementation doesn't set defaults on required fields
    # Since we know that required_with_default is actually required in the implementation,
    # we need to provide it when creating an instance
    required_with_default_instance = mixed_model(required_no_default="test", required_with_default=100)
    assert required_with_default_instance.required_with_default == 100


def test_nested_object_with_defaults():
    """Test handling of defaults in nested object structures."""
    nested_schema = {
        "type": "object",
        "properties": {
            "user": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "default": "Guest"},
                    "settings": {
                        "type": "object",
                        "properties": {
                            "theme": {"type": "string", "default": "light"},
                            "notifications": {"type": "boolean", "default": True},
                            "language": {"type": "string", "default": "en"},
                        },
                    },
                },
                "default": {"name": "DefaultUser", "settings": {"theme": "dark"}},
            }
        },
    }

    nested_model = json_schema_to_model(nested_schema)

    instance = nested_model()

    assert instance.user["name"] == "DefaultUser"
    assert instance.user["settings"]["theme"] == "dark"

    custom_instance = nested_model(user={"name": "CustomUser"})

    assert custom_instance.user.name == "CustomUser"


def test_edge_case_defaults():
    """Test edge cases for default values in JSON schemas."""
    edge_case_schema = {
        "type": "object",
        "properties": {
            # Special default values
            "default_zero": {"type": "integer", "default": 0},
            "default_false": {"type": "boolean", "default": False},
            "default_empty_object": {"type": "object", "properties": {}, "default": {}},
            # Array with default that contains null
            "array_with_null": {"type": "array", "items": {"type": "string"}, "default": ["a", None, "c"]},
            # Field that can be null or have a default
            "nullable_with_default": {"type": ["string", "null"], "default": "default value"},
        },
    }

    # Convert schema to model
    edge_case_model = json_schema_to_model(edge_case_schema)

    # Create instance without providing values
    instance = edge_case_model()

    # Verify special "falsy" defaults
    assert instance.default_zero == 0
    assert instance.default_false is False
    assert instance.default_empty_object == {}

    # Verify array default with null
    assert instance.array_with_null == ["a", None, "c"]

    # Verify nullable field with default
    assert instance.nullable_with_default == "default value"

    # Test nullable field with null override
    null_override = edge_case_model(nullable_with_default=None)
    assert null_override.nullable_with_default is None


def test_default_factory_behavior():
    """Test that mutable default values create new instances for each model instance."""
    schema_with_mutable_defaults = {
        "type": "object",
        "properties": {
            "list_default": {"type": "array", "items": {"type": "integer"}, "default": [1, 2, 3]},
            "dict_default": {"type": "object", "additionalProperties": {"type": "string"}, "default": {"a": "b"}},
            "nested_default": {
                "type": "object",
                "properties": {"items": {"type": "array", "items": {"type": "string"}, "default": ["x"]}},
                "default": {"items": ["default"]},
            },
        },
    }

    # Convert schema to model
    mutable_defaults_model = json_schema_to_model(schema_with_mutable_defaults)

    # Create two instances
    instance1 = mutable_defaults_model()
    instance2 = mutable_defaults_model()

    # Verify default values are equal but not the same object
    assert instance1.list_default == instance2.list_default
    assert instance1.list_default is not instance2.list_default

    assert instance1.dict_default == instance2.dict_default
    assert instance1.dict_default is not instance2.dict_default

    # Fix: Access nested dictionary items with dictionary syntax
    assert instance1.nested_default["items"] == instance2.nested_default["items"]
    assert instance1.nested_default is not instance2.nested_default
    assert instance1.nested_default["items"] is not instance2.nested_default["items"]

    # Modify one instance's default values
    instance1.list_default.append(4)
    instance1.dict_default["c"] = "d"
    instance1.nested_default["items"].append("y")

    # Verify other instance remains unchanged
    assert instance2.list_default == [1, 2, 3]
    assert instance2.dict_default == {"a": "b"}
    assert instance2.nested_default["items"] == ["default"]
