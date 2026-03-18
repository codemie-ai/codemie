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

import inspect
import textwrap
from collections.abc import Mapping
from types import UnionType
from typing import Annotated, Any, Literal, TypeAlias, Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, create_model
from pydantic.fields import FieldInfo
from pydantic.main import ModelT

# Use PydanticUndefined for explicit check if default is set
from pydantic_core import PydanticUndefined

from codemie.configs import logger

# Type Aliases for clarity
JsonSchema: TypeAlias = dict[str, Any]
FieldName: TypeAlias = str
TypeName: TypeAlias = str
TypeAnnotation: TypeAlias = Any
FieldDefinition: TypeAlias = tuple[TypeAnnotation, FieldInfo]


class Cache:
    def __init__(self) -> None:
        self.ref_path: list[str] = ["#"]
        self._cache = dict[str, type[BaseModel]]()
        self._processing_stack: set[str] = set()  # Track models currently being created

    def get(self, key: str) -> type[BaseModel] | None:
        return self._cache.get(key)

    def set_path(self, path: str):
        self.ref_path.append(path)

    def unset_path(self, value: str):
        for i in range(len(self.ref_path) - 1, -1, -1):
            if self.ref_path[i] == value:
                # Keep everything up to (but not including) this element
                self.ref_path = self.ref_path[:i]
                return

    def save_model(self, model: type[BaseModel]):
        self._cache["/".join(self.ref_path)] = model

    def get_model_by_path(self, path: str):
        return self._cache.get(path)

    @property
    def processing_stack(self):
        return self._processing_stack


type ModelCache = Cache

# ===========================================================================
# Public API
# ===========================================================================


def json_schema_to_model(schema: JsonSchema) -> type[ModelT]:
    """
    Generate a Pydantic v2 model dynamically from a JSON Schema dictionary.

    Supports a subset of JSON Schema draft-07/2020-12 keywords including:
    type, properties, required, additionalProperties, items, enum,
    oneOf, anyOf, allOf, pattern, default, description, examples.

    Args:
        schema: A dictionary representing the JSON Schema.

    Returns:
        A dynamically created Pydantic BaseModel subclass reflecting the schema.

    Raises:
        TypeError: If the input schema is not a mapping or if the top-level
                   schema does not represent an object.
        NotImplementedError: If unsupported JSON Schema keywords are encountered.
        ValueError: If schema constructs are invalid (e.g., empty enum).
    """
    if not isinstance(schema, Mapping):
        raise TypeError("Input 'schema' must be a dictionary-like mapping.")

    if not _is_object_schema(schema):
        raise TypeError(
            "Top-level schema must represent an object (e.g., have 'type': 'object', 'properties', or 'allOf')."
        )

    cache: ModelCache = Cache()
    model_name = _normalise_name(schema.get("title", "GeneratedModel"))

    return _create_model_from_schema(model_name, schema, cache)


class SkipPropertyException(Exception): ...


# ===========================================================================
# Core Model Creation Logic (_create_model_from_schema)
# ===========================================================================


def _create_model_from_schema(model_name: TypeName, schema: JsonSchema, cache: ModelCache) -> type[BaseModel]:
    """
    Creates a Pydantic BaseModel subclass from an 'object' JSON schema.

    Handles caching, properties, required fields, 'allOf' inheritance,
    and 'additionalProperties' configuration.
    """

    # 1. Determine Base Model (handles 'allOf')
    base_model = _determine_base_model(model_name, schema.get("allOf"), cache)

    _process_definitions(schema, cache)

    # 2. Process 'properties' into Pydantic field definitions
    field_definitions = _process_properties(schema, cache)

    # 3. Determine Model Configuration (e.g., 'additionalProperties')
    model_config = _configure_model_extras(schema)

    # 4. Create the Pydantic Model using create_model
    #    Fields from 'properties' override any identically named fields from 'allOf' base.
    model = create_model(
        model_name,
        __base__=base_model,
        __config__=model_config,
        **field_definitions,
    )

    return model


def _handle_object(model_name: TypeName, schema: JsonSchema, cache: ModelCache) -> type[BaseModel]:
    """
    Creates a Pydantic BaseModel subclass from an 'object' JSON schema.

    Handles caching, properties, required fields, 'allOf' inheritance,
    and 'additionalProperties' configuration.
    """

    # 1. Determine Base Model (handles 'allOf')
    base_model = _determine_base_model(model_name, schema.get("allOf"), cache)

    _process_definitions(schema, cache)

    # Mark this model as being processed (for recursive reference detection)
    # Use current ref_path directly, as this is what $ref will reference
    current_path = "/".join(cache.ref_path)
    cache.processing_stack.add(current_path)

    # 2. Process 'properties' into Pydantic field definitions (may encounter recursive $ref)
    field_definitions = _process_properties(schema, cache)

    # 3. Determine Model Configuration (e.g., 'additionalProperties')
    model_config = _configure_model_extras(schema)

    # 4. Create the Pydantic Model using create_model
    #    Fields from 'properties' override any identically named fields from 'allOf' base.
    model = create_model(
        model_name.capitalize(),
        __base__=base_model,
        __config__=model_config,
        **field_definitions,
    )
    cache.set_path(model_name)
    cache.save_model(model)
    cache.unset_path(model_name)

    # Remove from processing stack and rebuild to resolve forward references
    cache.processing_stack.discard(current_path)
    if field_definitions:  # Only rebuild if there are fields (indicates possible forward refs)
        model.model_rebuild()

    return model


# ---------------------------------------------------------------------------
# Helper functions for _create_model_from_schema
# ---------------------------------------------------------------------------


def _process_definitions(schema: JsonSchema, cache: ModelCache):
    """Definitions or $defs must contain all declarations for ref objects."""
    if defs := schema.get("definitions"):
        definitions: Mapping[str, JsonSchema] | None = defs
        path = "definitions"
    elif defs := schema.get("$defs"):
        definitions: Mapping[str, JsonSchema] | None = defs
        path = "$defs"
    else:
        return

    if not definitions:
        return

    cache.set_path(path)
    is_required = True

    for def_name, def_schema in definitions.items():
        cache.set_path(def_name)  # Include definition name in path
        definition = _create_field_definition(def_name, def_schema, is_required, cache)
        cache.save_model(definition[0])
        cache.unset_path(def_name)  # Remove definition name from path

    cache.unset_path(path)


_PRIMITIVE_MAPPING: dict[str, type[Any]] = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
    "null": type(None),
}


def _is_object_schema(schema: JsonSchema) -> bool:
    """Check if a schema fragment represents a JSON object."""
    return schema.get("type") == "object" or "properties" in schema or "allOf" in schema


def _is_pure_map_schema(schema: JsonSchema) -> bool:
    """True when the schema is a free-form map (no declared properties, additionalProperties not false).

    Covers three cases:
      - additionalProperties: <schema dict>  → typed map, e.g. dict[str, list[str]]
      - additionalProperties: true           → untyped map, dict[str, Any]
      - additionalProperties absent          → JSON Schema default (any extra props allowed), dict[str, Any]

    additionalProperties: false is the only case that is NOT a free-form map — that schema
    has a finite, declared set of properties and must remain a Pydantic model.

    Such schemas map naturally to dict[str, T] rather than a Pydantic model.  Using a plain
    dict avoids the need for extra='allow' and ensures dynamic keys are preserved through
    all serialisation layers (including dict[str, Any] fields in outer models).
    """
    return (
        schema.get("additionalProperties") is not False  # absent/True/<schema> → free-form
        and not schema.get("properties")  # absent or empty {}
        and "allOf" not in schema
        and "oneOf" not in schema
        and "anyOf" not in schema
    )


def _handle_pure_map_schema(name: TypeName, schema: JsonSchema, cache: ModelCache) -> TypeAnnotation:
    """Return dict[str, T] for a free-form map schema."""
    value_schema = schema.get("additionalProperties")
    if not value_schema or not isinstance(value_schema, dict):
        return dict[str, Any]
    try:
        value_type = _schema_to_type_annotation(f"{name}Value", name, value_schema, cache)
    except (TypeError, NotImplementedError, SkipPropertyException):
        value_type = Any
    return dict[str, value_type]


def _determine_base_model(
    model_name: TypeName, allof_schemas: list[JsonSchema] | None, cache: ModelCache
) -> type[BaseModel] | tuple[type[BaseModel], ...] | None:
    """
    Determines the base class(es) for the model being created.
    If 'allOf' is present, it generates a combined base model.
    Otherwise, defaults to pydantic.BaseModel.
    """
    if not allof_schemas:
        return None
    # Generate a base model by combining all schemas in 'allOf'
    return _handle_allof_inheritance(f"{model_name}AllOfBase", allof_schemas, cache)


def _process_properties(schema: JsonSchema, cache: ModelCache) -> dict[FieldName, FieldDefinition]:
    """
    Processes the 'properties' part of a schema into Pydantic field definitions.
    """
    properties: Mapping[str, JsonSchema] = schema.get("properties", {})

    cache.set_path("properties")

    required_set: set[FieldName] = set(schema.get("required", []))
    field_definitions: dict[FieldName, FieldDefinition] = {}

    for prop_name, prop_schema in properties.items():
        try:
            cache.set_path(prop_name)

            is_required = prop_name in required_set
            definition = _create_field_definition(prop_name, prop_schema, is_required, cache)
            field_definitions[prop_name] = definition

            cache.save_model(definition[0])
            cache.unset_path(prop_name)
        except SkipPropertyException as e:
            logger.debug(f"Skipping '{prop_name}' tool property... Reason: {str(e)}")
            cache.unset_path(prop_name)

    cache.unset_path("properties")

    return field_definitions


def _create_field_definition(
    prop_name: FieldName, prop_schema: JsonSchema, is_required: bool, cache: ModelCache
) -> FieldDefinition:
    """
    Creates a tuple (TypeAnnotation, FieldInfo) for a single property.
    Handles required status, default values, and nullability.
    """
    # 1. Determine the base Python type annotation from the schema

    prop_model_name = _normalise_name(prop_name)
    base_annotation = _schema_to_type_annotation(prop_model_name, prop_name, prop_schema, cache)
    final_annotation = base_annotation  # Start with base, may become Optional

    # 2. Determine the Pydantic FieldInfo (handling required/optional/default)
    field_info: FieldInfo

    if is_required:
        # Required field: Use Field(...)
        # Type annotation remains as derived (already handles schema nullability)
        field_info = Field()  # Ellipsis (...) is the default marker for required
    else:
        # Optional field: Check for explicit "default" in schema
        schema_default = prop_schema.get("default", PydanticUndefined)

        if schema_default is not PydanticUndefined:
            # Optional with an explicit default value
            field_info = Field(default=schema_default)
            # If explicit default is None, ensure type hint includes None
            if schema_default is None:
                final_annotation = _make_type_nullable(base_annotation)
        else:
            # Optional with no explicit default -> Pydantic default is None
            field_info = Field(default=None)
            # Make the Python type hint explicitly Optional (T | None)
            final_annotation = _make_type_nullable(base_annotation)

    # 3. Transfer metadata from schema to FieldInfo
    if description := prop_schema.get("description"):
        field_info.description = description
    if examples := prop_schema.get("examples"):
        field_info.examples = examples
    # Pydantic v2 uses 'json_schema_extra' for arbitrary schema keys
    # field_info.json_schema_extra = {k: v for k, v in prop_schema.items() if k not in ...} # Optional

    return final_annotation, field_info


def _configure_model_extras(schema: JsonSchema) -> ConfigDict | None:
    """
    Creates a Pydantic ConfigDict based on schema keywords like 'additionalProperties'.

    - additionalProperties: false  → extra='forbid'
    - additionalProperties: <dict> → extra='allow'  (free-form / typed map)
    - otherwise                    → no config (Pydantic default)
    """
    additional_props = schema.get("additionalProperties")
    if additional_props is False:
        return ConfigDict(extra="forbid")
    if isinstance(additional_props, dict):
        return ConfigDict(extra="allow")
    return None


# ===========================================================================
# 'allOf' Handling (_handle_allof_inheritance)
# ===========================================================================


def _handle_allof_inheritance(name: TypeName, sub_schemas: list[JsonSchema], cache: ModelCache) -> type[BaseModel]:
    """
    Handles the 'allOf' JSON Schema keyword by creating a Pydantic model.

    Merges sub-schemas that resolve to Pydantic models via multiple inheritance.
    Converts sub-schemas that resolve to primitive/container types into *required fields*
    on the resulting combined model.
    """
    if not sub_schemas:
        raise ValueError("'allOf' must contain at least one sub-schema.")

    model_bases: list[type[BaseModel]] = []
    primitive_fields: dict[FieldName, FieldDefinition] = {}
    generated_field_names: set[FieldName] = set()

    for idx, subschema in enumerate(sub_schemas):
        # Determine the type resulting from this sub-schema
        part_name = f"{name}Part{idx}"
        part_type = _schema_to_type_annotation(part_name, name, subschema, cache)

        # Classify: Is it a Pydantic model or something else?
        is_model = isinstance(part_type, type) and issubclass(part_type, BaseModel)

        if is_model:
            model_bases.append(part_type)
            # Track field names from model bases to avoid clashes with generated primitive fields
            generated_field_names.update(part_type.model_fields.keys())
        else:
            # It's a primitive or container - generate a required field for it
            field_name, field_def = _generate_allof_primitive_field(
                part_type, subschema, part_name, generated_field_names
            )
            primitive_fields[field_name] = field_def
            generated_field_names.add(field_name)

    if not model_bases and not primitive_fields:
        # This case should ideally be caught by the initial check, but defensive coding
        raise ValueError("Processed 'allOf' resulted in no bases or fields.")

    # Create the final model by combining bases and adding primitive fields
    return _create_allof_composed_model(name, model_bases, primitive_fields)


# ---------------------------------------------------------------------------
# Helper functions for _handle_allof_inheritance
# ---------------------------------------------------------------------------


def _generate_allof_primitive_field(
    part_type: TypeAnnotation,
    subschema: JsonSchema,
    _part_name: TypeName,
    existing_field_names: set[FieldName],
) -> tuple[FieldName, FieldDefinition]:
    """Generates a field name and definition for a non-model branch within 'allOf'."""

    # Generate a unique field name based on the type
    base_name = _get_base_name_for_type(part_type)
    field_name = _make_unique_field_name(base_name, existing_field_names)

    # Create FieldInfo - always required for allOf primitives
    field_info = Field()  # Required
    if description := subschema.get("description"):
        field_info.description = description
    if examples := subschema.get("examples"):
        field_info.examples = examples

    return field_name, (part_type, field_info)


def _get_base_name_for_type(part_type: TypeAnnotation) -> str:
    """Suggests a base name for fields generated from primitive types in allOf."""
    base_name_map: dict[Any, str] = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        type(None): "nullValue",  # Avoid 'null' as it's often skipped
        list: "array",
        dict: "objectMap",  # More descriptive than 'object'
    }
    origin = get_origin(part_type)
    lookup_type = origin if origin else part_type  # Use origin (e.g., list) or the type itself

    return base_name_map.get(lookup_type, getattr(part_type, "__name__", "value").lower())


def _make_unique_field_name(base_name: str, existing_names: set[FieldName]) -> FieldName:
    """Ensures the generated field name is unique."""
    if base_name not in existing_names:
        return base_name
    i = 2
    while f"{base_name}_{i}" in existing_names:
        i += 1
    return f"{base_name}_{i}"


def _create_allof_composed_model(
    name: TypeName, model_bases: list[type[BaseModel]], primitive_fields: dict[FieldName, FieldDefinition]
) -> type[BaseModel]:
    """Creates the final model by combining bases and primitive fields from allOf."""

    # Deduplicate base models while preserving order for MRO
    unique_bases: tuple[type[BaseModel], ...] = tuple(dict.fromkeys(model_bases))

    # If no models were found, the base is just BaseModel
    actual_bases = unique_bases if unique_bases else (BaseModel,)

    # Create an intermediate class inheriting from all model bases
    # This resolves MRO before adding primitive fields.
    mi_base = type(f"{name}MiBase", actual_bases, {})

    # Create the final model, inheriting from the combined base and adding primitive fields.
    # `create_model` handles merging fields correctly.
    composed_model = create_model(name, __base__=mi_base, **primitive_fields)
    return composed_model


# ===========================================================================
# Schema Fragment to Type Annotation (_schema_to_type_annotation)
# ===========================================================================


def _get_allof_oneof_anyof_dispatch(name: TypeName, core_schema: JsonSchema, cache: Cache):
    handler_dispatch = {
        "allOf": lambda: _handle_allof_inheritance(name, core_schema["allOf"], cache),
        "oneOf": lambda: _handle_oneof_anyof_schema(name, core_schema, cache),
        "anyOf": lambda: _handle_oneof_anyof_schema(name, core_schema, cache),
    }

    dispatch = None
    for keyword in handler_dispatch:
        if keyword in core_schema:
            return handler_dispatch[keyword]

    return dispatch


def _handlers_after_primitive_types(name: TypeName, core_schema: JsonSchema, cache: Cache):
    if core_schema.get("$ref"):
        ref_type: str = core_schema.get("$ref", "")

        # Check if model is already cached
        if model := cache.get_model_by_path(ref_type):
            return model

        # Check if this ref is currently being processed (recursive reference)
        if ref_type in cache.processing_stack:
            # Use forward reference for recursive types
            # Extract model name from ref path (e.g., "#/$defs/condition" -> "Condition")
            model_name = _extract_model_name_from_ref(ref_type)
            return model_name  # Return string for ForwardRef

        raise TypeError(f"Cannot find Pydantic type $ref schema fragment (name='{name}'): {core_schema}")

    elif "not" in core_schema:
        not_prop = core_schema.get("not")
        if not not_prop:
            raise SkipPropertyException(f"empty not keyword for '{name}': {core_schema}")

        raise TypeError(f"Unsupported `not` feature in schema for '{name}': {core_schema}")
    return None


def _schema_to_type_annotation(
    name: TypeName, base_prop_name: TypeName, schema: JsonSchema, cache: ModelCache
) -> TypeAnnotation:
    """
    Recursively converts a JSON Schema fragment into a Python type annotation.

    This is the central dispatcher function calling specific handlers based on
    schema keywords.
    """
    # Check for explicit null type first
    if schema.get("type") == "null":
        return type(None)

    # Detect potential nullability from "type": ["<type>", "null"]
    schema_type_keyword = schema.get("type")
    is_nullable, core_schema = _extract_nullability(schema, schema_type_keyword)

    # --- Dispatch based on primary schema keyword ---
    core_annotation: TypeAnnotation = None

    if dispatch := _get_allof_oneof_anyof_dispatch(name, core_schema, cache):
        core_annotation = dispatch()

    elif "enum" in core_schema:
        core_annotation = _handle_enum_schema(core_schema["enum"])
    elif core_schema.get("type") == "string":
        core_annotation = _handle_string_schema(core_schema)
    elif core_schema.get("type") in _PRIMITIVE_MAPPING:
        core_annotation = _handle_primitive_schema(core_schema["type"])
    elif core_schema.get("type") == "array":
        core_annotation = _handle_array_schema(name, core_schema, cache)
    elif _is_object_schema(core_schema):  # Object type or 'properties' present
        if _is_pure_map_schema(core_schema):
            core_annotation = _handle_pure_map_schema(name, core_schema, cache)
        else:
            core_annotation = _handle_object(base_prop_name, core_schema, cache)
    elif not core_schema or core_schema.get("type") == "any":  # Empty or explicit 'any'
        core_annotation = Any
    elif annotation := _handlers_after_primitive_types(name, core_schema, cache):
        core_annotation = annotation
    else:
        # --- Unsupported or Unknown Schema ---
        _check_for_unsupported_keywords(name, core_schema)
        _check_for_non_spec_keywords(base_prop_name)

        # If no unsupported keywords found, the schema structure is just unknown
        raise TypeError(f"Cannot determine Pydantic type for schema fragment (name='{name}'): {core_schema}")

    # --- Apply Nullability ---
    # Make the determined core annotation nullable if necessary
    return _make_type_nullable(core_annotation) if is_nullable else core_annotation


# ---------------------------------------------------------------------------
# Helper functions for _schema_to_type_annotation (Dispatch Targets)
# ---------------------------------------------------------------------------


def _extract_nullability(schema: JsonSchema, schema_type_keyword: Any) -> tuple[bool, JsonSchema]:
    """
    Determines if a schema implies nullability (via "type": ["...", "null"]).
    Returns the nullability status and a potentially modified schema
    with the "null" type removed for further processing.
    """
    is_nullable = False
    core_schema = schema  # Assume no changes needed initially

    if isinstance(schema_type_keyword, list):
        if "null" in schema_type_keyword:
            is_nullable = True
            non_null_types = [t for t in schema_type_keyword if t != "null"]

            if len(non_null_types) == 1:
                # Simplified: ["string", "null"] -> process as "string", add null later
                core_schema = schema.copy()  # Avoid modifying original schema
                core_schema["type"] = non_null_types[0]
            elif len(non_null_types) > 1:
                # Compound: ["string", "number", "null"] -> process as anyOf[string, number], add null later
                core_schema = schema.copy()
                core_schema.pop("type")  # Remove original type list
                core_schema["anyOf"] = [{"type": t} for t in non_null_types]
            else:  # Only "null" was present
                core_schema = {"type": "null"}  # Let the main dispatcher handle this

        else:
            # Multiple non-null types, no "null": ["string", "number"] -> anyOf[string, number]
            core_schema = schema.copy()
            core_schema.pop("type")
            core_schema["anyOf"] = [{"type": t} for t in schema_type_keyword]

    return is_nullable, core_schema


def _handle_oneof_anyof_schema(name: TypeName, schema: JsonSchema, cache: ModelCache) -> TypeAnnotation:
    """Handles 'oneOf' and 'anyOf' by creating a typing.Union."""
    combiner_keyword = "oneOf" if "oneOf" in schema else "anyOf"

    variants = []
    for i, sub_schema in enumerate(schema[combiner_keyword]):
        try:
            variant = _schema_to_type_annotation(f"{name}Variant{i}", name, sub_schema, cache)
            variants.append(variant)
        except SkipPropertyException as e:
            logger.debug(f"Skipping '{name}Variant{i}' in 'oneOf' and 'anyOf' schemas... Reason: {str(e)}")
            variants.append(Any)

    # Filter out NoneType if it was somehow generated as a variant,
    # nullability is handled separately.
    variants = [v for v in variants if v is not type(None)]
    if not variants:
        return type(None)  # Only null variants found
    # Use | operator syntax for Union if possible (requires Python 3.10+)
    # return reduce(lambda x, y: x | y, variants) # Might be less readable than Union[...]
    return Union[tuple(variants)]  # type: ignore[arg-type]


def _handle_enum_schema(enum_values: list[Any]) -> TypeAnnotation:
    """Handles 'enum' by creating a typing.Literal."""
    if not enum_values:
        raise ValueError("JSON Schema 'enum' cannot be empty.")
    # Literals can contain None, handle it directly
    return Literal[tuple(enum_values)]


def _handle_string_schema(schema: JsonSchema) -> TypeAnnotation:
    """Handles 'string' type, including 'pattern' constraint."""
    if pattern := schema.get("pattern"):
        # Apply pattern constraint using Annotated and StringConstraints
        return Annotated[str, StringConstraints(pattern=pattern)]
    else:
        return str


def _handle_primitive_schema(schema_type: str) -> TypeAnnotation:
    """Handles basic primitive types (number, integer, boolean)."""
    return _PRIMITIVE_MAPPING[schema_type]


def _handle_array_schema(name: TypeName, schema: JsonSchema, cache: ModelCache) -> TypeAnnotation:
    """Handles 'array' type, determining the item type."""
    item_schema: dict = schema.get("items", {})  # Default to empty schema -> Any
    cache.set_path("items")

    if dispatch := _get_allof_oneof_anyof_dispatch(name, item_schema, cache):
        item_type = dispatch()
        cache.unset_path("items")  # Clean up path stack
        # List items incorrectly handle union types, preventing composite items. See `test_any_of_array_schema`.
        return list[Union[item_type, Any]]

    item_type = Any if not item_schema else _schema_to_type_annotation(f"{name}Item", name, item_schema, cache)
    cache.unset_path("items")  # Clean up path stack
    return list[item_type]


def _check_for_unsupported_keywords(name: TypeName, schema: JsonSchema) -> None:
    """Raises NotImplementedError if unsupported keywords are found."""
    unsupported = {"if", "then", "else", "patternProperties"}
    found_unsupported = unsupported.intersection(schema)
    if found_unsupported:
        raise NotImplementedError(f"Unsupported JSON Schema features in schema for '{name}': {found_unsupported}")


def _check_for_non_spec_keywords(name: TypeName):
    """Some MCPs do not follow the JSON Draft 07 specification."""
    non_spec_properties = {"defaultValue"}
    if name in non_spec_properties:
        raise SkipPropertyException(f"got unsupported {name}")


# ===========================================================================
# Type Utility Helpers (_make_type_nullable, _normalise_name)
# ===========================================================================


def _make_type_nullable(inner_type: TypeAnnotation) -> TypeAnnotation:
    """
    Makes a type annotation nullable (T | None) if it isn't already.

    Handles base types, Union, Literal, Annotated, and prevents None | None.
    """
    if inner_type is Any:  # Any includes None implicitly
        return Any
    if inner_type is type(None):  # Already None
        return type(None)

    origin = get_origin(inner_type)

    # Check if already nullable (Union with None or Literal containing None)
    if origin in (Union, UnionType) and type(None) in get_args(inner_type):
        return inner_type
    if origin is Literal and None in get_args(inner_type):
        return inner_type

    # Handle Annotated[T, ...] -> Annotated[T | None, ...]
    if origin is Annotated:
        base_type = get_args(inner_type)[0]
        metadata = get_args(inner_type)[1:]
        optional_base_type = _make_type_nullable(base_type)
        if optional_base_type is not base_type:  # Avoid Annotated[None | None, ...]
            return Annotated[optional_base_type, *metadata]
        else:
            return inner_type  # Base was already nullable

    # Default case: Add | None using PEP 604 union syntax
    return inner_type | None


def _normalise_name(raw_name: str) -> TypeName:
    """
    Converts a raw string (e.g., from schema 'title' or property name)
    into a valid PascalCase Python identifier for use as a class/model name.
    """
    # Remove non-alphanumeric characters, convert to title case, join parts
    cleaned = "".join(c for c in raw_name.title() if c.isalnum())
    # Ensure it's not empty, default to "Model"
    return cleaned or "Model"


def _extract_model_name_from_ref(ref_path: str) -> str:
    """
    Extract model name from $ref path.

    Examples:
        "#/$defs/condition" -> "Condition"
        "#/definitions/Range" -> "Range"
    """
    # Remove "#/" prefix and get last segment
    parts = ref_path.replace("#/", "").split("/")
    if parts:
        return _normalise_name(parts[-1])
    return "Model"


# ===========================================================================
# Model Rendering Utility (REVISED _render_type_line AGAIN)
# ===========================================================================


# (Keep model_to_string as it was, the change is in its helper)
def model_to_string(
    model_cls: type[BaseModel],
    *,
    indent: int = 0,
    _seen: set[int] | None = None,  # Tracks models processed in THIS recursive call stack
) -> str:
    """
    Recursively render *model_cls* in a human‑friendly form including defaults
    and descriptions. Cycles are detected using the _seen set.
    """
    pad = " " * indent
    lines: list[str] = [f"{pad}{model_cls.__name__}:"]
    # Initialize seen set for this call stack if it's the top-level call
    seen = _seen if _seen is not None else set()

    # --- Cycle Detection ---
    model_id = id(model_cls)
    if model_id in seen:
        lines.append(f"{pad}  <recursive ref to {model_cls.__name__}>")
        return "\n".join(lines)
    seen.add(model_id)  # Mark this model as seen in this path
    # --- End Cycle Detection ---

    if not model_cls.model_fields:
        lines.append(f"{pad}  <no fields>")

    for field_name, field_info in model_cls.model_fields.items():
        ann = field_info.annotation
        default_repr = ""
        if not field_info.is_required():
            default = field_info.get_default(call_default_factory=False)
            default_repr = f" = {default!r}"

        # Pass the *updated* seen set down for recursive calls
        lines.extend(
            _render_type_line(
                field_name,
                ann,
                field_info,
                default_repr,
                indent + 4,
                seen,
            )
        )

    # Remove model from seen set when returning up the stack, allowing it
    # to be potentially rendered fully via a different path if needed.
    # This is subtle: prevents premature cycle breaks if a model appears
    # multiple times non-recursively (e.g., field1: ModelA, field2: ModelA)
    # UPDATE: Keeping it simple - cycle detection prevents infinite loops,
    # let's not remove from seen here, rely on the top-level call having the right seen set.
    # seen.remove(model_id)

    return "\n".join(lines)


def _collect_nested_models(
    hint: Any,
    seen: set[int],
    result: dict[int, tuple[type[BaseModel], Any]] | None = None,
) -> dict[int, tuple[type[BaseModel], Any]]:
    """Recursively collect BaseModel subclasses within a type hint.

    Skips models already in *seen* (rendered by a caller) and deduplicates
    within the current field via *result*.
    """
    if result is None:
        result = {}
    origin = get_origin(hint)
    args = get_args(hint)
    type_to_check = args[0] if origin is Annotated else hint
    if inspect.isclass(type_to_check) and issubclass(type_to_check, BaseModel):
        model_id = id(type_to_check)
        if model_id not in seen and model_id not in result:
            result[model_id] = (type_to_check, hint)
    if origin is not None and args:
        for arg in args:
            _collect_nested_models(arg, seen, result)
    return result


def _extract_nested_lines(nested_model_str: str, model_name: str) -> list[str]:
    """Return the body lines from a nested model's rendered string.

    Suppresses output when the only line is a recursive-reference marker, and
    strips the model-name header line so the caller can indent it differently.
    """
    nested_lines = nested_model_str.split("\n")
    if not nested_lines:
        return []
    # Single line containing a recursive ref — nothing useful to show.
    if len(nested_lines) == 1 and "<recursive ref>" in nested_lines[0]:
        return []
    # Skip the model-name header line when present.
    start = 1 if nested_lines[0].strip().startswith(f"{model_name}:") else 0
    return nested_lines[start:]


def _render_type_line(
    name: str,
    typ: TypeAnnotation,  # Annotation of the field
    field_info: FieldInfo,
    default_repr: str,
    indent: int,
    seen: set[int],  # Use the 'seen' set passed down from model_to_string
) -> list[str]:
    """
    Renders a single field line, including description, and potentially recurses
    to show definitions of nested Pydantic models found within the type hint.
    """
    pad = " " * indent
    desc_pad = " " * (indent + 2)
    lines = [f"{pad}{name}: {_render_type_name(typ)}{default_repr}"]

    if field_info.description:
        lines.extend(
            textwrap.wrap(
                field_info.description,
                width=80 - len(desc_pad),
                initial_indent=f"{desc_pad}# ",
                subsequent_indent=f"{desc_pad}# ",
            )
        )

    for model_class, type_hint_part in _collect_nested_models(typ, seen).values():
        lines.append(f"{pad}  ({_render_type_name(type_hint_part)} details):")
        nested_model_str = model_to_string(model_class, indent=indent + 4, _seen=seen)
        lines.extend(_extract_nested_lines(nested_model_str, model_class.__name__))

    return lines


def _render_union_type(args: tuple[Any, ...]) -> str:
    """Render a Union/UnionType, appending '| None' when NoneType is present."""
    non_none = [a for a in args if a is not type(None)]
    rendered = " | ".join(_render_type_name(a) for a in non_none)
    if type(None) not in args:
        return rendered
    return f"{rendered} | None" if rendered else "None"


def _render_list_type(args: tuple[Any, ...]) -> str:
    """Render a list type hint, e.g. list[str]."""
    return f"list[{_render_type_name(args[0])}]" if args else "list"


def _render_dict_type(args: tuple[Any, ...]) -> str:
    """Render a dict type hint, e.g. dict[str, int]."""
    if len(args) == 2:
        return f"dict[{_render_type_name(args[0])}, {_render_type_name(args[1])}]"
    return "dict"


def _render_type_name(typ: Any) -> str:
    """Generate a string representation for a type hint."""
    origin = get_origin(typ)
    args = get_args(typ)

    if origin in (Union, UnionType):
        return _render_union_type(args)
    if origin is Literal:
        return f"Literal[{', '.join(repr(a) for a in args)}]"
    if origin is list:
        return _render_list_type(args)
    if origin is dict:
        return _render_dict_type(args)
    if origin is Annotated:
        return f"Annotated[{_render_type_name(args[0])}, ...]"
    if hasattr(typ, "__name__"):
        return typ.__name__
    return str(typ).replace("typing.", "")
