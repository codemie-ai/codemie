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

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.customer_config_declarations import (
    BANNER,
    CHAT_DISCLAIMER,
    RELEASE_NOTES_RECENT_COUNT,
    FieldDeclaration,
    FieldType,
    SettingDeclaration,
)
from codemie.service.customer_config_service import validate_and_sanitize


def test_accepts_a_valid_payload():
    result = validate_and_sanitize(CHAT_DISCLAIMER, {"enabled": True, "text": "Mind the **gap**"})

    assert result == {"enabled": True, "text": "Mind the **gap**"}


def test_rejects_an_undeclared_field():
    with pytest.raises(ExtendedHTTPException) as error:
        validate_and_sanitize(CHAT_DISCLAIMER, {"enabled": True, "text": "hi", "name": "sneaky"})

    assert error.value.code == 400
    assert "name" in str(error.value.details)


def test_rejects_a_wrong_type_for_a_switch():
    with pytest.raises(ExtendedHTTPException) as error:
        validate_and_sanitize(CHAT_DISCLAIMER, {"enabled": "yes", "text": "hi"})

    assert error.value.code == 400


def test_rejects_a_wrong_type_for_a_text_field():
    with pytest.raises(ExtendedHTTPException) as error:
        validate_and_sanitize(CHAT_DISCLAIMER, {"enabled": True, "text": 42})

    assert error.value.code == 400


def test_rejects_a_missing_declared_field():
    with pytest.raises(ExtendedHTTPException) as error:
        validate_and_sanitize(CHAT_DISCLAIMER, {"enabled": True})

    assert error.value.code == 400
    assert "text" in str(error.value.details)


def test_rejects_an_over_length_value():
    over_length = "x" * (CHAT_DISCLAIMER.fields[1].max_length + 1)

    with pytest.raises(ExtendedHTTPException) as error:
        validate_and_sanitize(CHAT_DISCLAIMER, {"enabled": True, "text": over_length})

    assert error.value.code == 400


def test_strips_html_from_a_markdown_field():
    result = validate_and_sanitize(
        CHAT_DISCLAIMER,
        {"enabled": True, "text": "Careful <script>alert(1)</script> now"},
    )

    assert "<script>" not in result["text"]
    assert "alert(1)" not in result["text"]


def test_preserves_markdown_syntax():
    text = "See [the policy](https://example.com) and **read** it"

    result = validate_and_sanitize(CHAT_DISCLAIMER, {"enabled": True, "text": text})

    assert result["text"] == text


@pytest.mark.parametrize(
    "text",
    [
        "[click](javascript:alert(1))",
        "[click](JavaScript:alert(1))",
        "[click](  javascript:alert(1))",
        "[click](data:text/html;base64,PHN2Zz4=)",
    ],
)
def test_rejects_dangerous_link_targets(text):
    with pytest.raises(ExtendedHTTPException) as error:
        validate_and_sanitize(CHAT_DISCLAIMER, {"enabled": True, "text": text})

    assert error.value.code == 400


def test_allows_an_empty_optional_text():
    result = validate_and_sanitize(CHAT_DISCLAIMER, {"enabled": False, "text": ""})

    assert result["text"] == ""


# --- Regressions found in code review (CR-002, CR-012) ---


@pytest.mark.parametrize(
    "text",
    [
        "[x](java&#115;cript:alert(1))",
        "[x](&#x6a;avascript:alert(1))",
        "[x](&#106;avascript:alert(1))",
        "[x](&#100;ata:text/html;base64,PHN2Zz4=)",
    ],
)
def test_rejects_dangerous_link_targets_hidden_behind_character_references(text):
    """A browser decodes character references inside an href, so the raw spelling is not enough."""
    with pytest.raises(ExtendedHTTPException) as error:
        validate_and_sanitize(CHAT_DISCLAIMER, {"enabled": True, "text": text})

    assert error.value.code == 400


@pytest.mark.parametrize(
    "text",
    [
        "AT&T policy applies",
        "Use < 100 tokens",
        "Compare a > b",
        "Terms & conditions & more",
    ],
)
def test_stores_the_authors_text_without_html_escaping(text):
    """The stored value is Markdown source; escaping it would surface as AT&amp;T in the UI."""
    result = validate_and_sanitize(CHAT_DISCLAIMER, {"enabled": True, "text": text})

    assert result["text"] == text


def test_still_strips_html_tags_after_unescaping():
    result = validate_and_sanitize(
        CHAT_DISCLAIMER,
        {"enabled": True, "text": "Careful <b>now</b> and <script>alert(1)</script>"},
    )

    assert "<b>" not in result["text"]
    assert "alert(1)" not in result["text"]
    assert "Careful now" in result["text"]


def test_rejects_a_value_failing_the_declared_pattern():
    declaration = SettingDeclaration(
        component_id="userGuide",
        label="User guide",
        fields=[
            FieldDeclaration(
                name="url",
                type=FieldType.INPUT,
                label="URL",
                pattern=r"^https://",
                pattern_message="URL must start with https://",
            )
        ],
    )

    with pytest.raises(ExtendedHTTPException) as error:
        validate_and_sanitize(declaration, {"url": "http://example.com"})

    assert error.value.code == 400
    assert "https://" in str(error.value.details)


def test_accepts_a_value_matching_the_declared_pattern():
    declaration = SettingDeclaration(
        component_id="userGuide",
        label="User guide",
        fields=[FieldDeclaration(name="url", type=FieldType.INPUT, label="URL", pattern=r"^https://")],
    )

    result = validate_and_sanitize(declaration, {"url": "https://example.com"})

    assert result["url"] == "https://example.com"


def test_an_empty_optional_value_skips_the_pattern_check():
    declaration = SettingDeclaration(
        component_id="userGuide",
        label="User guide",
        fields=[FieldDeclaration(name="url", type=FieldType.INPUT, label="URL", pattern=r"^https://")],
    )

    assert validate_and_sanitize(declaration, {"url": ""})["url"] == ""


def _banner_payload(**overrides) -> dict:
    payload = {"enabled": True, "message": "Scheduled maintenance", "linkLabel": "", "linkRoute": ""}
    payload.update(overrides)
    return payload


def test_banner_accepts_a_relative_link_route():
    result = validate_and_sanitize(BANNER, _banner_payload(linkLabel="Details", linkRoute="/settings/profile"))

    assert result["linkRoute"] == "/settings/profile"


@pytest.mark.parametrize(
    "link_route",
    ["https://docs.example.com/policy", "http://intranet.example/page", "/settings/profile", "//example.com"],
)
def test_banner_accepts_any_link_route_without_a_scripting_scheme(link_route):
    result = validate_and_sanitize(BANNER, _banner_payload(linkLabel="Details", linkRoute=link_route))

    assert result["linkRoute"] == link_route


@pytest.mark.parametrize(
    "link_route",
    [
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "  javascript:alert(1)",
        "data:text/html;base64,PHN2Zz4=",
        "vbscript:msgbox(1)",
        "java&#115;cript:alert(1)",
        "&#x6a;avascript:alert(1)",
        "java\tscript:alert(1)",
    ],
)
def test_banner_rejects_a_link_route_carrying_a_scripting_scheme(link_route):
    with pytest.raises(ExtendedHTTPException) as error:
        validate_and_sanitize(BANNER, _banner_payload(linkLabel="Details", linkRoute=link_route))

    assert error.value.code == 400


def test_banner_rejects_a_message_over_its_limit():
    with pytest.raises(ExtendedHTTPException) as error:
        validate_and_sanitize(BANNER, _banner_payload(message="x" * 1001))

    assert error.value.code == 400


def test_a_scripting_scheme_is_rejected_in_any_declared_text_field():
    """The check belongs to text validation, not to one declaration's pattern."""
    with pytest.raises(ExtendedHTTPException) as error:
        validate_and_sanitize(BANNER, _banner_payload(message="javascript:alert(1)"))

    assert error.value.code == 400


@pytest.mark.parametrize("value", ["0", "01", "1.5", "-1", "abc", "5 "])
def test_rejects_a_non_positive_integer_recent_release_count(value):
    with pytest.raises(ExtendedHTTPException) as error:
        validate_and_sanitize(RELEASE_NOTES_RECENT_COUNT, {"recentReleaseCount": value})

    assert error.value.code == 400
    assert "positive integer" in str(error.value.details)


@pytest.mark.parametrize("value", ["1", "10", "999"])
def test_accepts_a_positive_integer_recent_release_count(value):
    result = validate_and_sanitize(RELEASE_NOTES_RECENT_COUNT, {"recentReleaseCount": value})

    assert result == {"recentReleaseCount": value}


def test_recent_release_count_is_optional_and_empty_skips_the_pattern():
    result = validate_and_sanitize(RELEASE_NOTES_RECENT_COUNT, {"recentReleaseCount": ""})

    assert result == {"recentReleaseCount": ""}


def test_rejects_a_recent_release_count_longer_than_its_max_length():
    with pytest.raises(ExtendedHTTPException) as error:
        validate_and_sanitize(RELEASE_NOTES_RECENT_COUNT, {"recentReleaseCount": "99999"})

    assert error.value.code == 400
    assert "exceeds its limit" in str(error.value.details)
