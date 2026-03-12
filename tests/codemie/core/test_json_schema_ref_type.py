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

from typing import get_args, get_type_hints
import json
from codemie.core.json_schema_utils import _create_model_from_schema, Cache

JSON_SCHEMA_WITH_COMPLEX_REF = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "name": "update_content_type",
    "description": "Update an existing content type. The handler will merge your field updates with existing content type data, so you only need to provide the fields and properties you want to change.",
    "inputSchema": {
        "type": "object",
        "$defs": {
            "Size": {
                "type": "object",
                "properties": {"min": {"type": "number"}, "max": {"type": "number"}},
                "additionalProperties": False,
            },
            "ValidationItem": {
                "type": "object",
                "properties": {
                    "size": {"$ref": "#/$defs/Size"},
                    "assetImageDimensions": {
                        "type": "object",
                        "properties": {
                            "width": {"$ref": "#/$defs/Size"},
                            "height": {"$ref": "#/$defs/Size"},
                        },
                        "additionalProperties": False,
                    },
                    "assetFileSize": {"$ref": "#/$defs/Size"},
                },
                "additionalProperties": False,
            },
        },
        "properties": {
            "fields": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "validations": {
                            "type": "array",
                            "items": {"$ref": "#/$defs/ValidationItem"},
                        },
                        "items": {
                            "type": "object",
                            "properties": {
                                "validations": {
                                    "type": "array",
                                    "items": {"$ref": "#/$defs/ValidationItem"},
                                }
                            },
                            "required": ["validations"],
                            "additionalProperties": False,
                        },
                    },
                    "required": ["validations"],
                    "additionalProperties": False,
                },
                "description": "Array of field definitions for the content type. Will be merged with existing fields.",
            }
        },
        "required": ["fields"],
        "additionalProperties": False,
    },
}
JSON_SCHEMA_WITH_COMPLEX_REF_INPUT = {
    "fields": [
        {
            "validations": [
                {
                    "size": {"min": 100.0, "max": 500.0},
                    "assetImageDimensions": {
                        "width": {"min": 100.0, "max": 500.0},
                        "height": {"min": 100.0, "max": 500.0},
                    },
                    "assetFileSize": {"min": 100.0, "max": 500.0},
                }
            ],
            "items": {
                "validations": [
                    {
                        "size": {"min": 50.0, "max": 1000.0},
                        "assetImageDimensions": {
                            "width": {"min": 50.0, "max": 1000.0},
                            "height": {"min": 50.0, "max": 1000.0},
                        },
                        "assetFileSize": {"min": 50.0, "max": 1000.0},
                    }
                ]
            },
        }
    ]
}


def test_json_schema_ref_type_deep_complex_ref():
    cache = Cache()
    model = _create_model_from_schema("Test", JSON_SCHEMA_WITH_COMPLEX_REF["inputSchema"], cache)

    data = model(**JSON_SCHEMA_WITH_COMPLEX_REF_INPUT)
    extracted = data.model_dump()

    assert json.dumps(extracted) == json.dumps(JSON_SCHEMA_WITH_COMPLEX_REF_INPUT)

    # Check that Size model is cached in $defs
    size_model = cache.get_model_by_path("#/$defs/Size")
    assert size_model, "Size model not found in cache"

    # Check that ValidationItem is cached
    validation_item_model = cache.get_model_by_path("#/$defs/ValidationItem")
    assert validation_item_model, "ValidationItem model not found in cache"

    # Navigate through the model structure to verify references
    fields_list = get_type_hints(model)["fields"]
    fields_type = get_args(fields_list)[0]
    validations_list = get_type_hints(fields_type)["validations"]
    validations = get_args(validations_list)[0]

    # Verify ValidationItem is the correct model
    assert validations is validation_item_model, "validations should reference ValidationItem model"

    # Get the size field from ValidationItem
    size = get_type_hints(validation_item_model)["size"]

    # Get width and height from assetImageDimensions
    asset_dims = get_type_hints(validation_item_model)["assetImageDimensions"]
    asset_dims_type = get_args(asset_dims)[0]  # Unwrap Optional if present
    width = get_type_hints(asset_dims_type)["width"]
    height = get_type_hints(asset_dims_type)["height"]

    # All should reference the same Size model (may be wrapped in Optional)
    size_unwrapped = get_args(size)[0] if get_args(size) else size
    width_unwrapped = get_args(width)[0] if get_args(width) else width
    height_unwrapped = get_args(height)[0] if get_args(height) else height

    assert size_unwrapped is size_model, "size should reference Size model"
    assert width_unwrapped is size_model, "width should reference Size model"
    assert height_unwrapped is size_model, "height should reference Size model"


JSON_SCHEMA_WITH_DEFINITIONS_REF_TO_REF = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "name": "update_content_type",
    "inputSchema": {
        "type": "object",
        "definitions": {
            "Range": {
                "type": "object",
                "properties": {"min": {"type": "number"}, "max": {"type": "number"}},
                "additionalProperties": False,
            },
            "Size": {
                "type": "object",
                "properties": {
                    "min": {"type": "number"},
                    "max": {"type": "number"},
                    "range": {"$ref": "#/definitions/Range"},
                },
                "additionalProperties": False,
                "required": ["min", "max", "range"],
            },
        },
        "properties": {
            "fields": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "validations": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "size": {"$ref": "#/definitions/Size"},
                                    "assetImageDimensions": {
                                        "type": "object",
                                        "properties": {
                                            "width": {"$ref": "#/definitions/Size"},
                                            "height": {"$ref": "#/definitions/Size"},
                                        },
                                        "additionalProperties": False,
                                    },
                                },
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": ["validations"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["fields"],
        "additionalProperties": False,
    },
}

JSON_SCHEMA_WITH_DEFINITIONS_REF_TO_REF_INPUT = {
    "fields": [
        {
            "validations": [
                {
                    "size": {"min": 100.0, "max": 200.0, "range": {"min": None, "max": None}},
                    "assetImageDimensions": {
                        "width": {"min": 10.0, "max": 20.0, "range": {"min": None, "max": None}},
                        "height": {"min": 30.0, "max": 40.0, "range": {"min": 30.0, "max": 40.0}},
                    },
                }
            ]
        }
    ]
}


def test_json_schema_definitions_ref_to_ref_nested_size_and_dimensions():
    cache = Cache()
    model = _create_model_from_schema("Test", JSON_SCHEMA_WITH_DEFINITIONS_REF_TO_REF["inputSchema"], cache)

    data = model(**JSON_SCHEMA_WITH_DEFINITIONS_REF_TO_REF_INPUT)
    extracted = data.model_dump()

    assert json.dumps(extracted) == json.dumps(JSON_SCHEMA_WITH_DEFINITIONS_REF_TO_REF_INPUT)

    # Paths to cached models
    size_path = "#/properties/fields/items/properties/validations/items/properties/size"
    width_path = (
        "#/properties/fields/items/properties/validations/items/properties/assetImageDimensions/properties/width"
    )
    height_path = (
        "#/properties/fields/items/properties/validations/items/properties/assetImageDimensions/properties/height"
    )

    size_model = cache.get_model_by_path(size_path)
    width_model = cache.get_model_by_path(width_path)
    height_model = cache.get_model_by_path(height_path)
    range_model = cache.get_model_by_path("#/definitions/Range")

    assert size_model is not None
    assert width_model is not None
    assert height_model is not None

    range_from_size = get_type_hints(get_args(width_model)[0])["range"]

    assert range_from_size is not None

    assert range_from_size is range_model


JSON_SCHEMA_WITH_PROPERTIES_REF_TO_REF = {
    "$schema": "https://json-schema.org/draft/2019-09/schema",
    "inputSchema": {
        "type": "object",
        "properties": {
            "Range": {
                "type": "object",
                "properties": {"min": {"type": "number"}, "max": {"type": "number"}},
                "required": ["min", "max"],
            },
            "Size": {
                "type": "object",
                "properties": {
                    "min": {"type": "number"},
                    "max": {"type": "number"},
                    "range": {"$ref": "#/properties/Range"},
                },
            },
            "fields": {
                "type": "array",
                "items": {"$ref": "#/properties/Size"},
            },
        },
    },
}

JSON_SCHEMA_WITH_PROPERTIES_REF_TO_REF_INPUT = {
    "Range": None,
    "Size": None,
    "fields": [
        {"min": None, "max": None, "range": {"min": 10.0, "max": 10.0}},
    ],
}


def test_json_schema_properties_ref_to_ref():
    cache = Cache()
    model = _create_model_from_schema("Test", JSON_SCHEMA_WITH_PROPERTIES_REF_TO_REF["inputSchema"], cache)

    data = model(**JSON_SCHEMA_WITH_PROPERTIES_REF_TO_REF_INPUT)
    extracted = data.model_dump()

    assert json.dumps(extracted) == json.dumps(JSON_SCHEMA_WITH_PROPERTIES_REF_TO_REF_INPUT)

    # Paths to cached models
    size_path = "#/properties/Size"
    fields_path = "#/properties/fields"

    size_model = cache.get_model_by_path(size_path)
    assert size_model is not None, "Size model should be cached"

    fields_model = cache.get_model_by_path(fields_path)
    assert fields_model is not None, "Fields property should be cached"

    # Navigate through fields to get the array item type
    fields_hint = get_type_hints(model)["fields"]
    # fields is Optional[list[T]], so we need to unwrap
    fields_args = get_args(fields_hint)  # This gets Union[list[Size], None]
    if len(fields_args) > 0:
        # Get the non-None arg
        list_type = [arg for arg in fields_args if arg is not type(None)][0]
        # Now get the item type from list[Size]
        item_type_args = get_args(list_type)
        if len(item_type_args) > 0:
            fields_size_model = item_type_args[0]
            assert size_model is fields_size_model, "fields items should reference the same Size model"
