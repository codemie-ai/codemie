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

from unittest.mock import patch

import pytest

from codemie.core.run_attributes import (
    PAIRS_FIELD,
    RunAttributesError,
    attributes_from_pairs,
    attributes_from_record,
    build_record_fields,
    encode_pair,
    pair_prefix,
    parse_attributes_filter,
    validate_attribute_key,
    validate_run_attributes,
)

WORK_ITEM_KEY = "com.epam.gen_ai.service.work_item.id"


@pytest.mark.parametrize("attrs", [None, {}])
def test_validate_run_attributes_treats_empty_as_none(attrs):
    assert validate_run_attributes(attrs) is None


def test_validate_run_attributes_accepts_the_longest_key_and_value():
    attrs = {"k" * 64: "v" * 128}

    assert validate_run_attributes(attrs) == attrs
    assert len(build_record_fields(attrs)["run_attribute_pairs"][0]) == 193


def test_validate_run_attributes_rejects_more_keys_than_configured():
    attrs = {f"key-{i}": "v" for i in range(3)}

    with patch("codemie.core.run_attributes.config.RUN_ATTRIBUTES_MAX_KEYS", 2):
        with pytest.raises(RunAttributesError, match="at most 2"):
            validate_run_attributes(attrs)


def test_validate_run_attributes_rejects_non_object():
    with pytest.raises(RunAttributesError, match="object"):
        validate_run_attributes(["team=core"])


@pytest.mark.parametrize(
    "key",
    ["a", "Team", "team id", ".team", "team.", "team=core", "k" * 65, "команда"],
)
def test_validate_attribute_key_rejects_invalid_format(key):
    with pytest.raises(RunAttributesError, match="Invalid attribute key"):
        validate_attribute_key(key)


@pytest.mark.parametrize("key", ["codemie.user", "otel.service.name"])
def test_validate_attribute_key_rejects_reserved_prefixes(key):
    with pytest.raises(RunAttributesError, match="reserved prefix"):
        validate_attribute_key(key)


@pytest.mark.parametrize("key", ["ab", WORK_ITEM_KEY, "team_1-a", "codemie-team"])
def test_validate_attribute_key_accepts_valid_keys(key):
    assert validate_attribute_key(key) == key


@pytest.mark.parametrize(
    "value, message",
    [
        (123, "must be a string"),
        ("", "1-128"),
        ("v" * 129, "1-128"),
        ("😀" * 65, "1-128"),
        ("line\nbreak", "control characters"),
        ("tab\tvalue", "control characters"),
        ("lone\ud800surrogate", "unpaired surrogates"),
        ("\udfffend", "unpaired surrogates"),
    ],
)
def test_validate_run_attributes_rejects_invalid_values(value, message):
    with pytest.raises(RunAttributesError, match=message):
        validate_run_attributes({"team": value})


def test_validate_run_attributes_counts_value_length_in_utf16_units():
    assert validate_run_attributes({"team": "😀" * 64}) == {"team": "😀" * 64}


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_parse_attributes_filter_returns_none_for_blank(raw):
    assert parse_attributes_filter(raw) is None


@pytest.mark.parametrize(
    "raw, message",
    [
        ("{team: core}", "valid JSON object"),
        ('["team=core"]', "JSON object"),
        ('{"team": 1}', "must be a string"),
        ('{"Team": "core"}', "Invalid attribute key"),
        ('{"team": "\\ud800"}', "unpaired surrogates"),
        ("[" * 100_000 + "]" * 100_000, "valid JSON object"),
        ('{"team": ' + "[" * 100_000 + "]" * 100_000 + "}", "valid JSON object"),
    ],
)
def test_parse_attributes_filter_rejects_invalid_input(raw, message):
    with pytest.raises(RunAttributesError, match=message):
        parse_attributes_filter(raw)


def test_parse_attributes_filter_returns_validated_object():
    assert parse_attributes_filter(f'{{"{WORK_ITEM_KEY}": "ABC-123"}}') == {WORK_ITEM_KEY: "ABC-123"}


def test_build_record_fields_sorts_keys_and_pairs():
    fields = build_record_fields({"team": "core", WORK_ITEM_KEY: "ABC-123"})

    assert fields == {
        "run_attribute_keys": [WORK_ITEM_KEY, "team"],
        "run_attribute_pairs": [f"{WORK_ITEM_KEY}=ABC-123", "team=core"],
    }


@pytest.mark.parametrize("attrs", [None, {}])
def test_build_record_fields_adds_nothing_without_attributes(attrs):
    assert build_record_fields(attrs) == {}


def test_attributes_from_pairs_inverts_build_record_fields():
    attrs = {WORK_ITEM_KEY: "ABC-123", "team": "a=b"}

    assert attributes_from_pairs(build_record_fields(attrs)["run_attribute_pairs"]) == attrs


@pytest.mark.parametrize("pairs", [None, [], "team=core", ["no-separator"]])
def test_attributes_from_pairs_returns_none_without_valid_pairs(pairs):
    assert attributes_from_pairs(pairs) is None


def test_record_fields_use_the_shared_pair_encoding():
    fields = build_record_fields({"team": "core"})

    assert fields["run_attribute_pairs"] == [encode_pair("team", "core")]
    assert encode_pair("team", "core").startswith(pair_prefix("team"))
    assert PAIRS_FIELD == "attributes.run_attribute_pairs.keyword"


def test_attributes_from_record_reads_the_pairs_field():
    attrs = {WORK_ITEM_KEY: "ABC-123"}

    assert attributes_from_record({"user_id": "u1", **build_record_fields(attrs)}) == attrs
    assert attributes_from_record({"user_id": "u1"}) is None
