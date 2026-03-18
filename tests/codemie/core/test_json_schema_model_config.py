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

from codemie.core.json_schema_utils import json_schema_to_model


def test_strict_additional_properties():
    """Test schema with additionalProperties: false rejecting additional properties"""
    # Schema with additionalProperties: false
    strict_schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
        "required": ["name"],
        "additionalProperties": False,
    }

    # Create the model without checking model_config - we test behavior instead
    strict_model = json_schema_to_model(strict_schema)

    # Valid data should pass validation
    valid_data = {"name": "John Doe", "age": 30}
    strict_instance = strict_model(**valid_data)
    assert strict_instance.name == "John Doe"
    assert strict_instance.age == 30

    # Data with additional fields should raise ValidationError
    # This behavior check is more important than implementation details
    data_with_additional_fields = {
        "name": "John Doe",
        "age": 30,
        "email": "john@example.com",
        "address": "123 Main St",
    }

    # Try to create instance with extra fields - should fail
    with pytest.raises(ValidationError) as excinfo:
        strict_model(**data_with_additional_fields)

    # Check that the error mentions the extra fields
    error_str = str(excinfo.value)
    assert any(field in error_str for field in ["email", "address", "extra fields", "extra attributes"])


def test_permissive_additional_properties():
    """Test schema with additionalProperties: true accepting additional properties"""
    # Schema with additionalProperties: true
    permissive_schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
        "required": ["name"],
        "additionalProperties": True,
    }

    # Create the model
    permissive_model = json_schema_to_model(permissive_schema)

    # We don't check model_config directly since implementation may vary

    # Valid data should pass validation
    valid_data = {"name": "Jane Smith", "age": 25}
    permissive_instance = permissive_model(**valid_data)
    assert permissive_instance.name == "Jane Smith"
    assert permissive_instance.age == 25

    # Data with additional fields should not raise ValidationError
    data_with_additional_fields = {
        "name": "Jane Smith",
        "age": 25,
        "email": "jane@example.com",
        "address": "456 Oak St",
    }

    # This should not raise an exception
    permissive_instance = permissive_model(**data_with_additional_fields)
    assert permissive_instance.name == "Jane Smith"
    assert permissive_instance.age == 25


def test_default_additional_properties():
    """Test schema without additionalProperties accepting additional properties"""
    # Schema without additionalProperties
    default_schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
        "required": ["name"],
    }

    # Create the model
    default_model = json_schema_to_model(default_schema)

    # We don't check model_config directly since implementation may vary

    # Valid data should pass validation
    valid_data = {"name": "Alice Johnson", "age": 35}
    default_instance = default_model(**valid_data)
    assert default_instance.name == "Alice Johnson"
    assert default_instance.age == 35

    # Data with additional fields should not raise ValidationError
    data_with_additional_fields = {
        "name": "Alice Johnson",
        "age": 35,
        "email": "alice@example.com",
        "address": "789 Pine St",
    }

    # This should not raise an exception
    default_instance = default_model(**data_with_additional_fields)
    assert default_instance.name == "Alice Johnson"
    assert default_instance.age == 35


def test_nested_additional_properties():
    """Test nested schemas with different additionalProperties settings"""
    # Try the original test approach first, modified to use separate schemas
    parent_schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}

    child_schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
        "required": ["name"],
        "additionalProperties": False,
    }

    parent_model = json_schema_to_model(parent_schema)
    child_model = json_schema_to_model(child_schema)

    # Test the child model with extra fields - should fail
    with pytest.raises(ValidationError) as excinfo:
        child_model(name="Test Child", age=5, extra="should not be allowed")

    error_str = str(excinfo.value)
    assert any(x in error_str for x in ["extra", "extra fields", "extra attributes"])

    # Test creating a valid child and parent
    child = child_model(name="Valid Child", age=10)
    assert child.name == "Valid Child"

    parent = parent_model(name="Valid Parent", extra_field="allowed")
    assert parent.name == "Valid Parent"

    # Success with original approach


def test_schema_with_object_additional_properties():
    """Test additionalProperties with schema object instead of boolean"""
    # Schema with additionalProperties as an object schema
    schema_with_object_additional = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
        "required": ["name"],
        "additionalProperties": {
            "type": "string"  # All additional properties must be strings
        },
    }

    additional_props_model = json_schema_to_model(schema_with_object_additional)

    # Valid data with extra fields.
    # Note: extra="allow" does not enforce the additionalProperties value type,
    # so extra2 (integer) is accepted even though the schema declares "type": "string".
    valid_data = {
        "name": "Bob",
        "age": 40,
        "extra1": "string value",
        "extra2": 123,  # intentionally a non-string to verify no type error is raised
    }

    instance = additional_props_model(**valid_data)
    assert instance.name == "Bob"
    assert instance.age == 40

    # Extra properties must be preserved through model_dump()
    dumped = instance.model_dump()
    assert dumped.get("extra1") == "string value"
    assert dumped.get("extra2") == 123


def test_free_form_map_schema():
    """Free-form map field (additionalProperties as schema dict) must preserve its values.

    Reproduces the facetFilters bug: when the outer object schema has a field whose type is a
    free-form map (type: object + additionalProperties: <schema>), the map's contents must not
    be discarded after Pydantic validation and model_dump() serialisation.
    """
    outer_schema = {
        "type": "object",
        "properties": {
            "facetFilters": {
                "type": ["object", "null"],
                "additionalProperties": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            }
        },
    }

    outer_model = json_schema_to_model(outer_schema)

    # Single entry — the exact bug case
    instance = outer_model(facetFilters={"project.code": ["EPM-TIME"]})
    assert instance.model_dump()["facetFilters"] == {"project.code": ["EPM-TIME"]}

    # Multiple entries
    instance2 = outer_model(facetFilters={"project.code": ["EPM-TIME"], "status": ["active"]})
    assert instance2.model_dump()["facetFilters"] == {"project.code": ["EPM-TIME"], "status": ["active"]}

    # None accepted (nullable field)
    instance3 = outer_model(facetFilters=None)
    assert instance3.facetFilters is None

    # facetFilters must be a plain dict, NOT a Pydantic model instance.
    # LangChain's _parse_input returns getattr(result, field) which, for a plain dict field,
    # gives a dict directly.  A Pydantic sub-model instance inside dict[str, Any] loses its
    # extra fields when serialised via MCPToolInvocationRequest.model_dump().
    instance4 = outer_model(facetFilters={"project.code": ["EPM-TIME"]})
    assert isinstance(
        instance4.facetFilters, dict
    ), "facetFilters must be stored as a plain dict, not a Pydantic model instance"
