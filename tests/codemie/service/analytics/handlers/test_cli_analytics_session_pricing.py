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

"""`cost_incomplete` and `unpriced_models` of ONE session (Sessions list, Session Details), EPMCDME-17152: computed from
that session's own (session, model) rows with the pricing the cost already uses, never from the window's metadata."""

from unittest.mock import AsyncMock

import pytest

from tests.codemie.service.analytics.handlers.test_cli_analytics_handler import (
    BASE_COST_ROW,
    DETAIL_SCOPE,
    UNPRICED_MODEL,
    _priced_detail_handler,
    _two_model_catalog,
    make_filter,
    make_handler,
)

_MIXED = [  # a partial session, a fully unpriced one and a complete sibling, in one page
    {"session_id": "partial", "model_name": UNPRICED_MODEL, "input_tokens": 1_000_000},
    {"session_id": "partial", "model_name": "claude-haiku", "input_tokens": 1_000_000},
    {"session_id": "unpriced", "model_name": UNPRICED_MODEL, "input_tokens": 1_000_000},
    {"session_id": "complete", "model_name": "claude-haiku", "input_tokens": 1_000_000},
    {"session_id": "idle", "model_name": UNPRICED_MODEL, "input_tokens": 0},  # a zero-token row is no usage
]


@pytest.mark.asyncio
async def test_each_listed_session_carries_the_pricing_of_its_own_rows():
    handler = make_handler(
        cost_facts=[{**BASE_COST_ROW, "session_id": s} for s in ("partial", "unpriced", "complete", "idle")]
    )
    handler._repo.get_session_model_costs = AsyncMock(return_value=_MIXED)

    with _two_model_catalog():
        data, pricer, _ = await handler.get_sessions(
            make_filter(), page=0, per_page=20, sort_by="start_time", search=None
        )

    by_id = {s["trace_id"]: s for s in data["sessions"]}
    seen = {sid: (s["cost_incomplete"], s["unpriced_models"], s["cost_available"]) for sid, s in by_id.items()}
    assert seen == {
        "partial": (True, [UNPRICED_MODEL], True),  # the priced part kept, still marked
        "unpriced": (True, [UNPRICED_MODEL], False),
        "complete": (False, [], True),  # the window's warning is never copied onto a complete sibling
        "idle": (False, [], True),
    }
    assert pricer.unpriced == [UNPRICED_MODEL]  # the window metadata is unchanged
    assert by_id["partial"]["cost_usd"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_the_detail_carries_the_pricing_of_its_one_session():
    mixed = [{k: v for k, v in r.items() if k != "session_id"} for r in _MIXED[:2]]
    complete = [{"model_name": "claude-haiku", "input_tokens": 1_000_000}]
    unpriced = [{"model_name": UNPRICED_MODEL, "input_tokens": 1_000_000}]

    with _two_model_catalog():
        seen = {}
        for name, rows in (("mixed", mixed), ("complete", complete), ("unpriced", unpriced)):
            detail, _, _ = await _priced_detail_handler(rows).get_session_detail("s1", scope=DETAIL_SCOPE)
            seen[name] = (detail["cost_incomplete"], detail["unpriced_models"], detail["cost_available"])

    assert seen == {
        "mixed": (True, [UNPRICED_MODEL], True),
        "complete": (False, [], True),
        "unpriced": (True, [UNPRICED_MODEL], False),
    }
