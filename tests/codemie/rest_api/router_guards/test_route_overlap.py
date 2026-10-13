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

"""Unit tests for the Starlette path-pattern overlap matcher (EPMCDME-15651 step 0)."""

from __future__ import annotations

import pytest

from tests.codemie.rest_api.router_guards.route_overlap import OverlapPair, overlapping_pairs, paths_overlap


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        pytest.param("/c/users", "/c/{id}", True, id="literal-vs-param"),
        pytest.param("/c/users", "/c/{id}/x", False, id="param-cannot-span-a-slash"),
        pytest.param("/c/{x}", "/c/{y}/b", False, id="two-params-different-depth"),
        pytest.param("/c/folder/{f:path}", "/c/{id}/history/{i}", True, id="path-param-spans-slashes"),
        pytest.param("/c/users", "/c/admins", False, id="different-literals"),
        pytest.param("/c/{x}", "/c/{y}", True, id="two-params-same-depth"),
        pytest.param("/c/{x}", "/c/", False, id="param-needs-one-character"),
        pytest.param("/c/{p:path}", "/c/", True, id="path-param-may-be-empty"),
        pytest.param("/c/{x:int}", "/c/abc", True, id="other-converters-are-treated-as-plain-params"),
        pytest.param("/c/{x}.json", "/c/a.json", True, id="param-next-to-literal-text"),
        pytest.param("/c/{x}.json", "/c/a.xml", False, id="param-next-to-literal-text-mismatch"),
    ],
)
def test_paths_overlap(first, second, expected):
    assert paths_overlap(first, second) is expected
    assert paths_overlap(second, first) is expected


def _route(path: str, *methods: str) -> dict[str, object]:
    return {"path": path, "methods": list(methods)}


def test_get_and_post_on_one_path_are_not_paired():
    routes = [_route("/c/{id}", "GET"), _route("/c/{id}", "POST")]

    assert overlapping_pairs(routes) == []


def test_pair_keeps_input_order_even_when_the_generic_route_comes_first():
    routes = [_route("/c/{id}", "GET"), _route("/c/users", "GET")]

    assert overlapping_pairs(routes) == [OverlapPair("GET", "/c/{id}", "/c/users")]


def test_specific_route_first_is_paired_in_input_order():
    routes = [_route("/c/users", "GET"), _route("/c/{id}", "GET")]

    assert overlapping_pairs(routes) == [OverlapPair("GET", "/c/users", "/c/{id}")]


def test_exact_duplicate_method_and_path_collapses_to_its_first_occurrence():
    routes = [_route("/c/{id}", "GET"), _route("/c/{id}", "GET"), _route("/c/users", "GET")]

    assert overlapping_pairs(routes) == [OverlapPair("GET", "/c/{id}", "/c/users")]


def test_route_with_several_methods_pairs_per_method():
    routes = [_route("/c/users", "GET", "PUT"), _route("/c/{id}", "PUT", "DELETE")]

    assert overlapping_pairs(routes) == [OverlapPair("PUT", "/c/users", "/c/{id}")]


def test_pairs_are_listed_in_live_order_of_the_earlier_then_the_later_route():
    routes = [
        _route("/a/{x}", "GET"),
        _route("/b/{y}", "GET"),
        _route("/b/z", "GET"),
        _route("/a/q", "GET"),
    ]

    assert overlapping_pairs(routes) == [
        OverlapPair("GET", "/a/{x}", "/a/q"),
        OverlapPair("GET", "/b/{y}", "/b/z"),
    ]


def test_non_overlapping_routes_produce_no_pairs():
    routes = [_route("/a/b", "GET"), _route("/x/{y}", "GET"), _route("/x/{y}/z", "GET")]

    assert overlapping_pairs(routes) == []
