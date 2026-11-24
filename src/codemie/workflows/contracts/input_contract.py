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

"""Structural check of a node's declared input contract (`input_schema`, AC-16).

The document's shape is checked against a fixed JSON schema with `jsonschema`. The module imports nothing from
`codemie`, so the schema stage can use it without pulling in the validation layer or any service. `$ref` is never
resolved and every keyword other than the ones checked below is accepted and ignored.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from jsonschema import Draft202012Validator, ValidationError

MAX_INPUT_CONTRACT_BYTES = 64 * 1024
MAX_INPUT_CONTRACT_DEPTH = 32
_MAX_NAME_LENGTH = 40
# A shape error at ["properties", <name>] is about the property itself, not a key inside it
_PROPERTY_PATH_DEPTH = 2

_VALUE_TYPES = frozenset({"string", "number", "integer", "boolean", "object", "array"})
_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9_.\-]")


@dataclass(frozen=True)
class InputContractProblem:
    """One structural problem; `message` is a fixed template that never echoes values."""

    code: str
    message: str


def check_input_contract(text: object) -> list[InputContractProblem]:
    """Return the structural problems of an `input_schema` value; an empty list means it is accepted."""
    if not isinstance(text, str):
        return [InputContractProblem("not_string", "input_schema must be a JSON text (a string)")]
    size = utf8_size(text)
    if size is None:
        return [
            InputContractProblem("not_encodable", "input_schema contains characters that cannot be encoded as UTF-8")
        ]
    if size > MAX_INPUT_CONTRACT_BYTES:
        return [InputContractProblem("too_large", f"input_schema is larger than {MAX_INPUT_CONTRACT_BYTES // 1024} KB")]
    if _nesting_depth_exceeds_limit(text):
        return [
            InputContractProblem("too_deep", f"input_schema is nested deeper than {MAX_INPUT_CONTRACT_DEPTH} levels")
        ]
    try:
        document = json.loads(text)
    except (ValueError, RecursionError):
        return [InputContractProblem("unparseable", "input_schema is not parseable JSON")]
    return _check_document(document)


def utf8_size(text: str) -> int | None:
    """The UTF-8 size of `text`, or `None` when it cannot be encoded (a lone surrogate).

    Encoding is strict on purpose: repairing the text first (`errors="replace"`) would measure data that differs from
    what was sent, so the caller rejects instead of deciding on the repaired copy.
    """
    try:
        return len(text.encode("utf-8"))
    except UnicodeEncodeError:
        return None


def _nesting_depth_exceeds_limit(text: str) -> bool:
    """Scan brackets outside JSON strings, so deep nesting is rejected before the parser can recurse on it."""
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > MAX_INPUT_CONTRACT_DEPTH:
                return True
        elif char in "]}":
            depth = max(0, depth - 1)
    return False


_PROPERTY_TYPE = {
    "anyOf": [
        {"enum": sorted(_VALUE_TYPES)},
        {
            "type": "array",
            "minItems": 1,
            "items": {"enum": [*sorted(_VALUE_TYPES), "null"]},
            "contains": {"enum": sorted(_VALUE_TYPES)},
        },
    ]
}
# The shape an `input_schema` document must have; the document is checked as data, never used as a schema.
_INPUT_CONTRACT_SHAPE = {
    "type": "object",
    "allOf": [{"required": ["type"]}, {"required": ["properties"]}],
    "properties": {
        "type": {"const": "object"},
        "properties": {
            "type": "object",
            "additionalProperties": {"type": "object", "required": ["type"], "properties": {"type": _PROPERTY_TYPE}},
        },
        "required": {"type": "array", "items": {"type": "string"}},
    },
}
_SHAPE_VALIDATOR = Draft202012Validator(_INPUT_CONTRACT_SHAPE)
_TYPE_VALIDATOR = Draft202012Validator(_PROPERTY_TYPE)
# Problems of the top-level keys and their place in the report; the problems of single properties come at place 2.
_KEY_PROBLEMS = {
    "type": (0, InputContractProblem("root_type", "input_schema must declare root type 'object'")),
    "properties": (1, InputContractProblem("properties", "input_schema must have a 'properties' object")),
    "required": (3, InputContractProblem("required_shape", "'required' must be a list of property names")),
}


def _check_document(document: object) -> list[InputContractProblem]:
    if not isinstance(document, dict):
        return [InputContractProblem("not_object", "input_schema must be a JSON object")]
    properties = document.get("properties")
    names = list(properties) if isinstance(properties, dict) else []
    found: dict[tuple[int, ...], InputContractProblem] = {}
    for error in _SHAPE_VALIDATOR.iter_errors(document):
        path = list(error.absolute_path)
        key = path[0] if path else error.validator_value[0]
        if key == "properties" and len(path) > 1:
            found.setdefault((2, names.index(path[1])), _property_problem(path[1], path, error))
        else:
            place, problem = _KEY_PROBLEMS[key]
            found.setdefault((place,), problem)
    problems = [found[place] for place in sorted(found)]
    if "required" in document and (3,) not in found:
        problems.extend(
            InputContractProblem(
                "required_unknown", f"'required' names '{_safe_name(item)}', which is not in 'properties'"
            )
            for item in document["required"]
            if item not in names
        )
    return problems


def _is_valid_type(value: object) -> bool:
    """A property `type`: one value type, or a non-empty list of them that may include null."""
    return _TYPE_VALIDATOR.is_valid(value)


def _property_problem(name: str, path: list[object], error: ValidationError) -> InputContractProblem:
    label = _safe_name(name)
    if len(path) == _PROPERTY_PATH_DEPTH and error.validator == "type":
        return InputContractProblem("property_shape", f"Property '{label}' must be an object with a 'type'")
    return InputContractProblem(
        "property_type",
        f"Property '{label}' needs a 'type' of string, number, integer, boolean, object or array "
        "(or a list of these, which may include null)",
    )


def _safe_name(name: object) -> str:
    """Names reach `<br>`-joined error markup, so keep a conservative character set and a bounded length."""
    return _UNSAFE_NAME_CHARS.sub("_", str(name))[:_MAX_NAME_LENGTH]
