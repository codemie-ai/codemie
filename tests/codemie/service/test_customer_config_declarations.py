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

from codemie.service.customer_config_declarations import (
    DECLARATIONS,
    FieldDeclaration,
    FieldType,
    Markup,
    SettingDeclaration,
    by_component_id,
    by_key,
    build_key,
)
from codemie.service.dynamic_config_service import DynamicConfigService


@pytest.mark.parametrize(
    "component_id, expected_key",
    [
        ("chatDisclaimer", "CUSTOMER_CONFIG__CHAT_DISCLAIMER"),
        ("features:webSearch", "CUSTOMER_CONFIG__FEATURES__WEB_SEARCH"),
        ("bannerMessage", "CUSTOMER_CONFIG__BANNER_MESSAGE"),
        ("applications:test-mate", "CUSTOMER_CONFIG__APPLICATIONS__TEST_MATE"),
    ],
)
def test_key_is_derived_from_component_id(component_id, expected_key):
    assert build_key(component_id) == expected_key


@pytest.mark.parametrize(
    "component_id",
    ["chatDisclaimer", "features:webSearch", "applications:test-mate", "bannerLinkRoute"],
)
def test_derived_key_satisfies_dynamic_config_key_pattern(component_id):
    assert DynamicConfigService.KEY_PATTERN.match(build_key(component_id))


def test_declaration_exposes_its_derived_key():
    declaration = SettingDeclaration(
        component_id="features:webSearch",
        label="Web search",
        fields=[FieldDeclaration(name="enabled", type=FieldType.SWITCH, label="Enabled")],
    )

    assert declaration.key == "CUSTOMER_CONFIG__FEATURES__WEB_SEARCH"


def test_chat_disclaimer_is_declared():
    declaration = by_component_id("chatDisclaimer")

    assert declaration is not None
    field_names = [field.name for field in declaration.fields]
    assert field_names == ["enabled", "text"]


def test_chat_disclaimer_text_field_carries_markdown_markup():
    declaration = by_component_id("chatDisclaimer")
    text_field = next(field for field in declaration.fields if field.name == "text")

    assert text_field.type is FieldType.TEXTAREA
    assert text_field.markup is Markup.MARKDOWN
    assert text_field.max_length is not None


def test_lookup_by_key_matches_lookup_by_component_id():
    declaration = by_component_id("chatDisclaimer")

    assert by_key(declaration.key) is declaration


def test_undeclared_component_is_not_resolvable():
    assert by_component_id("features:dynamicCodeInterpreter") is None
    assert by_key("CUSTOMER_CONFIG__FEATURES__DYNAMIC_CODE_INTERPRETER") is None


def test_every_declaration_has_a_unique_key():
    keys = [declaration.key for declaration in DECLARATIONS]

    assert len(keys) == len(set(keys))


def test_release_notes_recent_count_is_declared():
    declaration = by_component_id("releaseNotesRecentCount")

    assert declaration is not None
    field_names = [field.name for field in declaration.fields]
    assert field_names == ["recentReleaseCount"]
    assert declaration.key == "CUSTOMER_CONFIG__RELEASE_NOTES_RECENT_COUNT"


def test_release_notes_recent_count_field_is_a_digit_only_input():
    declaration = by_component_id("releaseNotesRecentCount")
    count_field = next(field for field in declaration.fields if field.name == "recentReleaseCount")

    assert count_field.type is FieldType.INPUT
    assert count_field.required is False
    assert count_field.pattern == r"^[1-9][0-9]*$"
