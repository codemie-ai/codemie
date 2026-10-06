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

import copy
import dataclasses
import pickle

import pytest
from unittest.mock import patch

from codemie_tools.base.http_result import HttpResult
from codemie_tools.base.script_result import ScriptResult, ScriptResultSource, ScriptResultTooLarge


def _legacy_spaced(method: str, url: str, status: int, reason: str | None, body: str) -> str:
    return f"HTTP: {method} {url} -> {status} {reason} {body}"


def _legacy_compact(method: str, url: str, status: int, reason: str | None, body: str) -> str:
    return f"HTTP: {method}{url} -> {status}{reason}{body}"


def _legacy_body_on_new_line(method: str, url: str, status: int, reason: str | None, body: str) -> str:
    return f"HTTP: {method} {url} -> {status} {reason}\n{body}"


@pytest.mark.parametrize("reason", ["OK", None])
@pytest.mark.parametrize(
    ("layout", "legacy"),
    [
        ("spaced", _legacy_spaced),
        ("compact", _legacy_compact),
        ("body_on_new_line", _legacy_body_on_new_line),
    ],
)
def test_layout_matches_legacy_fstring(layout: str, legacy, reason: str | None) -> None:
    result = HttpResult("GET", "/rest/api/2/issue", 200, reason, '{"a": 1}', layout)

    assert str(result) == legacy("GET", "/rest/api/2/issue", 200, reason, '{"a": 1}')
    assert result == legacy("GET", "/rest/api/2/issue", 200, reason, '{"a": 1}')


def test_attributes_are_kept() -> None:
    result = HttpResult("POST", "/x", 404, "Not Found", "nope", "spaced")

    assert (result.method, result.url, result.status, result.reason, result.body, result.layout) == (
        "POST",
        "/x",
        404,
        "Not Found",
        "nope",
        "spaced",
    )
    assert isinstance(result, str)


def test_explicit_rendered_overrides_layout() -> None:
    result = HttpResult("GET", "/x", 200, "OK", "b", "spaced", rendered="custom text")

    assert str(result) == "custom text"
    assert result.body == "b"


def test_with_suffix_changes_only_the_string() -> None:
    original = HttpResult("GET", "/x", 200, "OK", "b", "spaced")

    suffixed = original.with_suffix("\n\nNOTE")

    assert isinstance(suffixed, HttpResult)
    assert str(suffixed) == str(original) + "\n\nNOTE"
    assert (suffixed.method, suffixed.url, suffixed.status, suffixed.reason, suffixed.body, suffixed.layout) == (
        "GET",
        "/x",
        200,
        "OK",
        "b",
        "spaced",
    )
    assert str(original) == _legacy_spaced("GET", "/x", 200, "OK", "b")


def test_fields_survive_copy_and_pickle() -> None:
    original = HttpResult("GET", "/x", 201, None, "b", "compact").with_suffix("!")

    for clone in (copy.copy(original), copy.deepcopy(original), pickle.loads(pickle.dumps(original))):
        assert isinstance(clone, HttpResult)
        assert str(clone) == str(original)
        assert (clone.status, clone.reason, clone.body, clone.layout) == (201, None, "b", "compact")


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('{"a": [1, 2], "b": null}', {"a": [1, 2], "b": None}),
        ('[1, "two", {"three": 3}]', [1, "two", {"three": 3}]),
        ('  {"padded": true}  ', {"padded": True}),
    ],
)
def test_to_script_result_parses_json_containers(body: str, expected: object) -> None:
    assert HttpResult("GET", "/x", 200, "OK", body, "spaced").to_script_result().result == expected


@pytest.mark.parametrize("body", ["plain text", "123", '"quoted"', "{not json", "[broken", "", "true"])
def test_to_script_result_keeps_non_json_as_string(body: str) -> None:
    assert HttpResult("GET", "/x", 200, "OK", body, "spaced").to_script_result().result == body


def test_http_result_is_a_script_result_source() -> None:
    assert isinstance(HttpResult("GET", "/x", 200, "OK", "b", "spaced"), ScriptResultSource)
    assert not isinstance(object(), ScriptResultSource)


def test_to_script_result_carries_the_http_status_and_reason() -> None:
    result = HttpResult("GET", "/x", 404, "Not Found", '{"e": 1}', "spaced").to_script_result()

    assert result == ScriptResult(result={"e": 1}, http_status=404, http_reason="Not Found")


def test_a_body_longer_than_the_cap_is_refused_before_it_is_parsed() -> None:
    body = '{"data": "' + "x" * 200 + '"}'
    http = HttpResult("GET", "/x", 200, "OK", body, "spaced")

    with patch("codemie_tools.base.http_result.json.loads") as parse, pytest.raises(ScriptResultTooLarge):
        http.to_script_result(max_bytes=100)

    parse.assert_not_called()


def test_a_body_within_the_cap_is_parsed_as_usual() -> None:
    http = HttpResult("GET", "/x", 200, "OK", '{"a": 1}', "spaced")

    assert http.to_script_result(max_bytes=100).result == {"a": 1}


def test_a_body_exactly_at_the_cap_is_not_refused() -> None:
    http = HttpResult("GET", "/x", 200, "OK", "x" * 100, "spaced")

    assert http.to_script_result(max_bytes=100).result == "x" * 100


def test_script_result_defaults_and_frozen() -> None:
    result = ScriptResult(result={"a": 1})

    assert result.http_status is None
    assert result.http_reason is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.result = "other"  # type: ignore[misc]
