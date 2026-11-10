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

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.protocol import (
    check_etag,
    decode_cursor,
    encode_cursor,
    etag_matches,
    parse_range,
    parse_session_cookies,
)


def test_etag_preconditions_require_exact_target_revision():
    with pytest.raises(HostedApplicationError) as exc:
        check_etag(None, "app", "id", 1)
    assert exc.value.status == 428
    for value in ('*', 'W/"app:id:1"', '"app:other:1"', '"app:id:2"'):
        with pytest.raises(HostedApplicationError) as exc:
            check_etag(value, "app", "id", 1)
        assert exc.value.status == 409
    check_etag('"app:id:1"', "app", "id", 1)


def test_cursor_is_bound_to_actor_query_and_signature():
    scope = {"actor": "owner", "q": "description", "scope": "mine"}
    position = {"id": "a", "sort": "b"}
    cursor = encode_cursor(scope, position, b"s" * 32)
    assert decode_cursor(cursor, scope, b"s" * 32) == position
    for token, query, secret in (
        (cursor, {**scope, "actor": "viewer"}, b"s" * 32),
        (cursor[:-1] + "0", scope, b"s" * 32),
        (cursor, scope, b"t" * 32),
    ):
        with pytest.raises(HostedApplicationError):
            decode_cursor(token, query, secret)


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, (0, 9, False)),
        ("bytes=0-3", (0, 3, True)),
        ("bytes=3-", (3, 9, True)),
        ("bytes=-4", (6, 9, True)),
        ("bytes=0-500", (0, 9, True)),
    ],
)
def test_single_ranges_are_bounded(value, expected):
    assert parse_range(value, 10) == expected


@pytest.mark.parametrize(
    "value",
    [
        "bytes=10-",
        "bytes=-0",
        "bytes=4-2",
        "bytes=0-1,4-5",
        "bytes=abc",
        "items=0-1",
        "bytes=999999999999999999999999999999-",
    ],
)
def test_unsatisfiable_or_amplifying_range_rejected(value):
    with pytest.raises(HostedApplicationError) as exc:
        parse_range(value, 10)
    assert exc.value.status == 416


SESSION = "__Host-codemie-app-session"
EMBEDDED = "__Host-codemie-app-embedded"
NAMES = (EMBEDDED, SESSION)
REAL, PLANTED = "r" * 43, "p" * 43


@pytest.mark.parametrize(
    "header",
    [
        # One app sets `x=1 <platform cookie>=<token>` with Domain=<content domain>; the space-separated
        # value must stay part of the foreign cookie, whichever order the browser sends the pairs in.
        f"{EMBEDDED}={REAL}; x=1 {EMBEDDED}={PLANTED}",
        f"x=1 {EMBEDDED}={PLANTED}; {EMBEDDED}={REAL}",
        f"x {EMBEDDED}={PLANTED}; {EMBEDDED}={REAL}",
        f'x="{EMBEDDED}={PLANTED}"; {EMBEDDED}={REAL}',
        f"{EMBEDDED}={REAL};x={EMBEDDED}={PLANTED}",
        f"\t{EMBEDDED}={REAL} ;  other=1;;",
    ],
)
def test_planted_foreign_cookie_cannot_supply_a_platform_session(header):
    assert parse_session_cookies(header, NAMES) == {EMBEDDED: REAL}


@pytest.mark.parametrize(
    "header",
    [
        f"{SESSION}={REAL}; {SESSION}={PLANTED}",
        f"{SESSION}={REAL}; {SESSION}={REAL}",
        f"{SESSION}={REAL[:-1]}",
        f"{SESSION}={REAL}x",
        f'{SESSION}="{REAL}"',
        f"{SESSION}= {REAL[:-1]}",
        f"{SESSION}={REAL[:-1]}+",
        f"{SESSION}",
        f"{SESSION}={REAL}\n",
    ],
)
def test_ambiguous_or_malformed_platform_cookies_are_rejected(header):
    with pytest.raises(ValueError):
        parse_session_cookies(header, NAMES)


def test_a_cookie_flood_from_a_sibling_app_cannot_lock_the_viewer_out():
    """CR-005: any app can set many Domain cookies on the shared content domain; the session must still be read."""
    flood = "; ".join(f"junk{index}=" + "x" * 100 for index in range(200))
    header = f"{flood}; {SESSION}={REAL}"
    assert len(header) > 16 * 1024
    assert parse_session_cookies(header, NAMES) == {SESSION: REAL}


def test_unknown_cookies_are_ignored_and_absent_platform_cookies_are_omitted():
    header = f"theme=dark; {SESSION.lower()}={PLANTED}; {SESSION} ={PLANTED}; unknown=x y z; =nameless"
    assert parse_session_cookies(header, NAMES) == {}
    assert parse_session_cookies("", NAMES) == {}
    assert parse_session_cookies(f"{EMBEDDED}={REAL}; {SESSION}={PLANTED}", NAMES) == {
        EMBEDDED: REAL,
        SESSION: PLANTED,
    }


@pytest.mark.parametrize(
    "header, expected",
    [
        (None, False),
        ("", False),
        ('"abc"', True),
        ('W/"abc"', True),
        ('"other", "abc"', True),
        (' "other" ,W/"abc" ', True),
        ("*", True),
        ('"abcd"', False),
        ("abc", False),
        ('"other"', False),
    ],
)
def test_if_none_match_uses_weak_comparison_over_lists(header, expected):
    assert etag_matches(header, '"abc"') is expected
