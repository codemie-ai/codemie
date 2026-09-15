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

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

import yaml

from codemie.core.exceptions import MaterializationFailedException, MissingPlaceholderVariablesException

PLACEHOLDER_PATTERN: re.Pattern[str] = re.compile(r"\$\{input:([^}]+)\}")


def extract_placeholder_names(text: str | None) -> list[str]:
    if not text:
        return []
    names: list[str] = []
    seen: set[str] = set()
    for match in PLACEHOLDER_PATTERN.finditer(text):
        name = match.group(1)
        if name not in seen:
            seen.add(name)
            names.append(name)
    return names


def substitute_placeholders(text: str | None, variables: dict[str, str]) -> str | None:
    if not text:
        return text
    return PLACEHOLDER_PATTERN.sub(lambda m: variables.get(m.group(1), m.group(0)), text)


def substitute_placeholders_for_yaml_load(text: str) -> tuple[str, dict[str, str]]:
    """Replace ${input:...} tokens with YAML-safe sentinel identifiers before yaml.safe_load.

    Unlike quote_unquoted_placeholders, this approach is context-independent: it replaces every
    placeholder token regardless of whether it appears in a bare scalar, a quoted string, or a
    block scalar — so mid-string placeholders inside double-quoted YAML values are handled
    correctly and do not produce broken YAML after substitution.

    A UUID prefix is generated once per call so that sentinel strings cannot collide with
    legitimate content that already appears in the template.

    Returns (preprocessed_text, sentinel_to_original_map). Pass the map to
    restore_sentinels_in_obj() after yaml.safe_load to recover original tokens in the dict.
    """
    sentinel_to_original: dict[str, str] = {}
    index = 0
    uid = uuid4().hex

    def _replacer(m: re.Match) -> str:
        nonlocal index
        sentinel = f"__CMPH_{uid}_{index:04d}__"
        sentinel_to_original[sentinel] = m.group(0)
        index += 1
        return sentinel

    return PLACEHOLDER_PATTERN.sub(_replacer, text), sentinel_to_original


def restore_sentinels_in_obj(obj: Any, sentinel_map: dict[str, str]) -> Any:
    """Recursively replace sentinel strings in a parsed YAML structure with original tokens."""
    if isinstance(obj, str):
        for sentinel, original in sentinel_map.items():
            obj = obj.replace(sentinel, original)
        return obj
    if isinstance(obj, dict):
        return {
            restore_sentinels_in_obj(k, sentinel_map): restore_sentinels_in_obj(v, sentinel_map) for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [restore_sentinels_in_obj(item, sentinel_map) for item in obj]
    return obj


def materialize_template_source(template_source: str, variables: dict[str, str]) -> dict[str, Any]:
    """Safely substitute template placeholders in a parsed YAML tree.

    Parsing before substitution preserves all caller values as scalar strings, so a value
    cannot inject YAML nodes or alter the surrounding structure.
    """
    required_variables = extract_placeholder_names(template_source)
    missing_variables = [name for name in required_variables if name not in variables or not variables[name].strip()]
    if missing_variables:
        raise MissingPlaceholderVariablesException(missing_variables)

    normalized_variables = {name: variables[name].strip() for name in required_variables}
    try:
        preprocessed, sentinel_map = substitute_placeholders_for_yaml_load(template_source)
        parsed = yaml.safe_load(preprocessed)
    except yaml.YAMLError as exc:
        raise MaterializationFailedException("The template YAML cannot be parsed.") from exc

    if not isinstance(parsed, dict):
        raise MaterializationFailedException("The template must contain a YAML mapping.")

    restored = restore_sentinels_in_obj(parsed, sentinel_map)
    materialized = _substitute_variables_in_yaml_tree(restored, normalized_variables)
    if not isinstance(materialized.get("execution_config"), dict):
        raise MaterializationFailedException("The template execution_config must be a YAML mapping.")
    if extract_placeholder_names(yaml.safe_dump(materialized)):
        raise MaterializationFailedException("The materialized template contains unresolved placeholders.")
    return materialized


def _substitute_variables_in_yaml_tree(value: Any, variables: dict[str, str]) -> Any:
    if isinstance(value, str):
        return PLACEHOLDER_PATTERN.sub(lambda match: variables[match.group(1)], value)
    if isinstance(value, list):
        return [_substitute_variables_in_yaml_tree(item, variables) for item in value]
    if isinstance(value, dict):
        materialized: dict[Any, Any] = {}
        for key, item in value.items():
            materialized_key = _substitute_variables_in_yaml_tree(key, variables)
            if materialized_key in materialized:
                raise MaterializationFailedException("Variable values produce duplicate YAML mapping keys.")
            materialized[materialized_key] = _substitute_variables_in_yaml_tree(item, variables)
        return materialized
    return value
