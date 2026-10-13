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

"""Route precedence guard (EPMCDME-15651 step 0).

Starlette serves the first registered route that matches, so two routes of one method whose paths can
match the same URL are order-dependent. ``baselines/route_precedence.json`` lists every such pair of the
guarded app built from base commit ``e55bea3005``, earlier route first. Pairs where the generic route
already comes first are recorded as they are, not fixed.

The guard fails when a baseline pair swaps order or one of its routes disappears. Overlapping pairs that
are not in the baseline are reported as warnings only. Regenerate the baseline manually after an
intended, reviewed change (see ``regenerate_baselines``).
"""

from __future__ import annotations

import warnings
from collections.abc import Iterable, Mapping
from typing import Any

from tests.codemie.rest_api.router_guards.baseline import ROUTE_PRECEDENCE_BASELINE, load_baseline
from tests.codemie.rest_api.router_guards.guarded_app import load_guarded_app_dump
from tests.codemie.rest_api.router_guards.route_overlap import OverlapPair, overlapping_pairs

_API_PREFIX = "/v1"

# Pairs reproduced by hand in technical-analysis.md (section 2, "Route-order pairs"): (method, earlier, later).
_ASSISTANT_SLUG_ROUTE = "/assistants/slug/{assistant_slug:path}"
_ASSISTANT_VERSIONS = "/assistants/{assistant_id}/versions"
_CONVERSATION_FOLDER = "/conversations/folder/{folder:path}"
_CONVERSATION_HISTORY = "/conversations/{conversation_id}/history"
_CONVERSATION_HISTORY_ITEM = "/conversations/{conversation_id}/history/{history_index}"
_HAND_REPRODUCED_PAIRS = [
    ("POST", "/assistants/virtual/model", "/assistants/{assistant_id}/model"),
    ("GET", "/assistants/id/{assistant_id}", _ASSISTANT_VERSIONS),
    ("GET", "/assistants/prebuilt/{slug}", _ASSISTANT_VERSIONS),
    ("GET", "/assistants/metrics/{conversation_id}", _ASSISTANT_VERSIONS),
    ("GET", _ASSISTANT_SLUG_ROUTE, _ASSISTANT_VERSIONS),
    ("GET", _ASSISTANT_SLUG_ROUTE, "/assistants/{assistant_id}/versions/{version_number}"),
    ("GET", _ASSISTANT_SLUG_ROUTE, "/assistants/{assistant_id}/versions/{version1}/compare/{version2}"),
    ("GET", "/index/users", "/index/{index_id}"),
    ("GET", "/index/find_id", "/index/{index_id}"),
    ("GET", "/conversations/search", "/conversations/{conversation_id}"),
    ("GET", "/conversations/new", "/conversations/{conversation_id}"),
    ("PUT", _CONVERSATION_HISTORY, _CONVERSATION_FOLDER),
    ("PUT", _CONVERSATION_HISTORY_ITEM, _CONVERSATION_FOLDER),
    ("DELETE", _CONVERSATION_HISTORY, _CONVERSATION_FOLDER),
    ("DELETE", _CONVERSATION_HISTORY_ITEM, _CONVERSATION_FOLDER),
    ("PUT", _CONVERSATION_FOLDER, "/conversations/{conversation_id}/messages/{history_index}/output"),
]


def _first_positions(routes: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], int]:
    positions: dict[tuple[str, str], int] = {}
    for index, route in enumerate(routes):
        for method in route["methods"]:
            positions.setdefault((method, route["path"]), index)
    return positions


def _order_violations(baseline: Iterable[OverlapPair], routes: Iterable[Mapping[str, Any]]) -> list[str]:
    positions = _first_positions(routes)
    violations = []
    for pair in baseline:
        absent = [path for path in (pair.earlier, pair.later) if (pair.method, path) not in positions]
        if absent:
            violations.extend(f"baseline route missing: {pair.method} {path}" for path in absent)
        elif positions[(pair.method, pair.earlier)] > positions[(pair.method, pair.later)]:
            violations.append(f"order changed: {pair.method} {pair.earlier} must come before {pair.later}")
    return violations


def _new_pairs(baseline: Iterable[OverlapPair], routes: Iterable[Mapping[str, Any]]) -> list[OverlapPair]:
    known = {(pair.method, frozenset((pair.earlier, pair.later))) for pair in baseline}
    return [
        pair for pair in overlapping_pairs(routes) if (pair.method, frozenset((pair.earlier, pair.later))) not in known
    ]


def _baseline_pairs() -> list[OverlapPair]:
    return [OverlapPair(**entry) for entry in load_baseline(ROUTE_PRECEDENCE_BASELINE)]


def _route(path: str, *methods: str) -> dict[str, object]:
    return {"path": path, "methods": list(methods)}


def test_baseline_order_is_preserved():
    violations = _order_violations(_baseline_pairs(), load_guarded_app_dump()["routes"])

    assert not violations, "route precedence changed:\n" + "\n".join(violations)


def test_new_overlapping_pairs_are_reported_but_never_fail():
    new_pairs = _new_pairs(_baseline_pairs(), load_guarded_app_dump()["routes"])

    for pair in new_pairs:
        warnings.warn(
            f"new overlapping route pair not in the baseline: {pair.method} {pair.earlier} before {pair.later}",
            stacklevel=1,
        )


def test_baseline_contains_hand_reproduced_pairs():
    recorded = {(pair.method, pair.earlier, pair.later) for pair in _baseline_pairs()}
    expected = {
        (method, _API_PREFIX + earlier, _API_PREFIX + later) for method, earlier, later in _HAND_REPRODUCED_PAIRS
    }

    assert len(expected) == 16
    missing = sorted(expected - recorded)
    assert not missing, "baseline lacks hand-reproduced pairs (matcher or technical analysis is wrong):\n" + "\n".join(
        f"{method} {earlier} before {later}" for method, earlier, later in missing
    )


def test_swapped_pair_is_an_order_violation():
    baseline = [OverlapPair("GET", "/c/users", "/c/{id}")]
    swapped = [_route("/c/{id}", "GET"), _route("/c/users", "GET")]

    assert _order_violations(baseline, swapped) == ["order changed: GET /c/users must come before /c/{id}"]


def test_missing_baseline_route_is_a_violation():
    baseline = [OverlapPair("GET", "/c/users", "/c/{id}")]

    assert _order_violations(baseline, [_route("/c/users", "GET")]) == ["baseline route missing: GET /c/{id}"]


def test_intact_order_has_no_violation():
    baseline = [OverlapPair("GET", "/c/users", "/c/{id}")]

    assert _order_violations(baseline, [_route("/c/users", "GET"), _route("/c/{id}", "GET")]) == []


def test_new_pair_is_found_without_failing_the_order_check():
    baseline = [OverlapPair("GET", "/c/users", "/c/{id}")]
    routes = [
        _route("/c/users", "GET"),
        _route("/c/{id}", "GET"),
        _route("/c/{id}/x", "GET"),
        _route("/c/{id}/{y}", "GET"),
    ]

    assert _new_pairs(baseline, routes) == [OverlapPair("GET", "/c/{id}/x", "/c/{id}/{y}")]
    assert _order_violations(baseline, routes) == []
