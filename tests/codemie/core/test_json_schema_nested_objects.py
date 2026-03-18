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
from pydantic import BaseModel, ValidationError
from typing import get_args, get_origin

from codemie.core.json_schema_utils import json_schema_to_model


def test_basic_nested_schema_conversion():
    """Test conversion of a basic two-level nested object schema to Pydantic models."""
    # Basic nested schema with two levels
    basic_nested_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"},
            "address": {
                "type": "object",
                "properties": {"street": {"type": "string"}, "city": {"type": "string"}, "zipcode": {"type": "string"}},
                "required": ["street", "city"],
            },
        },
        "required": ["name", "address"],
    }

    # Convert schema to a Pydantic model
    basic_nested_model = json_schema_to_model(basic_nested_schema)

    # Verify the model structure and field types
    assert issubclass(basic_nested_model, BaseModel)

    # Check that top-level fields have the expected types
    field_annotations = basic_nested_model.model_fields
    assert "name" in field_annotations
    assert "age" in field_annotations
    assert "address" in field_annotations

    assert field_annotations["name"].annotation is str
    assert field_annotations["age"].annotation == int | None  # Optional

    # Verify address field is a nested Pydantic model
    address_field = field_annotations["address"]
    address_model = address_field.annotation

    assert issubclass(address_model, BaseModel)

    # Check fields in the nested address model
    address_fields = address_model.model_fields
    assert "street" in address_fields
    assert "city" in address_fields
    assert "zipcode" in address_fields

    # Check types in nested model
    assert address_fields["street"].annotation is str
    assert address_fields["city"].annotation is str
    assert address_fields["zipcode"].annotation == str | None  # Optional

    # Test validation of a valid instance
    valid_instance = basic_nested_model(name="John Doe", address={"street": "123 Main St", "city": "Anytown"})
    assert valid_instance.name == "John Doe"
    assert valid_instance.address.street == "123 Main St"
    assert valid_instance.address.city == "Anytown"
    assert valid_instance.address.zipcode is None  # Default None for optional

    # Test validation error for missing required field in nested object
    with pytest.raises(ValidationError) as excinfo:
        basic_nested_model(
            name="John Doe",
            address={
                # Missing required 'street' field
                "city": "Anytown"
            },
        )
    assert "street" in str(excinfo.value)  # Error mentions missing field

    # Test validation error for missing top-level required field
    with pytest.raises(ValidationError) as excinfo:
        basic_nested_model(
            # Missing required 'name' field
            address={"street": "123 Main St", "city": "Anytown"}
        )
    assert "name" in str(excinfo.value)  # Error mentions missing field


def test_deep_nested_schema_conversion():
    """Test conversion of a deeply nested schema with multiple levels of objects."""
    # Deep nested schema with multiple levels
    deep_nested_schema = {
        "type": "object",
        "properties": {
            "user": {
                "type": "object",
                "properties": {
                    "profile": {
                        "type": "object",
                        "properties": {
                            "personal": {
                                "type": "object",
                                "properties": {
                                    "firstName": {"type": "string"},
                                    "lastName": {"type": "string"},
                                    "age": {"type": "integer"},
                                },
                                "required": ["firstName", "lastName"],
                            },
                            "preferences": {
                                "type": "object",
                                "properties": {
                                    "theme": {"type": "string", "default": "light"},
                                    "notifications": {"type": "boolean", "default": True},
                                },
                            },
                        },
                        "required": ["personal"],
                    }
                },
                "required": ["profile"],
            }
        },
        "required": ["user"],
    }

    # Convert schema to a Pydantic model
    deep_nested_model = json_schema_to_model(deep_nested_schema)

    # Verify the model structure
    assert issubclass(deep_nested_model, BaseModel)

    # Navigate through the nested structure and verify each level
    user_field = deep_nested_model.model_fields["user"]
    user_model = user_field.annotation

    assert issubclass(user_model, BaseModel)

    profile_field = user_model.model_fields["profile"]
    profile_model = profile_field.annotation

    assert issubclass(profile_model, BaseModel)

    personal_field = profile_model.model_fields["personal"]
    preferences_field = profile_model.model_fields["preferences"]

    # Extract the personal model (handling potential Union)
    personal_model = personal_field.annotation

    assert issubclass(personal_model, BaseModel)

    # Check if preferences_field.annotation is a Union type (optional model)
    preferences_type = preferences_field.annotation
    non_none_types = [arg for arg in get_args(preferences_type) if arg is not type(None)]
    assert len(non_none_types) == 1
    assert issubclass(non_none_types[0], BaseModel)

    # Check the personal info model
    assert "firstName" in personal_model.model_fields
    assert "lastName" in personal_model.model_fields
    assert "age" in personal_model.model_fields

    # Check the preferences model and its default values
    # Extract the actual model type from potential Union
    preferences_model = [arg for arg in get_args(preferences_field.annotation) if arg is not type(None)][0]

    theme_field = preferences_model.model_fields["theme"]
    notifications_field = preferences_model.model_fields["notifications"]

    assert theme_field.get_default() == "light"
    assert notifications_field.get_default() is True

    # Test validation of a valid deep nested instance
    valid_instance = deep_nested_model(
        user={
            "profile": {
                "personal": {"firstName": "John", "lastName": "Doe"},
                # Add preferences explicitly with defaults
                "preferences": {"theme": "light", "notifications": True},
            }
        }
    )

    # Verify the nested values and defaults
    assert valid_instance.user.profile.personal.firstName == "John"
    assert valid_instance.user.profile.personal.lastName == "Doe"
    assert valid_instance.user.profile.personal.age is None  # Default for optional field

    # Now the preferences will exist
    assert valid_instance.user.profile.preferences.theme == "light"
    assert valid_instance.user.profile.preferences.notifications is True

    # Test validation error for missing required fields at deep nesting levels
    with pytest.raises(ValidationError) as excinfo:
        deep_nested_model(
            user={
                "profile": {
                    "personal": {
                        # Missing required "lastName" field
                        "firstName": "John"
                    }
                }
            }
        )
    assert "lastName" in str(excinfo.value)


def test_nested_array_schema_conversion():
    """Test conversion of nested arrays of objects in JSON schema."""
    # Schema with arrays of nested objects
    nested_array_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "departments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "employees": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "integer"},
                                    "name": {"type": "string"},
                                    "position": {"type": "string"},
                                },
                                "required": ["id", "name"],
                            },
                        },
                    },
                    "required": ["name"],
                },
            },
        },
        "required": ["name"],
    }

    # Convert schema to a Pydantic model
    nested_array_model = json_schema_to_model(nested_array_schema)

    # Verify the model structure
    assert issubclass(nested_array_model, BaseModel)

    # Check the departments field is a list
    departments_field = nested_array_model.model_fields["departments"]
    departments_type = departments_field.annotation

    # Handle optional list type (Union[list[Model], None])
    non_none_types = [arg for arg in get_args(departments_type) if arg is not type(None)]
    assert len(non_none_types) == 1
    departments_type = non_none_types[0]  # Get the actual list type

    # Check if it's a generic list
    assert get_origin(departments_type) is list

    # Get the department item type, which should be a model
    department_model = get_args(departments_type)[0]
    assert issubclass(department_model, BaseModel)

    # Check the employees field in department model, which should also be a list
    employees_field = department_model.model_fields["employees"]
    employees_type = employees_field.annotation

    # Handle optional list type (Union[list[Model], None])
    non_none_types = [arg for arg in get_args(employees_type) if arg is not type(None)]
    assert len(non_none_types) == 1
    employees_type = non_none_types[0]  # Get the actual list type

    assert get_origin(employees_type) is list

    # Get the employee item type, which should be a model
    employee_model = get_args(employees_type)[0]
    assert issubclass(employee_model, BaseModel)

    # Check that employee model has the expected fields and requirements
    assert "id" in employee_model.model_fields
    assert "name" in employee_model.model_fields
    assert "position" in employee_model.model_fields

    # Test validation of a valid nested array instance
    valid_instance = nested_array_model(
        name="Acme Corp",
        departments=[
            {
                "name": "Engineering",
                "employees": [
                    {"id": 1, "name": "John", "position": "Developer"},
                    {"id": 2, "name": "Jane", "position": "Manager"},
                ],
            },
            {"name": "Sales", "employees": [{"id": 3, "name": "Bob", "position": "Sales Rep"}]},
        ],
    )

    # Verify the nested array data
    assert valid_instance.name == "Acme Corp"
    assert len(valid_instance.departments) == 2
    assert valid_instance.departments[0].name == "Engineering"
    assert len(valid_instance.departments[0].employees) == 2
    assert valid_instance.departments[0].employees[0].id == 1
    assert valid_instance.departments[0].employees[0].name == "John"

    # Test validation error for invalid data in nested array
    with pytest.raises(ValidationError) as excinfo:
        nested_array_model(
            name="Acme Corp",
            departments=[
                {
                    "name": "Engineering",
                    "employees": [
                        # Missing required 'id' field
                        {"name": "John", "position": "Developer"}
                    ],
                }
            ],
        )
    assert "id" in str(excinfo.value)


def test_edge_cases_for_nested_schemas():
    """Test edge cases for nested schema conversion."""
    # Edge case 1: Empty nested objects
    empty_nested_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "metadata": {
                "type": "object",
                "properties": {},  # Empty properties
            },
        },
    }

    # Convert schema to a Pydantic model
    empty_nested_model = json_schema_to_model(empty_nested_schema)

    # Verify the model structure
    assert issubclass(empty_nested_model, BaseModel)
    metadata_field = empty_nested_model.model_fields["metadata"]
    metadata_type = metadata_field.annotation

    # Handle optional type (Union[T, None])
    non_none_types = [arg for arg in get_args(metadata_type) if arg is not type(None)]
    assert len(non_none_types) == 1
    metadata_inner = non_none_types[0]

    # An object with empty properties and no additionalProperties is a free-form map.
    # JSON Schema spec: absent additionalProperties means any extra props are allowed.
    assert (
        get_origin(metadata_inner) is dict
    ), f"Expected dict for bare object schema with empty properties, got {metadata_inner}"

    # Arbitrary keys must round-trip through model_dump()
    instance = empty_nested_model(name="Test", metadata={"foo": 1, "bar": "baz"})
    assert instance.model_dump()["metadata"] == {"foo": 1, "bar": "baz"}

    # Edge case 2: Null values for optional nested objects
    nullable_nested_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "address": {"type": "object", "properties": {"city": {"type": "string"}}},
        },
    }

    # Convert schema to a Pydantic model
    nullable_nested_model = json_schema_to_model(nullable_nested_schema)

    # Create instance with null nested object
    instance = nullable_nested_model(name="Test", address=None)
    assert instance.name == "Test"
    assert instance.address is None

    # Edge case 3: Optional nested object with defaults
    defaults_nested_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "settings": {
                "type": "object",
                "properties": {
                    "color": {"type": "string", "default": "blue"},
                    "active": {"type": "boolean", "default": True},
                },
            },
        },
    }

    # Convert schema to a Pydantic model
    defaults_nested_model = json_schema_to_model(defaults_nested_schema)

    # Create instance with defaults - explicitly provide settings
    instance = defaults_nested_model(name="Test", settings={"color": "blue", "active": True})
    assert instance.name == "Test"
    assert instance.settings.color == "blue"
    assert instance.settings.active is True
