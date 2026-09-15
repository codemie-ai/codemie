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

import pytest

from codemie.workflows.placeholders import (
    extract_placeholder_names,
    materialize_template_source,
    substitute_placeholders,
    substitute_placeholders_for_yaml_load,
    restore_sentinels_in_obj,
)
from codemie.core.exceptions import MissingPlaceholderVariablesException


def test_extract_unique_first_seen_order():
    text = "a: ${input:foo}\nb: ${input:bar}\nc: ${input:foo}"
    assert extract_placeholder_names(text) == ["foo", "bar"]


def test_extract_empty():
    assert extract_placeholder_names("name: hello") == []


def test_substitute_replaces_known_variables():
    text = "Hello ${input:team_name}, role is ${input:role}."
    assert substitute_placeholders(text, {"team_name": "Alice", "role": "admin"}) == "Hello Alice, role is admin."


def test_substitute_preserves_unknown_variables():
    text = "Hello ${input:team_name}, ${input:unknown}."
    assert substitute_placeholders(text, {"team_name": "Alice"}) == "Hello Alice, ${input:unknown}."


def test_substitute_none_input_returns_none():
    assert substitute_placeholders(None, {"x": "y"}) is None


def test_substitute_empty_variables_no_change():
    assert substitute_placeholders("Hi ${input:x}.", {}) == "Hi ${input:x}."


def test_extract_uses_exact_capture_group():
    text = "a: ${input:good}\nb: ${input:  }\nc: ${input:bad:name}"
    assert extract_placeholder_names(text) == ["good", "  ", "bad:name"]


def test_materialize_template_source_requires_non_blank_values():
    source = "name: ${input:name}\nexecution_config: {}\n"

    with pytest.raises(MissingPlaceholderVariablesException) as exc_info:
        materialize_template_source(source, {"name": "  "})

    assert exc_info.value.missing_variables == ["name"]


def test_materialize_template_source_preserves_values_as_yaml_scalars():
    source = "name: Example\n" "description: ${input:team}\n" "execution_config:\n" "  team: ${input:team}\n"

    materialized = materialize_template_source(source, {"team": "foo\ninjected_key: injected_val"})

    assert materialized["description"] == "foo\ninjected_key: injected_val"
    assert materialized["execution_config"]["team"] == "foo\ninjected_key: injected_val"
    assert "injected_key" not in materialized["execution_config"]


# CR-007: sentinel collision — template containing old-pattern string must not be corrupted


def test_substitute_for_yaml_load_does_not_corrupt_old_sentinel_pattern():
    # A YAML template that happens to contain the old CODEMIEPLACEHOLDER0000X literal
    # must survive the round-trip unchanged (the UUID prefix makes collision impossible)
    yaml_text = "note: CODEMIEPLACEHOLDER0000X\nname: ${input:project}"
    processed, sentinel_map = substitute_placeholders_for_yaml_load(yaml_text)
    import yaml

    parsed = yaml.safe_load(processed)
    restored = restore_sentinels_in_obj(parsed, sentinel_map)
    # The note field must not have been overwritten with a placeholder
    assert restored["note"] == "CODEMIEPLACEHOLDER0000X"
    assert restored["name"] == "${input:project}"


# CR-008: restore_sentinels_in_obj must also restore sentinels that appear in dict keys


def test_restore_sentinels_restores_dict_keys():
    sentinel_map = {"__CMPH_abc123_0000__": "${input:field_name}"}
    obj = {"__CMPH_abc123_0000__": "some_value", "normal_key": "val"}
    result = restore_sentinels_in_obj(obj, sentinel_map)
    assert "${input:field_name}" in result
    assert result["${input:field_name}"] == "some_value"
    assert "normal_key" in result
