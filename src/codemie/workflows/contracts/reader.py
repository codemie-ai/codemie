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

"""Output contract reader (EPMCDME-16503, parent §3.1): classify the `output_schema` of a state.

Forms: schema (what the runtime accepts), example (names only), list (a JSON array whose dict items give the names,
or an array-root schema whose `items.properties` do), invalid (anything else) and none (no contract). Nothing here
raises on hostile input: text over 64 KB or nested deeper than 32 levels is never parsed, and key recovery from
unparseable text is one linear pass.

An unknown source is a contract with `known_source == False`: it contributes nothing and can never cause a "not
guaranteed" finding. `producer_note` says what the producer gets: an advisory (convert to a schema) or a WARNING
(unknown source).
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from enum import Enum

import jsonschema

from codemie.workflows.contracts.input_contract import (
    MAX_INPUT_CONTRACT_BYTES,
    MAX_INPUT_CONTRACT_DEPTH,
    _is_valid_type,
    check_input_contract,
    _nesting_depth_exceeds_limit,
    utf8_size,
)

MAX_CONTRACT_BYTES = MAX_INPUT_CONTRACT_BYTES
MAX_CONTRACT_DEPTH = MAX_INPUT_CONTRACT_DEPTH

# Root keys that make an object a schema attempt rather than an example (parent §3.1).
_SCHEMA_ROOT_KEYS = frozenset(
    {"type", "properties", "$schema", "$ref", "$defs", "definitions", "allOf", "anyOf", "oneOf"}
)


class ContractForm(str, Enum):
    NONE = "none"
    SCHEMA = "schema"
    EXAMPLE = "example"
    LIST = "list"
    INVALID = "invalid"


class ProducerNote(str, Enum):
    CONVERT_TO_SCHEMA = "advisory"
    UNKNOWN_SOURCE = "warning"


@dataclass(frozen=True)
class ContractField:
    """A top-level field; `types` / `required` are `None` when the contract does not state them."""

    name: str
    types: tuple[str, ...] | None = None
    required: bool | None = None


@dataclass(frozen=True)
class OutputContract:
    form: ContractForm
    fields: tuple[ContractField, ...] = ()
    known_source: bool = False
    recovered: bool = False
    producer_note: ProducerNote | None = None
    reason: str = ""

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(field.name for field in self.fields)


def classify_output_contract(value: object) -> OutputContract:
    """Classify an `output_schema` value (JSON text, or an already parsed object such as a transform schema)."""
    if value is None:
        return OutputContract(ContractForm.NONE)
    if isinstance(value, str):
        return _classify_text(value)
    if isinstance(value, dict | list):
        reason = _object_bound_violation(value)
        if reason:
            return _unknown(ContractForm.INVALID, reason)
        return _classify_document(value)
    return _unknown(ContractForm.INVALID, "not_text")


def read_input_contract_fields(text: object) -> tuple[ContractField, ...] | None:
    """The top-level fields of a declared `input_schema`, or `None` when the AC-16 check rejects it.

    A declared input contract is judged by the shallow structural check alone, the same one the schema stage applies
    on save. It is deliberately not run through the full JSON Schema meta-check used for output contracts: a keyword
    outside that check (`minLength: -1`, say) is ignored, so it can never discard the declared fields.
    """
    if check_input_contract(text):
        return None
    document = json.loads(str(text))
    return _schema_fields(document["properties"], document.get("required"))


def _classify_text(text: str) -> OutputContract:
    if not text.strip():
        return OutputContract(ContractForm.NONE)
    size = utf8_size(text)
    if size is None:
        return _unknown(ContractForm.INVALID, "not_encodable")
    if size > MAX_CONTRACT_BYTES:
        return _unknown(ContractForm.INVALID, "too_large")
    if _nesting_depth_exceeds_limit(text):
        return _unknown(ContractForm.INVALID, "too_deep")
    try:
        document = json.loads(text)
    except (ValueError, RecursionError):
        return _recover_from_unparseable(text)
    return _classify_document(document)


def _classify_document(document: object) -> OutputContract:
    if isinstance(document, list):
        return _list_contract(document)
    if not isinstance(document, dict):
        return _unknown(ContractForm.INVALID, "not_object")
    if document.get("type") == "array":
        return _array_root_contract(document)
    if _is_schema_form(document):
        properties = document["properties"]
        return OutputContract(
            ContractForm.SCHEMA, _schema_fields(properties, document.get("required")), known_source=True
        )
    if _SCHEMA_ROOT_KEYS.intersection(document):
        return _unknown(ContractForm.INVALID, "schema_check_failed")
    fields = tuple(ContractField(str(name)) for name in document)
    return OutputContract(ContractForm.EXAMPLE, fields, known_source=bool(fields))


def _is_schema_form(document: dict[str, object]) -> bool:
    """Mirror of `validate_json_schema` (agents/utils.py); parity is tested, not imported."""
    if not isinstance(document.get("type"), str) or not isinstance(document.get("properties"), dict):
        return False
    try:
        jsonschema.Draft7Validator.check_schema(document)
    except (jsonschema.exceptions.SchemaError, RecursionError, TypeError):
        return False
    return True


def _schema_fields(properties: dict[str, object], required: object) -> tuple[ContractField, ...]:
    required_items = required if isinstance(required, list) else []
    required_names = {item for item in required_items if isinstance(item, str)}
    fields = []
    for name, entry in properties.items():
        declared = entry.get("type") if isinstance(entry, dict) else None
        types = None
        if _is_valid_type(declared):
            types = (declared,) if isinstance(declared, str) else tuple(declared)
        fields.append(ContractField(str(name), types, str(name) in required_names))
    return tuple(fields)


def _array_root_contract(document: dict[str, object]) -> OutputContract:
    """An array root is a list contract: names come from `items.properties`; a root `properties` is ignored."""
    items = document.get("items")
    properties = items.get("properties") if isinstance(items, dict) else None
    fields = _schema_fields(properties, items.get("required")) if isinstance(properties, dict) else ()
    if not fields:
        return _unknown(ContractForm.LIST, "array_root_without_item_properties")
    return OutputContract(ContractForm.LIST, fields, known_source=True)


def _list_contract(items: list[object]) -> OutputContract:
    names: dict[str, None] = {}
    for item in items:
        if isinstance(item, dict):
            names.update(dict.fromkeys(str(name) for name in item))
    if not names:
        return _unknown(ContractForm.LIST, "list_without_dict_items")
    return OutputContract(ContractForm.LIST, tuple(ContractField(name) for name in names), known_source=True)


def _recover_from_unparseable(text: str) -> OutputContract:
    if text.lstrip().startswith("["):
        names = _recover_keys(text, ["[", "{"])
        if not names:
            return _unknown(ContractForm.LIST, "list_without_recovered_keys")
        return OutputContract(
            ContractForm.LIST,
            tuple(ContractField(name) for name in names),
            known_source=True,
            recovered=True,
            producer_note=ProducerNote.CONVERT_TO_SCHEMA,
        )
    names = _recover_keys(text, ["{"])
    if not names:
        return _unknown(ContractForm.INVALID, "no_recovered_keys")
    return OutputContract(
        ContractForm.INVALID,
        tuple(ContractField(name) for name in names),
        known_source=True,
        recovered=True,
        producer_note=ProducerNote.CONVERT_TO_SCHEMA,
    )


def _unknown(form: ContractForm, reason: str) -> OutputContract:
    return OutputContract(form, producer_note=ProducerNote.UNKNOWN_SOURCE, reason=reason)


def _recover_keys(text: str, target: list[str]) -> list[str]:
    """Collect the quoted strings followed by `:` that sit exactly at the container path `target`.

    One left-to-right pass: strings, `//` and `/* */` comments are skipped as units, so ellipses, placeholders and
    trailing commas need no handling. Container depth is at most 32 here (checked before), so the path compare is
    constant.
    """
    keys: dict[str, None] = {}
    stack: list[str] = []
    pending: str | None = None
    index, length = 0, len(text)
    while index < length:
        char = text[index]
        if char == '"':
            end, value = _read_string(text, index)
            pending = value if stack == target else None
            index = end
        elif char.isspace():
            index += 1
        elif char == "/" and text[index + 1 : index + 2] in ("/", "*"):
            index = _skip_comment(text, index)
        else:
            _read_punctuation(char, pending, stack, keys)
            pending = None
            index += 1
    return list(keys)


def _read_punctuation(char: str, pending: str | None, stack: list[str], keys: dict[str, None]) -> None:
    """Record `pending` as a key when `char` is its `:`, and track the container path on brackets."""
    if char == ":" and pending is not None:
        keys.setdefault(pending)
    if char in "{[":
        stack.append(char)
    elif char in "}]" and stack:
        stack.pop()


def _read_string(text: str, start: int) -> tuple[int, str | None]:
    """Return (index after the string, its value); the value is `None` for an unterminated string."""
    index, length = start + 1, len(text)
    while index < length:
        char = text[index]
        if char == "\\":
            index += 2
        elif char == '"':
            raw = text[start + 1 : index]
            if "\\" in raw:
                with contextlib.suppress(ValueError):
                    raw = json.loads(text[start : index + 1])
            return index + 1, raw
        else:
            index += 1
    return length, None


def _skip_comment(text: str, start: int) -> int:
    if text[start + 1] == "/":
        end = text.find("\n", start)
    else:
        end = text.find("*/", start + 2)
        end = end + 1 if end != -1 else -1
    return len(text) if end == -1 else end + 1


def _object_bound_violation(value: object) -> str | None:
    """Bounds for already parsed objects: nesting depth, and node count as a stand-in for the byte limit."""
    stack: list[tuple[object, int]] = [(value, 1)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        if not isinstance(current, dict | list):
            continue
        if depth > MAX_CONTRACT_DEPTH:
            return "too_deep"
        children = list(current.values()) if isinstance(current, dict) else current
        nodes += len(children) + 1
        if nodes > MAX_CONTRACT_BYTES:
            return "too_large"
        stack.extend((child, depth + 1) for child in children)
    return None
