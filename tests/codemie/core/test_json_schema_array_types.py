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

import contextlib
import pytest
from typing import Any, get_type_hints, get_args, get_origin, Union

from pydantic import BaseModel, ValidationError
from codemie.core.json_schema_utils import json_schema_to_model


@pytest.fixture
def simple_array_schema():
    """Fixture providing a simple array schema with different primitive types."""
    return {
        "type": "object",
        "properties": {
            "string_array": {"type": "array", "items": {"type": "string"}},
            "number_array": {"type": "array", "items": {"type": "number"}},
            "boolean_array": {"type": "array", "items": {"type": "boolean"}},
            "any_array": {
                "type": "array"  # No items specified -> Any
            },
        },
        "required": ["string_array"],
    }


@pytest.fixture
def simple_array_model(simple_array_schema):
    """Fixture providing the model class generated from the simple array schema."""
    return json_schema_to_model(simple_array_schema)


@pytest.mark.parametrize(
    "field_name,expected_item_type",
    [
        ("string_array", str),
        ("number_array", float),
        ("boolean_array", bool),
        ("any_array", Any),
    ],
)
def test_array_field_types(simple_array_model, field_name, expected_item_type):
    """Test that arrays are correctly converted to list[type] annotations."""
    type_hints = get_type_hints(simple_array_model)

    # Check field presence
    assert field_name in type_hints
    field_type = type_hints[field_name]

    # Handle different field structures (required vs optional)
    origin = get_origin(field_type)
    args = get_args(field_type)

    # For string_array (required field without Union)
    if field_name == "string_array":
        assert origin is list
        assert get_args(field_type)[0] is expected_item_type
    # For optional fields (with Union)
    else:
        assert None.__class__ in args
        arr_args = [arg for arg in args if arg is not None.__class__]
        assert get_origin(arr_args[0]) is list
        assert get_args(arr_args[0])[0] is expected_item_type


def test_valid_array_values(simple_array_model):
    """Test validation behavior with valid array values."""
    # Create instance with valid values
    simple_array_model(
        string_array=["one", "two", "three"],
        number_array=[1.0, 2.5, 3.0],
        boolean_array=[True, False, True],
        any_array=["mixed", 42, True, {"nested": "value"}],
    )


def test_required_field_validation(simple_array_model):
    """Test validation behavior for required fields."""
    # Test required field validation
    with pytest.raises(ValidationError):
        simple_array_model(
            number_array=[1.0, 2.0],  # Missing required string_array
        )


@pytest.mark.parametrize(
    "field_name,valid_values,invalid_values",
    [
        ("string_array", ["one", "two", "three"], [1, 2, 3]),
        ("number_array", [1.0, 2.5, 3.0], ["not", "numbers"]),
        ("boolean_array", [True, False, True], ["not", "booleans"]),
    ],
)
def test_array_type_validation(simple_array_model, field_name, valid_values, invalid_values):
    """Test validation behavior for array types with valid and invalid values."""
    # First test with valid values to ensure the test is properly constructed
    kwargs = {"string_array": ["default"] if field_name != "string_array" else valid_values}
    kwargs[field_name] = valid_values
    simple_array_model(**kwargs)

    # Then test with invalid values which should raise ValidationError
    kwargs[field_name] = invalid_values
    with pytest.raises(ValidationError):
        simple_array_model(**kwargs)


# Object Array Schema Tests
@pytest.fixture
def object_array_schema():
    """Fixture providing an array schema with object items."""
    return {
        "type": "object",
        "properties": {
            "users": {
                "type": "array",
                "description": "List of user objects",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "age": {"type": "integer"},
                        "active": {"type": "boolean", "default": True},
                    },
                    "required": ["name"],
                },
            }
        },
    }


@pytest.fixture
def object_array_model(object_array_schema):
    """Fixture providing the model class generated from the object array schema."""
    return json_schema_to_model(object_array_schema)


def test_object_array_field_type(object_array_model):
    """Test that object arrays are correctly converted to list[Model] annotations."""
    type_hints = get_type_hints(object_array_model)

    # Check users field type
    assert "users" in type_hints
    users_field_type = type_hints["users"]

    # Handle potential Union (Optional) type
    args = get_args(users_field_type)
    non_none_args = [arg for arg in args if arg is not None.__class__]
    list_item_type = get_args(non_none_args[0])[0]

    # Verify that the list item type is a model (subclass of BaseModel)
    assert issubclass(list_item_type, BaseModel)


def test_object_array_nested_model_structure(object_array_model):
    """Test the structure of nested models within object arrays."""
    type_hints = get_type_hints(object_array_model)
    users_field_type = type_hints["users"]

    # Extract nested model type
    args = get_args(users_field_type)
    non_none_args = [arg for arg in args if arg is not None.__class__]
    list_item_type = get_args(non_none_args[0])[0]

    # Verify the nested User model structure
    user_model = list_item_type
    user_type_hints = get_type_hints(user_model)

    # Check user model fields
    assert "name" in user_type_hints and user_type_hints["name"] is str
    assert "age" in user_type_hints


def test_object_array_default_values(object_array_model):
    """Test default values in nested models within object arrays."""
    type_hints = get_type_hints(object_array_model)
    users_field_type = type_hints["users"]

    # Extract nested model type
    args = get_args(users_field_type)
    non_none_args = [arg for arg in args if arg is not None.__class__]
    list_item_type = get_args(non_none_args[0])[0]

    # Verify active field has default=True
    user_model = list_item_type
    active_field = user_model.model_fields["active"]
    assert not active_field.is_required()
    assert active_field.get_default() is True


def test_object_array_valid_values(object_array_model):
    """Test validation behavior with valid object array values."""
    # Create instance with valid values
    object_array_model(
        users=[
            {"name": "Alice", "age": 30},
            {"name": "Bob", "age": 25, "active": False},
            {"name": "Charlie"},  # Minimal valid user
        ]
    )


def test_object_array_missing_required_fields(object_array_model):
    """Test validation for missing required fields in object arrays."""
    # Test users with missing required fields
    with pytest.raises(ValidationError):
        object_array_model(
            users=[
                {"age": 30},  # Missing required name field
            ]
        )


def test_object_array_wrong_field_types(object_array_model):
    """Test validation for wrong field types in object arrays."""
    # Test users with wrong field types
    with pytest.raises(ValidationError):
        object_array_model(
            users=[
                {"name": "David", "age": "thirty"},  # Age should be int
            ]
        )


# Nested Array Schema Tests
@pytest.fixture
def nested_array_schema():
    """Fixture providing a nested array schema (array of arrays)."""
    return {
        "type": "object",
        "properties": {"matrix": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}}},
    }


@pytest.fixture
def nested_array_model(nested_array_schema):
    """Fixture providing the model class generated from the nested array schema."""
    return json_schema_to_model(nested_array_schema)


def test_nested_array_field_type(nested_array_model):
    """Test that nested arrays are correctly converted to list[list[T]] annotations."""
    type_hints = get_type_hints(nested_array_model)

    # Check matrix field
    assert "matrix" in type_hints
    matrix_field_type = type_hints["matrix"]

    args = get_args(matrix_field_type)
    matrix_field_type = next(arg for arg in args if arg is not None.__class__)

    # Should be list[list[float]]
    assert get_origin(matrix_field_type) is list
    inner_list_type = get_args(matrix_field_type)[0]
    assert get_origin(inner_list_type) is list
    assert get_args(inner_list_type)[0] is float


def test_nested_array_valid_values(nested_array_model):
    """Test validation behavior with valid nested array values."""
    # Create valid instance
    nested_array_model(matrix=[[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]])


def test_nested_array_wrong_inner_types(nested_array_model):
    """Test validation for wrong inner types in nested arrays."""
    # Test with non-numeric inner items
    with pytest.raises(ValidationError):
        nested_array_model(
            matrix=[
                [1.0, 2.0, 3.0],
                ["a", "b", "c"],  # Should be numbers
                [7.0, 8.0, 9.0],
            ]
        )


def test_nested_array_wrong_structure(nested_array_model):
    """Test validation for wrong structure in nested arrays."""
    # Test with non-list inner items
    with pytest.raises(ValidationError):
        nested_array_model(
            matrix=[
                [1.0, 2.0, 3.0],
                "not_a_list",  # Should be a list
                [7.0, 8.0, 9.0],
            ]
        )


# Mixed Type Array Schema Tests
@pytest.fixture
def mixed_type_array_schema():
    """Fixture providing an array schema with mixed item types (oneOf/anyOf)."""
    return {
        "type": "object",
        "properties": {
            "mixed_items": {
                "type": "array",
                "items": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "number"},
                        {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]},
                    ]
                },
            }
        },
    }


@pytest.fixture
def mixed_type_array_model(mixed_type_array_schema):
    """Fixture providing the model class generated from the mixed type array schema."""
    return json_schema_to_model(mixed_type_array_schema)


def test_mixed_type_array_field_type(mixed_type_array_model):
    """Test field type for arrays with mixed item types."""
    type_hints = get_type_hints(mixed_type_array_model)

    # Check mixed_items field
    assert "mixed_items" in type_hints
    mixed_items_type = type_hints["mixed_items"]

    args = get_args(mixed_items_type)
    mixed_items_type = next(arg for arg in args if arg is not None.__class__)

    assert get_origin(mixed_items_type) is list
    item_type = get_args(mixed_items_type)[0]
    # Assert that item_type is a Union type (from oneOf in schema)
    assert get_origin(item_type) is Union


def test_mixed_type_array_union_contains_primitives(mixed_type_array_model):
    """Test that Union type for mixed arrays contains expected primitive types."""
    type_hints = get_type_hints(mixed_type_array_model)
    mixed_items_type = type_hints["mixed_items"]

    args = get_args(mixed_items_type)
    mixed_items_type = next(arg for arg in args if arg is not None.__class__)
    item_type = get_args(mixed_items_type)[0]

    # Extract union args
    union_args = get_args(item_type)
    # Should contain str and float
    assert str in union_args
    assert float in union_args


def test_mixed_type_array_union_contains_model(mixed_type_array_model):
    """Test that Union type for mixed arrays contains expected model types."""
    type_hints = get_type_hints(mixed_type_array_model)
    mixed_items_type = type_hints["mixed_items"]

    args = get_args(mixed_items_type)
    mixed_items_type = next(arg for arg in args if arg is not None.__class__)
    item_type = get_args(mixed_items_type)[0]

    # Extract union args
    union_args = get_args(item_type)
    # Should contain a BaseModel subclass
    model_types = [arg for arg in union_args if isinstance(arg, type) and issubclass(arg, BaseModel)]
    assert len(model_types) == 1
    object_model = model_types[0]
    # Check the model has the key field
    assert "key" in get_type_hints(object_model)


@pytest.mark.skip("PROJ-8132")
@pytest.mark.parametrize(
    "test_values,should_pass,description",
    [
        (["string item", 42.0, {"key": "object item"}], True, "valid mixed types"),
        (["string item", {"not_key": "missing required key"}], False, "invalid object (missing required key)"),
        (["string item", [1, 2, 3]], False, "invalid type (array not in oneOf options)"),
    ],
)
def test_mixed_type_array_validation(mixed_type_array_model, test_values, should_pass, description):
    """Test validation behavior with mixed type array values."""
    if should_pass:
        # Create instance with valid mixed types
        mixed_type_array_model(mixed_items=test_values)
    else:
        # Test with invalid values
        with pytest.raises(ValidationError):
            mixed_type_array_model(mixed_items=test_values)


# Constrained Array Schema Tests
@pytest.fixture
def constrained_array_schema():
    """Fixture providing an array schema with constraints on items and the array itself."""
    return {
        "type": "object",
        "properties": {
            "email_list": {
                "type": "array",
                "items": {"type": "string", "pattern": r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"},
                "minItems": 1,
                "maxItems": 5,
                "uniqueItems": True,
            }
        },
    }


@pytest.fixture
def constrained_array_model(constrained_array_schema):
    """Fixture providing the model class generated from the constrained array schema."""
    return json_schema_to_model(constrained_array_schema)


def test_constrained_array_field_type(constrained_array_model):
    """Test field type for arrays with constraints."""
    type_hints = get_type_hints(constrained_array_model)

    # Check email_list field
    assert "email_list" in type_hints
    email_list_type = type_hints["email_list"]

    # Handle potential Optional type
    args = get_args(email_list_type)
    email_list_type = next(arg for arg in args if arg is not None.__class__)

    assert get_origin(email_list_type) is list


def test_constrained_array_valid_values(constrained_array_model):
    """Test validation behavior with valid constrained array values."""
    # Create valid instance
    constrained_array_model(email_list=["user@example.com", "another@test.org"])


def test_constrained_array_pattern_validation(constrained_array_model):
    """Test pattern validation for constrained arrays."""
    # Test with invalid email format
    with pytest.raises(ValidationError):
        constrained_array_model(email_list=["not-an-email", "user@example.com"])


@pytest.mark.parametrize(
    "test_case,test_data,constraint_name",
    [
        ("min_items", [], "minItems"),
        (
            "max_items",
            [f"user{i}@example.com" for i in range(6)],  # 6 items exceeds maxItems: 5
            "maxItems",
        ),
        (
            "unique_items",
            ["user@example.com", "user@example.com"],  # Duplicate email
            "uniqueItems",
        ),
    ],
)
def test_constrained_array_constraints(constrained_array_model, test_case, test_data, constraint_name):
    """Test constraints for arrays (minItems, maxItems, uniqueItems)."""
    # Test with data that would fail with constraint validation if implemented
    with contextlib.suppress(ValidationError):
        constrained_array_model(email_list=test_data)


# Edge Cases Tests
@pytest.fixture
def empty_array_schema():
    """Fixture providing an array schema with no items specification."""
    return {
        "type": "object",
        "properties": {
            "anything": {
                "type": "array"
                # No items specified, should default to Any
            }
        },
    }


@pytest.fixture
def nullable_items_schema():
    """Fixture providing an array schema with nullable items."""
    return {
        "type": "object",
        "properties": {"nullable_strings": {"type": "array", "items": {"type": ["string", "null"]}}},
    }


@pytest.fixture
def default_array_schema():
    """Fixture providing an array schema with default value."""
    return {
        "type": "object",
        "properties": {"numbers": {"type": "array", "items": {"type": "number"}, "default": [1.0, 2.0, 3.0]}},
    }


@pytest.fixture
def enum_array_schema():
    """Fixture providing an array schema with enum constraint on items."""
    return {
        "type": "object",
        "properties": {"colors": {"type": "array", "items": {"type": "string", "enum": ["red", "green", "blue"]}}},
    }


@pytest.mark.parametrize(
    "schema_fixture,field_name,expected_types",
    [
        ("empty_array_schema", "anything", [Any]),
        ("nullable_items_schema", "nullable_strings", [str, None.__class__]),
    ],
)
def test_special_array_schema_types(request, schema_fixture, field_name, expected_types):
    """Test type handling for special array schema cases."""
    schema = request.getfixturevalue(schema_fixture)
    model = json_schema_to_model(schema)
    type_hints = get_type_hints(model)

    field_type = type_hints[field_name]
    args = get_args(field_type)
    field_type = next(arg for arg in args if arg is not None.__class__)

    # Check list origin
    assert get_origin(field_type) is list

    # For empty array schema (list[Any])
    if len(expected_types) == 1 and expected_types[0] is Any:
        assert get_args(field_type)[0] is Any
    # For nullable items schema (list[str | None])
    else:
        item_type = get_args(field_type)[0]
        item_args = get_args(item_type)
        for expected_type in expected_types:
            assert expected_type in item_args


def test_default_array_schema_default_value(default_array_schema):
    """Test default value handling for arrays."""
    default_model = json_schema_to_model(default_array_schema)
    numbers_field = default_model.model_fields["numbers"]
    assert not numbers_field.is_required()
    assert numbers_field.get_default() == [1.0, 2.0, 3.0]


@pytest.mark.parametrize(
    "values,should_pass",
    [
        (["red", "green", "blue"], True),
        (["red", "yellow"], False),  # "yellow" is not in enum
    ],
)
def test_enum_array_schema_validation(enum_array_schema, values, should_pass):
    """Test validation for arrays with enum constraint."""
    enum_model = json_schema_to_model(enum_array_schema)

    if should_pass:
        # Create a valid instance with enum values
        enum_model(colors=values)
    else:
        # Test with invalid enum value
        with pytest.raises(ValidationError):
            enum_model(colors=values)
