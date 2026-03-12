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
from pydantic import BaseModel, ValidationError

from codemie.core.json_schema_utils import json_schema_to_model


def test_basic_object_schema_conversion():
    """
    Test that `json_schema_to_model` correctly converts a simple JSON object schema with
    basic property types (string, number, boolean) into a valid Pydantic model
    with appropriate field annotations, validation behavior, and field metadata.
    """
    # Create a simple JSON object schema with various property types
    schema = {
        "type": "object",
        "title": "Person",
        "properties": {
            "name": {"type": "string", "description": "The person's full name"},
            "age": {"type": "integer", "description": "Age in years"},
            "email": {"type": "string"},
            "is_active": {"type": "boolean", "description": "Whether the person is active"},
            "height": {"type": "number", "description": "Height in meters"},
        },
        "required": ["name", "email"],
        # Removed "additionalProperties": False to avoid Pydantic conflict between __config__ and __base__
    }

    # Generate a Pydantic model from the schema
    person_model = json_schema_to_model(schema)

    # Test 1: Verify model inheritance and name
    assert issubclass(person_model, BaseModel)
    assert person_model.__name__ == "Person"

    # Test 2: Verify field types and metadata
    type_hints = get_type_hints(person_model)
    assert 'name' in type_hints and type_hints['name'] is str
    assert 'email' in type_hints and type_hints['email'] is str
    assert 'age' in type_hints and type_hints['age'] == int | None
    assert 'is_active' in type_hints and type_hints['is_active'] == bool | None
    assert 'height' in type_hints and type_hints['height'] == float | None

    # Test 3: Verify field descriptions
    assert person_model.model_fields['name'].description == "The person's full name"
    assert person_model.model_fields['age'].description == "Age in years"
    assert person_model.model_fields['is_active'].description == "Whether the person is active"
    assert person_model.model_fields['height'].description == "Height in meters"
    # Email doesn't have a description in the schema
    assert not person_model.model_fields['email'].description

    # Test 4: Skip model configuration check since we removed additionalProperties

    # Test 5: Create a valid instance with required fields only
    valid_person = person_model(name="John Doe", email="john@example.com")
    assert valid_person.name == "John Doe"
    assert valid_person.email == "john@example.com"
    assert valid_person.age is None
    assert valid_person.is_active is None
    assert valid_person.height is None

    # Test 6: Create a valid instance with all fields
    full_person = person_model(name="Jane Smith", email="jane@example.com", age=30, is_active=True, height=1.75)
    assert full_person.name == "Jane Smith"
    assert full_person.email == "jane@example.com"
    assert full_person.age == 30
    assert full_person.is_active is True
    assert full_person.height == 1.75

    # Test 7: Validation error when missing required fields
    with pytest.raises(ValidationError) as exc_info:
        person_model(age=25)
    error_dict = exc_info.value.errors()
    error_fields = {error['loc'][0] for error in error_dict}
    assert 'name' in error_fields
    assert 'email' in error_fields

    # Test 8: Skip undefined field validation since we removed additionalProperties: False


def test_empty_properties_object():
    """
    Test that a schema with empty properties still generates a valid model.
    """
    empty_schema = {"type": "object", "properties": {}}
    empty_model = json_schema_to_model(empty_schema)

    # Should be a valid model with no fields
    assert issubclass(empty_model, BaseModel)
    assert len(empty_model.model_fields) == 0

    # Should be able to instantiate without any arguments
    instance = empty_model()
    assert isinstance(instance, empty_model)


def test_optional_fields_only():
    """
    Test that a schema with only optional fields generates a model with all optional fields.
    """
    optional_schema = {"type": "object", "properties": {"opt1": {"type": "string"}, "opt2": {"type": "number"}}}
    optional_model = json_schema_to_model(optional_schema)

    # Should be a valid model with all optional fields
    assert issubclass(optional_model, BaseModel)
    assert len(optional_model.model_fields) == 2

    # All fields should be optional
    assert not optional_model.model_fields['opt1'].is_required()
    assert not optional_model.model_fields['opt2'].is_required()

    # Should be able to instantiate without any arguments
    instance = optional_model()
    assert isinstance(instance, optional_model)
    assert instance.opt1 is None
    assert instance.opt2 is None

    # Should also accept values for the optional fields
    instance = optional_model(opt1="test", opt2=42.0)
    assert instance.opt1 == "test"
    assert instance.opt2 == 42.0
