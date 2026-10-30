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
"""Tests for the transport-independent write input validation."""

import pytest

from codemie_tools.ms_exchange.errors import InvalidLocalInputError, NotAppliedError
from codemie_tools.ms_exchange.write_validation import (
    MAX_CATEGORY_LENGTH,
    _MAX_ADDRESS_LENGTH,
    validate_body_type,
    validate_category,
    validate_folder_name,
    validate_message_id,
    validate_recipients,
    validate_send_input,
)


def test_recipients_are_returned_as_received_and_in_order():
    assert validate_recipients(["a@example.test", "b.c+d@sub.example.test"]) == [
        "a@example.test",
        "b.c+d@sub.example.test",
    ]


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        "a@example.test",  # a bare string is not a list of addresses
        [""],
        ["   "],
        ["no-at-sign"],
        ["@example.test"],
        ["a@"],
        ["a@@example.test"],
        ["a b@example.test"],
        ["Name <a@example.test>"],
        ["a@example.test,b@example.test"],
        ["a@example..test"],
        ["a@.example.test"],
        ["a@example.test\r\nBcc: x@example.test"],
        ["a@example.test", 5],
        ["a@example.test", "bad"],
        [" a@example.test"],  # padded values are rejected, never repaired
        ["a@example.test "],
        ["a@example.test", "\tb@example.test"],
    ],
)
def test_invalid_recipients_are_not_applied_input_errors(bad):
    with pytest.raises(InvalidLocalInputError) as info:
        validate_recipients(bad)

    assert isinstance(info.value, NotAppliedError)
    assert str(info.value).startswith("Not applied.")


def test_address_of_exactly_the_maximum_length_is_accepted():
    local = "a"
    domain = "d" * (_MAX_ADDRESS_LENGTH - len(local) - 1)  # "-1" for the "@"
    address = f"{local}@{domain}"
    assert len(address) == _MAX_ADDRESS_LENGTH

    assert validate_recipients([address]) == [address]


def test_address_one_char_over_the_maximum_length_is_rejected():
    local = "a"
    domain = "d" * (_MAX_ADDRESS_LENGTH - len(local))  # one char longer than the accepted case above
    address = f"{local}@{domain}"
    assert len(address) == _MAX_ADDRESS_LENGTH + 1

    with pytest.raises(InvalidLocalInputError):
        validate_recipients([address])


def test_recipient_with_a_non_ascii_local_part_is_accepted():
    """CURRENT BEHAVIOUR, not asserted as correct: ``_LOCAL_PART`` excludes only whitespace and a fixed
    punctuation set, not non-ASCII characters, so an internationalised local part (not valid under plain
    RFC 5321, which needs the SMTPUTF8 extension) passes through unchanged. Worth revisiting with the
    actual transports' behaviour before relying on it; not changed here (tests-only task)."""
    address = "héllo@example.test"

    assert validate_recipients([address]) == [address]


def test_recipient_with_a_raw_unicode_idn_domain_is_rejected():
    """``_DOMAIN`` only matches ASCII letters, digits and hyphens, so a domain must already be
    punycode-encoded (see the accepted case below); a raw Unicode domain label is rejected."""
    with pytest.raises(InvalidLocalInputError):
        validate_recipients(["a@münchen.de"])


def test_recipient_with_a_punycode_idn_domain_is_accepted():
    domain = "münchen.de".encode("idna").decode("ascii")
    address = f"a@{domain}"
    assert address == "a@xn--mnchen-3ya.de"

    assert validate_recipients([address]) == [address]


@pytest.mark.parametrize("value", ["text", "html"])
def test_body_type_accepts_exactly_the_allowed_values(value):
    assert validate_body_type(value) == value


@pytest.mark.parametrize("bad", [None, "", "markdown", "rtf", 1, "html5", "HTML", "Text", " text", "html "])
def test_invalid_body_type_is_an_input_error(bad):
    with pytest.raises(InvalidLocalInputError):
        validate_body_type(bad)


@pytest.mark.parametrize("bad", [None, "", "   ", 5, " AAMk=/id", "AAMk=/id "])
def test_message_id_must_be_a_non_blank_string(bad):
    with pytest.raises(InvalidLocalInputError):
        validate_message_id(bad)


def test_message_id_is_returned_unchanged_when_valid():
    assert validate_message_id("AAMk=/id") == "AAMk=/id"


def test_send_input_validates_everything_together():
    assert validate_send_input(["a@example.test"], "Subj", "Body", "html") == (["a@example.test"], "html")


@pytest.mark.parametrize("subject, body", [(None, "b"), ("s", None), (1, "b"), ("s", 2)])
def test_send_input_needs_string_subject_and_body(subject, body):
    with pytest.raises(InvalidLocalInputError):
        validate_send_input(["a@example.test"], subject, body, "text")


def test_empty_subject_and_body_are_allowed():
    assert validate_send_input(["a@example.test"], "", "", "text") == (["a@example.test"], "text")


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "",
        "   ",
        5,
        "Fin\nance",
        "Fin\x00ance",
        "tab\there",
        "x" * (MAX_CATEGORY_LENGTH + 1),
        " Needs Review",
        "Needs Review ",
    ],
)
def test_category_must_be_a_short_printable_non_blank_string(bad):
    with pytest.raises(InvalidLocalInputError) as info:
        validate_category(bad)

    assert isinstance(info.value, NotAppliedError)


def test_category_is_returned_as_received_with_its_case_and_inner_spaces():
    assert validate_category("Needs Review") == "Needs Review"
    assert validate_category("x" * MAX_CATEGORY_LENGTH) == "x" * MAX_CATEGORY_LENGTH


@pytest.mark.parametrize("bad", [None, "", "  ", 3, ["Inbox"], " Archive", "Archive "])
def test_folder_name_must_be_a_non_blank_string(bad):
    with pytest.raises(InvalidLocalInputError):
        validate_folder_name(bad)


def test_folder_name_is_returned_as_received():
    assert validate_folder_name("Archive") == "Archive"
