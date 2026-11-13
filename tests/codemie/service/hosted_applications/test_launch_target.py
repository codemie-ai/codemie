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

"""The launch path a CodeMie window may ask for: a local target the content gateway would serve, nothing else."""

from types import SimpleNamespace

import pytest

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications import paths
from codemie.service.hosted_applications import launch
from codemie.service.hosted_applications.launch import HostedApplicationLaunchService, launch_target


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (None, "/"),
        ("/", "/"),
        ("/index.html", "/index.html"),
        ("/?presenter=1", "/?presenter=1"),
        ("/slides/deck.html?presenter=1#/3", "/slides/deck.html?presenter=1#/3"),
        ("/docs/", "/docs/"),
        ("/%E2%9C%93.html", "/%E2%9C%93.html"),
        ("/print?a=%2F&b=..#a#b", "/print?a=%2F&b=..#a#b"),
        ("/?" + "a" * 4094, "/?" + "a" * 4094),
    ],
)
def test_a_local_target_is_kept_exactly_as_sent(path, expected):
    assert launch_target(path) == expected


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("", id="empty"),
        pytest.param("index.html", id="relative"),
        pytest.param("https://evil.example/", id="absolute-url"),
        pytest.param("//evil.example/x", id="protocol-relative"),
        pytest.param("/_platform/view", id="platform"),
        pytest.param("/_PLATFORM/launch", id="platform-case"),
        pytest.param("/_platform", id="platform-bare"),
        pytest.param("/%5Fplatform/view", id="platform-encoded"),
        pytest.param("/a/../b", id="dot-dot"),
        pytest.param("/..", id="dot-dot-root"),
        pytest.param("/%2e%2e/secret", id="dot-dot-encoded"),
        pytest.param("/./a", id="dot"),
        pytest.param("/a//b", id="empty-segment"),
        pytest.param("/a%2Fb", id="encoded-slash"),
        pytest.param("/a%2fb", id="encoded-slash-lower"),
        pytest.param("/a%5Cb", id="encoded-backslash"),
        pytest.param("/a%00", id="encoded-nul"),
        pytest.param("/a%zz", id="bad-escape"),
        pytest.param("/a\\b", id="backslash"),
        pytest.param("/a b", id="space"),
        pytest.param("/?a b", id="space-in-query"),
        pytest.param("/#a b", id="space-in-hash"),
        pytest.param("/a\tb", id="tab"),
        pytest.param("/a\nb", id="newline"),
        pytest.param("/a\x00", id="nul"),
        pytest.param("/a\x7f", id="delete"),
        pytest.param("/é", id="non-ascii"),
        pytest.param("/" + "a" * 4096, id="too-long"),
        pytest.param("/?" + "a" * 4095, id="too-long-with-query"),
        pytest.param("/" + "a" * 241, id="path-longer-than-a-stored-file"),
    ],
)
def test_anything_else_is_an_invalid_launch(path):
    with pytest.raises(HostedApplicationError) as raised:
        launch_target(path)
    assert (raised.value.code, raised.value.status) == ("INVALID_LAUNCH", 422)


@pytest.mark.parametrize("path", [5, b"/", ["/"]])
def test_a_non_string_path_is_an_invalid_launch(path):
    with pytest.raises(HostedApplicationError) as raised:
        launch_target(path)
    assert raised.value.code == "INVALID_LAUNCH"


@pytest.mark.asyncio
async def test_issue_refuses_an_invalid_path_before_it_opens_a_transaction():
    repository = SimpleNamespace(transaction=None)
    service = HostedApplicationLaunchService(SimpleNamespace(repository=repository), SimpleNamespace())
    with pytest.raises(HostedApplicationError) as raised:
        await service.issue(object(), "app", purpose="published_view", path="/_platform/view")
    assert raised.value.code == "INVALID_LAUNCH"


def test_launch_and_gateway_share_one_local_target_rule(monkeypatch):
    seen = []

    def spy(raw, query):
        seen.append((raw, query))
        return paths.local_target(raw, query)

    monkeypatch.setattr(launch, "local_target", spy)
    assert launch_target("/deck.html?presenter=1#/3") == "/deck.html?presenter=1#/3"
    assert seen == [(b"/deck.html", b"presenter=1")]


def test_the_limit_covers_path_query_and_fragment_together():
    assert len(launch_target("/?" + "a" * 2000 + "#" + "b" * 2093)) == 4096
    with pytest.raises(HostedApplicationError):
        launch_target("/?" + "a" * 2000 + "#" + "b" * 2094)
    assert "path, query and fragment together" in " ".join(launch_target.__doc__.split())
