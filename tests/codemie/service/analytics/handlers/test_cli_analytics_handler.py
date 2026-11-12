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

import asyncio

import pytest
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from tests.codemie.repository.cli_analytics.support.bounded import bounded_to_itself
from tests.codemie.repository.cli_analytics.support.grouped_money import fake_cost_money, price_groups
from tests.codemie.repository.cli_analytics.support.scopes import DETAIL_SCOPE, EXCLUDE_SCOPE
from tests.codemie.repository.cli_analytics.support.users_reads import install_users_reads
from codemie.configs.config import config
from codemie.configs.llm_config import CostConfig
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.ports import (
    RollupCost,
    G_MODEL,
    G_MODEL_PERSONAL,
    G_MODEL_ROUTE,
    G_PERSONAL,
    G_ROUTE,
    G_TOTAL,
    G_USER,
    G_USER_PERSONAL,
    G_USER_ROUTE,
    SESSION_PAGE_SORTS,
    CostGroup,
    GroupedMoney,
    ModelRates,
    ProjectPage,
)
from codemie.service.analytics.handlers.funding_buckets import buckets_from_groups
from codemie.repository.cli_analytics.scope import ViewerScope
from codemie.service.analytics.handlers.cli_analytics_handler import (
    LocalAnalyticsHandler,
    _count_dispatch_subtypes,
    _group_repositories_into_projects,
    _iso,
)


UNPRICED_MODEL = "definitely-not-a-real-model-xyz-9999"

# A non-admin's Sessions view: only the viewer's own Personal sessions.
OWN_SCOPE = ViewerScope(viewer_email="dev@x.com", unrestricted=False, admin_projects=frozenset(), mode="own_only")


def page_reader(cost_facts, ordered_ids=None):
    """`get_session_page` answering with the given facts' sessions (or `ordered_ids`): the reader's page contract."""
    ids = ordered_ids if ordered_ids is not None else [r["session_id"] for r in cost_facts or []]
    return AsyncMock(return_value=(len(ids), list(ids)))


def make_handler(cost_facts=None):
    repo = MagicMock()
    repo.get_session_cost_facts = AsyncMock(return_value=cost_facts or [])
    repo.get_session_page = page_reader(cost_facts)
    repo.get_turns_by_session = AsyncMock(return_value=[])
    repo.get_tool_success_by_session = AsyncMock(return_value=[])
    repo.get_lines_by_session = AsyncMock(return_value=[])
    repo.get_model_breakdown = AsyncMock(return_value=[])
    repo.get_session_model_costs = AsyncMock(return_value=[])
    repo.get_session_funding_costs = AsyncMock(return_value=[])
    repo.get_skill_names_by_session = AsyncMock(return_value=[])
    with_window_reads(repo, cost_facts)
    return LocalAnalyticsHandler(bounded_to_itself(repo))


def with_window_reads(repo, cost_facts=None, flags=()):
    """The window statements of the Sessions list: the model kind flags and the framework window (every listed
    session in order, no skill names), the reads `get_sessions` makes before it picks the page."""
    ids = [r["session_id"] for r in cost_facts or []]
    repo.get_window_model_flags = AsyncMock(return_value=list(flags))
    repo.get_cost_models = AsyncMock(return_value=[model for model, _ in flags])
    repo.get_cost_page = AsyncMock(return_value=(len(ids), list(ids), list(flags)))
    repo.get_framework_page = framework_page_reader([(sid, []) for sid in ids], flags)
    return repo


def framework_page_reader(window, flags=()):
    """`get_framework_page` as the database answers it: the window's distinct name sets go to `matching`, and the
    page is cut from the sessions of the accepted sets in the window's order, with the exact total."""

    async def get_framework_page(f, sort_by, page, per_page, matching, rates=None, search=None, is_unattributed=False):
        sets = [list(names) for names in {tuple(names) for _, names in window}]
        accepted = {tuple(names) for names in matching(sets)}
        ids = [sid for sid, names in window if tuple(names) in accepted]
        return len(ids), ids[page * per_page : (page + 1) * per_page], list(flags)

    return AsyncMock(side_effect=get_framework_page)


BASE_COST_ROW = {
    "session_id": "s1",
    "developer_name": "dev",
    "model_name": "claude",
    "cost_usd": 1.0,
    "input_tokens": 100,
    "output_tokens": 50,
    "cache_read_tokens": 0,
    "cache_creation_tokens": 0,
    "prompt": "hello",
    "started_at": datetime(2026, 1, 1, 0, 0, 0),
    "last_event_at": datetime(2026, 1, 1, 0, 1, 0),
    "duration_ms": 60_000,
}


def make_filter():
    return LocalAnalyticsFilter(scope=EXCLUDE_SCOPE, start_dt=datetime(2026, 1, 1), end_dt=datetime(2026, 12, 31))


@pytest.mark.asyncio
async def test_get_sessions_reports_the_reader_duration():
    """The reader's activity-range duration, the same one detail and Users Avg report, not a recomputation."""
    handler = make_handler(cost_facts=[{**BASE_COST_ROW, "duration_ms": 72_000}])
    data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)
    assert data["sessions"][0]["duration_ms"] == 72_000


COSTLESS_ROW = {
    **{k: v for k, v in BASE_COST_ROW.items() if k not in ("cost_usd", "input_tokens", "output_tokens")},
    "session_id": "s2",
    "cost_usd": None,
    "input_tokens": None,
    "output_tokens": None,
    "model_name": None,
}


@pytest.mark.asyncio
async def test_costless_session_is_listed_with_zero_cost_and_cost_available_false():
    handler = make_handler(cost_facts=[COSTLESS_ROW])
    data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)
    session = data["sessions"][0]
    assert session["cost_usd"] == 0.0
    assert session["cost_available"] is False
    assert session["input_tokens"] == 0
    assert handler._repo.get_session_cost_facts.call_args.kwargs["include_costless"] is True


@pytest.mark.asyncio
async def test_priced_session_has_cost_available_true():
    handler = make_handler(cost_facts=[BASE_COST_ROW])
    handler._repo.get_session_model_costs = AsyncMock(
        return_value=[{"session_id": "s1", "model_name": "claude-haiku", "input_tokens": 1}]
    )
    with _two_model_catalog():
        data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)
    assert data["sessions"][0]["cost_available"] is True


def _zero_rate_catalog():
    """The runtime catalog with one entry whose input, output and cache-read rates are all zero."""
    model = MagicMock()
    model.base_name = "gpt-5.5-2026-04-24"
    model.deployment_name = "gpt-5.5-2026-04-24"
    model.cost = CostConfig(input=0.0, output=0.0, cache_read_input_token_cost=0.0)
    return patch(
        "codemie.service.analytics.handlers.coding_agent_pricing.llm_service.get_all_llm_model_info",
        return_value=[model],
    )


_ZERO_RATE_MODEL_ROW = {
    "session_id": "s1",
    "model_name": "gpt-5.5-2026-04-24",
    "normalized_model_name": "gpt-5.5-2026-04-24",
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_tokens": 40_110,
    "cache_creation_tokens": 0,
    "cache_creation_5m_tokens": None,
    "cache_creation_1h_tokens": None,
}


@pytest.mark.asyncio
async def test_zero_rate_model_session_is_listed_with_cost_available_false():
    row = {**BASE_COST_ROW, "model_name": "gpt-5.5-2026-04-24", "input_tokens": 0, "output_tokens": 0}
    handler = make_handler(cost_facts=[{**row, "cache_read_tokens": 40_110}])
    handler._repo.get_session_model_costs = AsyncMock(return_value=[_ZERO_RATE_MODEL_ROW])
    with _zero_rate_catalog():
        data, pricer, _ = await handler.get_sessions(
            make_filter(), page=0, per_page=20, sort_by="start_time", search=None
        )
    session = data["sessions"][0]
    assert session["cost_available"] is False
    assert session["cost_usd"] == 0.0
    assert pricer.unpriced == ["gpt-5.5-2026-04-24"]


@pytest.mark.asyncio
async def test_zero_rate_model_session_detail_has_cost_available_false():
    handler = _priced_detail_handler([_ZERO_RATE_MODEL_ROW])
    with _zero_rate_catalog():
        detail, unpriced, _ = await handler.get_session_detail("s1", scope=DETAIL_SCOPE)
    assert detail["cost_available"] is False
    assert detail["cost_usd"] == 0.0
    assert unpriced == ["gpt-5.5-2026-04-24"]


@pytest.mark.asyncio
async def test_costless_session_sorts_by_cost_without_error():
    handler = make_handler(cost_facts=[COSTLESS_ROW, BASE_COST_ROW])
    handler._repo.get_session_model_costs = AsyncMock(
        return_value=[{"session_id": "s1", "model_name": "claude-haiku", "input_tokens": 1}]
    )
    with _two_model_catalog():
        data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="cost_usd", search=None)
    assert {s["trace_id"] for s in data["sessions"]} == {"s1", "s2"}


@pytest.mark.asyncio
async def test_start_time_sort_pages_in_the_reader_and_reads_only_the_page_sessions():
    page_row = {**BASE_COST_ROW, "session_id": "s7"}
    handler = make_handler(cost_facts=[page_row])
    handler._repo.get_session_page = AsyncMock(return_value=(41, ["s7"]))
    data, _, data_as_of = await handler.get_sessions(
        make_filter(), page=2, per_page=1, sort_by="start_time", search="x", is_unattributed=True
    )
    repo = handler._repo
    assert repo.get_session_page.call_args.args[1:] == ("start_time", 2, 1, "x", True)
    assert [s["trace_id"] for s in data["sessions"]] == ["s7"]
    assert (data["total"], data["page"], data["per_page"]) == (41, 2, 1)
    assert data_as_of == "2026-01-01T00:00:00+00:00"
    for query in (repo.get_session_cost_facts, repo.get_turns_by_session, repo.get_session_model_costs):
        assert query.call_args.args[0].session_ids == ["s7"]


@pytest.mark.asyncio
async def test_a_page_past_the_end_lists_nothing_with_the_readers_total_and_reads_no_facts():
    handler = make_handler()
    handler._repo.get_session_page = AsyncMock(return_value=(12, []))
    data, _, data_as_of = await handler.get_sessions(
        make_filter(), page=9, per_page=5, sort_by="ctx_per_call", search=None
    )
    assert data == {"sessions": [], "page": 9, "per_page": 5, "total": 12}
    assert data_as_of is None
    handler._repo.get_session_cost_facts.assert_not_called()


@pytest.mark.asyncio
async def test_the_readers_page_order_is_the_response_order():
    older = {**BASE_COST_ROW, "session_id": "a", "started_at": datetime(2026, 1, 1)}
    newer = {**BASE_COST_ROW, "session_id": "b", "started_at": datetime(2026, 2, 1)}
    handler = make_handler(cost_facts=[older, newer])
    handler._repo.get_session_page = AsyncMock(return_value=(2, ["a", "b"]))  # not the Python start_time order
    data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)
    assert [s["trace_id"] for s in data["sessions"]] == ["a", "b"]


_DETAIL_READS = (
    "get_session_cost_facts",
    "get_turns_by_session",
    "get_tool_success_by_session",
    "get_lines_by_session",
    "get_session_model_costs",
)
_INPUT_ONLY = (True, False, False, False, False)
_NO_TOKENS = (False,) * 5


def _assert_details_read_only(repo, page_ids):
    for name in _DETAIL_READS:
        assert getattr(repo, name).call_args.args[0].session_ids == page_ids, name
    assert repo.get_skill_names_by_session.call_args.args[0] == page_ids


_FILTER_COMBINATIONS = [(None, False), ("fix", False), (None, True), ("fix", True)]


@pytest.mark.parametrize("sort_by", SESSION_PAGE_SORTS)
@pytest.mark.parametrize(("search", "is_unattributed"), _FILTER_COMBINATIONS)
@pytest.mark.asyncio
async def test_every_sort_reads_details_only_for_the_database_page(sort_by, search, is_unattributed):
    handler = make_handler(cost_facts=[{**BASE_COST_ROW, "session_id": "s7"}])
    repo = handler._repo
    repo.get_session_page = AsyncMock(return_value=(41, ["s7"]))
    repo.get_cost_models = AsyncMock(return_value=["claude-haiku"])
    repo.get_cost_page = AsyncMock(return_value=(41, ["s7"], [("claude-haiku", _INPUT_ONLY)]))
    with _two_model_catalog():
        data, _, _ = await handler.get_sessions(
            make_filter(), page=2, per_page=1, sort_by=sort_by, search=search, is_unattributed=is_unattributed
        )
    if sort_by == "cost_usd":  # the cost sort is one statement with the flags; the window is not read twice
        assert repo.get_cost_page.call_args.args[1:3] == (2, 1)
        assert repo.get_cost_page.call_args.args[4:] == (search, is_unattributed)
        repo.get_session_page.assert_not_called()
        repo.get_window_model_flags.assert_not_called()
    else:
        assert repo.get_session_page.call_args.args[1:] == (sort_by, 2, 1, search, is_unattributed)
    assert ([s["trace_id"] for s in data["sessions"]], data["total"]) == (["s7"], 41)
    _assert_details_read_only(repo, ["s7"])
    repo.get_framework_page.assert_not_called()


@pytest.mark.asyncio
async def test_the_cost_sort_pages_at_the_catalog_rates_of_every_window_model():
    handler = make_handler(cost_facts=[BASE_COST_ROW])
    repo = handler._repo
    repo.get_cost_models = AsyncMock(return_value=["claude-haiku", UNPRICED_MODEL])
    with _two_model_catalog():
        await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="cost_usd", search=None)
    assert repo.get_cost_page.call_args.args[3] == [
        ModelRates("claude-haiku", input=1e-6, output=5e-6, cache_read=0.1e-6),
        ModelRates(UNPRICED_MODEL),
    ]
    # The rates come from the models of the token rows, never from a page.
    assert repo.get_cost_page.call_args.args[0].session_ids is None


@pytest.mark.parametrize("sort_by", ["start_time", "ctx_per_call"])
@pytest.mark.asyncio
async def test_sorts_without_money_page_without_rates(sort_by):
    handler = make_handler(cost_facts=[BASE_COST_ROW])
    await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by=sort_by, search=None)
    assert handler._repo.get_session_page.call_args.kwargs.get("rates") is None


_WINDOW = [("s1", []), ("s2", ["sdlc-factory:complexity-assessor"]), ("s3", []), ("s4", [])]


@pytest.mark.parametrize("sort_by", SESSION_PAGE_SORTS)
@pytest.mark.parametrize(("search", "is_unattributed"), _FILTER_COMBINATIONS)
@pytest.mark.asyncio
async def test_the_framework_filter_classifies_the_ordered_window_and_reads_details_only_for_its_page(
    sort_by, search, is_unattributed
):
    """s1, s3 and s4 are Pure chat in the reader's order; page 1 of 2 per page is s4 alone, of 3."""
    handler = make_handler(cost_facts=[{**BASE_COST_ROW, "session_id": "s4"}])
    repo = handler._repo
    repo.get_framework_page = framework_page_reader(_WINDOW)
    data, _, _ = await handler.get_sessions(
        make_filter(),
        page=1,
        per_page=2,
        sort_by=sort_by,
        search=search,
        framework="Pure chat",
        is_unattributed=is_unattributed,
    )
    call = repo.get_framework_page.call_args
    assert call.args[1] == sort_by
    assert (call.kwargs["search"], call.kwargs["is_unattributed"]) == (search, is_unattributed)
    assert ([s["trace_id"] for s in data["sessions"]], data["total"]) == (["s4"], 3)
    _assert_details_read_only(repo, ["s4"])
    repo.get_session_page.assert_not_called()


@pytest.mark.asyncio
async def test_the_framework_filter_keeps_the_readers_order():
    handler = make_handler(cost_facts=[{**BASE_COST_ROW, "session_id": sid} for sid in ("s1", "s3", "s4")])
    handler._repo.get_framework_page = framework_page_reader([("s4", []), ("s1", []), ("s3", [])])
    data, _, _ = await handler.get_sessions(
        make_filter(), page=0, per_page=20, sort_by="start_time", search=None, framework="Pure chat"
    )
    assert [s["trace_id"] for s in data["sessions"]] == ["s4", "s1", "s3"]


@pytest.mark.asyncio
async def test_the_framework_filter_with_the_cost_sort_orders_the_window_at_the_catalog_rates():
    handler = make_handler(cost_facts=[{**BASE_COST_ROW, "session_id": "s4"}])
    repo = handler._repo
    repo.get_cost_models = AsyncMock(return_value=["claude-haiku"])
    repo.get_window_model_flags = AsyncMock(return_value=[("claude-haiku", _INPUT_ONLY)])
    repo.get_framework_page = framework_page_reader(_WINDOW, [("claude-haiku", _INPUT_ONLY)])
    with _two_model_catalog():
        await handler.get_sessions(
            make_filter(), page=0, per_page=20, sort_by="cost_usd", search=None, framework="Pure chat"
        )
    assert repo.get_framework_page.call_args.kwargs["rates"] == [
        ModelRates("claude-haiku", input=1e-6, output=5e-6, cache_read=0.1e-6)
    ]


@pytest.mark.asyncio
async def test_a_framework_page_past_the_end_lists_nothing_with_the_matched_total_and_reads_no_facts():
    handler = make_handler()
    handler._repo.get_framework_page = framework_page_reader(_WINDOW)
    data, _, data_as_of = await handler.get_sessions(
        make_filter(), page=5, per_page=2, sort_by="start_time", search=None, framework="Pure chat"
    )
    assert (data, data_as_of) == ({"sessions": [], "page": 5, "per_page": 2, "total": 3}, None)
    handler._repo.get_session_cost_facts.assert_not_called()


# The window's models: one uncatalogued and used, one catalogued without a cache-write rate and used for cache
# writes, one uncatalogued with zero tokens only, and the page's priced model. Only the last is on the page.
_WINDOW_FLAGS = [
    (UNPRICED_MODEL, _INPUT_ONLY),
    ("claude-opus-5", (False, False, False, True, False)),
    ("zero-token-model-xyz", _NO_TOKENS),
    ("claude-haiku", (True, True, False, False, False)),
]
_PAGE_MODEL_ROWS = [{"session_id": "s1", "model_name": "claude-haiku", "input_tokens": 10, "output_tokens": 1}]


@pytest.mark.parametrize(
    ("sort_by", "framework", "search"),
    [("cost_usd", None, None), ("start_time", "Pure chat", None), ("cost_usd", "Pure chat", "fix")],
)
@pytest.mark.asyncio
async def test_unpriced_models_cover_the_whole_filtered_window_on_the_cost_sort_and_the_framework_filter(
    sort_by, framework, search
):
    handler = make_handler(cost_facts=[BASE_COST_ROW])
    repo = handler._repo
    repo.get_window_model_flags = AsyncMock(return_value=_WINDOW_FLAGS)
    repo.get_cost_models = AsyncMock(return_value=[model for model, _ in _WINDOW_FLAGS])
    repo.get_cost_page = AsyncMock(return_value=(1, ["s1"], _WINDOW_FLAGS))
    repo.get_framework_page = framework_page_reader([("s1", [])], _WINDOW_FLAGS)
    repo.get_session_model_costs = AsyncMock(return_value=_PAGE_MODEL_ROWS)
    with _two_model_catalog():
        data, pricer, _ = await handler.get_sessions(
            make_filter(), page=0, per_page=1, sort_by=sort_by, search=search, framework=framework
        )
    assert [s["trace_id"] for s in data["sessions"]] == ["s1"]
    assert pricer.unpriced == sorted([UNPRICED_MODEL, "claude-opus-5"])


@pytest.mark.parametrize(("sort_by", "search"), [("start_time", None), ("ctx_per_call", None), ("start_time", "fix")])
@pytest.mark.asyncio
async def test_unpriced_models_cover_only_the_pages_rows_on_the_other_sorts_with_no_window_statement(sort_by, search):
    """As on main: the models of the page's rows, priced by the page's tokens; nothing is read for the window."""
    handler = make_handler(cost_facts=[BASE_COST_ROW])
    repo = handler._repo
    repo.get_window_model_flags = AsyncMock(return_value=_WINDOW_FLAGS)
    page_rows = [*_PAGE_MODEL_ROWS, {"session_id": "s1", "model_name": UNPRICED_MODEL, "input_tokens": 5}]
    repo.get_session_model_costs = AsyncMock(return_value=page_rows)
    with _two_model_catalog():
        _, pricer, _ = await handler.get_sessions(
            make_filter(), page=0, per_page=1, sort_by=sort_by, search=search, framework=None
        )
    repo.get_window_model_flags.assert_not_called()
    repo.get_framework_page.assert_not_called()
    assert pricer.unpriced == [UNPRICED_MODEL]  # claude-opus-5 is used only off the page: not listed


@pytest.mark.asyncio
async def test_a_zero_token_uncatalogued_model_on_the_page_is_not_listed():
    handler = make_handler(cost_facts=[BASE_COST_ROW])
    handler._repo.get_session_model_costs = AsyncMock(
        return_value=[{"session_id": "s1", "model_name": "zero-token-model-xyz"}]
    )
    with _two_model_catalog():
        _, pricer, _ = await handler.get_sessions(make_filter(), page=0, per_page=1, sort_by="start_time", search=None)
    assert pricer.unpriced == []


@pytest.mark.asyncio
async def test_an_empty_page_reports_no_unpriced_models_on_the_default_sort():
    handler = make_handler()
    handler._repo.get_session_page = AsyncMock(return_value=(3, []))
    _, pricer, _ = await handler.get_sessions(make_filter(), page=9, per_page=5, sort_by="start_time", search=None)
    assert pricer.unpriced == []
    handler._repo.get_window_model_flags.assert_not_called()


@pytest.mark.asyncio
async def test_an_empty_cost_sort_page_still_reports_the_windows_unpriced_models():
    handler = make_handler()
    handler._repo.get_cost_page = AsyncMock(return_value=(3, [], [(UNPRICED_MODEL, _INPUT_ONLY)]))
    _, pricer, _ = await handler.get_sessions(make_filter(), page=9, per_page=5, sort_by="cost_usd", search=None)
    assert pricer.unpriced == [UNPRICED_MODEL]


@pytest.mark.asyncio
async def test_unpriced_models_come_from_the_viewers_own_sessions_under_own_only():
    """Page rows are the viewer's own sessions; the cost sort's flags statement carries the viewer's scope."""
    own = LocalAnalyticsFilter(scope=OWN_SCOPE, start_dt=datetime(2026, 1, 1), end_dt=datetime(2026, 12, 31))
    handler = make_handler(cost_facts=[BASE_COST_ROW])
    handler._repo.get_cost_page = AsyncMock(return_value=(1, ["s1"], [(UNPRICED_MODEL, _INPUT_ONLY)]))
    _, pricer, _ = await handler.get_sessions(own, page=0, per_page=20, sort_by="cost_usd", search=None)
    flags_filter = handler._repo.get_cost_page.call_args.args[0]
    assert (flags_filter.scope, flags_filter.session_ids) == (OWN_SCOPE, None)
    assert pricer.unpriced == [UNPRICED_MODEL]


@pytest.mark.asyncio
async def test_costless_sessions_are_logged_as_a_count_only():
    handler = make_handler(cost_facts=[COSTLESS_ROW, {**COSTLESS_ROW, "session_id": "s3"}])
    with patch("codemie.service.analytics.handlers.cli_analytics_handler.logger") as log:
        await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)
    message, listed, no_token_rows, unpriced_models = log.info.call_args.args
    assert (listed, no_token_rows, unpriced_models) == (2, 2, 0)
    assert "cost_available=false" in message
    assert "s2" not in str(log.info.call_args) and "dev" not in str(log.info.call_args.args[1:])


@pytest.mark.parametrize(("sort_by", "framework"), [("cost_usd", None), ("start_time", "Pure chat")])
@pytest.mark.asyncio
async def test_costless_log_counts_only_the_listed_page(sort_by, framework):
    """Three sessions match the window but one is listed: the log says 1, not 3."""
    rows = [{**COSTLESS_ROW, "session_id": f"s{n}"} for n in range(3)]
    handler = make_handler(cost_facts=rows[:1])
    handler._repo.get_session_page = AsyncMock(return_value=(3, ["s0"]))
    handler._repo.get_cost_page = AsyncMock(return_value=(3, ["s0"], []))
    handler._repo.get_framework_page = framework_page_reader([(r["session_id"], []) for r in rows])
    with patch("codemie.service.analytics.handlers.cli_analytics_handler.logger") as log:
        data, _, _ = await handler.get_sessions(
            make_filter(), page=0, per_page=1, sort_by=sort_by, search=None, framework=framework
        )
    assert (len(data["sessions"]), data["total"]) == (1, 3)
    assert log.info.call_args.args[1:] == (1, 1, 0)


@pytest.mark.asyncio
async def test_totals_only_session_is_not_counted_as_costless_in_the_log():
    """A session with no cost_daily row but subagent totals rows ends with cost_available=true: not counted."""
    totals_only = {**COSTLESS_ROW, "session_id": "s4"}
    handler = make_handler(cost_facts=[COSTLESS_ROW, totals_only])
    handler._repo.get_session_model_costs = AsyncMock(
        return_value=[{"session_id": "s4", "model_name": "claude-haiku", "input_tokens": 1}]
    )
    with patch("codemie.service.analytics.handlers.cli_analytics_handler.logger") as log, _two_model_catalog():
        data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)
    assert {s["trace_id"]: s["cost_available"] for s in data["sessions"]} == {"s2": False, "s4": True}
    assert log.info.call_args.args[1:] == (1, 1, 0)


@pytest.mark.asyncio
async def test_unpriced_only_session_is_listed_cost_available_false_with_zero_cost_and_counted_in_the_log():
    """Model rows exist but the only model has no catalog price: not free, unavailable; and the log counts it."""
    unpriced = {**COSTLESS_ROW, "session_id": "s5"}
    handler = make_handler(cost_facts=[BASE_COST_ROW, unpriced])
    handler._repo.get_session_model_costs = AsyncMock(
        return_value=[
            {"session_id": "s1", "model_name": "claude-haiku", "input_tokens": 1_000_000},
            {"session_id": "s5", "model_name": UNPRICED_MODEL, "input_tokens": 1_000_000},
        ]
    )
    with patch("codemie.service.analytics.handlers.cli_analytics_handler.logger") as log, _two_model_catalog():
        data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)
    by_id = {s["trace_id"]: s for s in data["sessions"]}
    assert by_id["s5"]["cost_available"] is False
    assert by_id["s5"]["cost_usd"] == 0.0
    assert by_id["s1"]["cost_available"] is True
    assert by_id["s1"]["cost_usd"] == pytest.approx(1.0)
    assert log.info.call_args.args[1:] == (1, 0, 1)


@pytest.mark.asyncio
async def test_session_with_one_priced_and_one_unpriced_model_is_cost_available_true():
    handler = make_handler(cost_facts=[BASE_COST_ROW])
    handler._repo.get_session_model_costs = AsyncMock(
        return_value=[
            {"session_id": "s1", "model_name": UNPRICED_MODEL, "input_tokens": 1_000_000},
            {"session_id": "s1", "model_name": "claude-haiku", "input_tokens": 1_000_000},
        ]
    )
    with _two_model_catalog():
        data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)
    assert data["sessions"][0]["cost_available"] is True
    assert data["sessions"][0]["cost_usd"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_a_model_with_a_catalog_entry_priced_at_zero_counts_as_unpriced():
    """A zero-rate entry is no price (EPMCDME-16522): it never makes cost_available true."""
    free = MagicMock()
    free.base_name = "free-model"
    free.deployment_name = "free-model"
    free.cost = CostConfig(input=0.0, output=0.0)
    handler = make_handler(cost_facts=[BASE_COST_ROW])
    handler._repo.get_session_model_costs = AsyncMock(
        return_value=[{"session_id": "s1", "model_name": "free-model", "input_tokens": 1_000}]
    )
    with patch(
        "codemie.service.analytics.handlers.coding_agent_pricing.llm_service.get_all_llm_model_info",
        return_value=[free],
    ):
        data, pricer, _ = await handler.get_sessions(
            make_filter(), page=0, per_page=20, sort_by="start_time", search=None
        )
    assert data["sessions"][0]["cost_available"] is False
    assert data["sessions"][0]["cost_usd"] == 0.0
    assert pricer.unpriced == ["free-model"]


@pytest.mark.asyncio
async def test_get_sessions_with_a_missing_reader_duration_reports_zero():
    row = {k: v for k, v in BASE_COST_ROW.items() if k != "duration_ms"}
    handler = make_handler(cost_facts=[row])
    data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)
    assert data["sessions"][0]["duration_ms"] == 0


@pytest.mark.asyncio
async def test_get_sessions_classifies_a_session_that_only_dispatched_a_factory_subagent():
    """The reader answers the subagent type of a session that invoked no skill: the same rule applies."""
    handler = make_handler(cost_facts=[{**BASE_COST_ROW, "session_id": "agents-only"}])
    handler._repo.get_skill_names_by_session = AsyncMock(
        return_value=[{"session_id": "agents-only", "skill_names": ["sdlc-factory:complexity-assessor"]}]
    )

    data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)

    assert data["sessions"][0]["delivery_framework"] == "CodeMie AI Factory"


@pytest.mark.asyncio
async def test_get_sessions_classifies_a_hook_only_session_by_the_reader_skill_names():
    """The reader answers the skill names of a session known only from hooks (no OTel skill rows): the STLC
    Factory rule applies to it, a session with no skill name stays Pure chat."""
    handler = make_handler(
        cost_facts=[
            {**BASE_COST_ROW, "session_id": "hooks"},
            {**BASE_COST_ROW, "session_id": "empty"},
            {**BASE_COST_ROW, "session_id": "absent"},
        ]
    )
    handler._repo.get_skill_names_by_session = AsyncMock(
        return_value=[
            {"session_id": "hooks", "skill_names": ["stlc-factory:exploratory", "stlc-feature"]},
            {"session_id": "empty", "skill_names": []},
        ]
    )

    data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)

    frameworks = {s["trace_id"]: s["delivery_framework"] for s in data["sessions"]}
    assert frameworks == {
        "hooks": "STLC Factory (QA factory)",
        "empty": "Pure chat",
        "absent": "Pure chat",
    }


@pytest.mark.asyncio
async def test_session_repository_none_when_plugin_absent():
    """Empty string from the reader for plugin-less session must become None."""
    row = {**BASE_COST_ROW, "repository": "", "branch": ""}
    handler = make_handler(cost_facts=[row])
    data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)
    session = data["sessions"][0]
    assert session["repository"] is None
    assert session["branch"] is None


@pytest.mark.asyncio
async def test_session_repository_present_when_plugin_ran():
    """Non-empty repository and branch pass through unchanged."""
    row = {**BASE_COST_ROW, "repository": "my-repo", "branch": "main"}
    handler = make_handler(cost_facts=[row])
    data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)
    session = data["sessions"][0]
    assert session["repository"] == "my-repo"
    assert session["branch"] == "main"


@pytest.mark.asyncio
async def test_session_repository_present_branch_none_non_git_dir():
    """Repository present but branch empty (non-git directory) — only branch is None."""
    row = {**BASE_COST_ROW, "repository": "my-repo", "branch": ""}
    handler = make_handler(cost_facts=[row])
    data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)
    session = data["sessions"][0]
    assert session["repository"] == "my-repo"
    assert session["branch"] is None


@pytest.mark.asyncio
async def test_search_does_not_crash_when_repository_is_none():
    """Search with repository=None must not raise AttributeError."""
    row = {**BASE_COST_ROW, "repository": "", "branch": ""}
    handler = make_handler(cost_facts=[row])
    data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search="hello")
    assert isinstance(data["sessions"], list)


def make_repo_handler(cost_facts):
    repo = MagicMock()
    repo.get_session_cost_facts = AsyncMock(return_value=cost_facts)
    repo.get_turns_by_session = AsyncMock(return_value=[])
    repo.get_file_facts_by_session = AsyncMock(return_value=[])
    repo.get_tool_success_by_session = AsyncMock(return_value=[])
    repo.get_lines_by_session = AsyncMock(return_value=[])
    repo.get_session_model_costs = AsyncMock(return_value=[])
    repo.get_session_funding_costs = AsyncMock(return_value=[])
    return LocalAnalyticsHandler(bounded_to_itself(repo))


BASE_COST_FACT = {
    "session_id": "s1",
    "model_name": "claude",
    "cost_usd": 1.0,
    "input_tokens": 100,
    "output_tokens": 50,
    "cache_read_tokens": 0,
    "cache_creation_tokens": 0,
    "repository": None,
    "branch": None,
    "project_name": None,
}


@pytest.mark.asyncio
async def test_repositories_unattributed_bucket_when_plugin_absent():
    """Plugin-less sessions form a bucket with repository=None, not silently excluded."""
    cost_facts = [
        {**BASE_COST_FACT, "session_id": "s1", "repository": "my-repo", "branch": "main", "project_name": "proj"},
        {
            **BASE_COST_FACT,
            "session_id": "s2",
            "cost_usd": 0.5,
            "repository": None,
            "branch": None,
            "project_name": None,
        },
    ]
    handler = make_repo_handler(cost_facts)
    data, _ = await handler.get_repositories(make_filter(), page=0, per_page=50, include_branches=False)
    repos = data["rows"]
    assert len(repos) == 2
    unattributed = next((r for r in repos if r["repository"] is None), None)
    assert unattributed is not None


@pytest.mark.asyncio
async def test_repositories_backend_rolls_up_branches_with_nested_tool_success_rate():
    """EPMCDME-15585 repro: 1 call/0 success + 3 calls/3 success across two branches
    of one repo. The backend sums raw counts and calls _pct once per level (repository
    and each branch) -- a percentage is never averaged, so the ticket's bug can't come
    back regardless of which side (backend or UI) renders it."""
    cost_facts = [
        {**BASE_COST_FACT, "session_id": "s1", "repository": "repo-x", "branch": "sweep-cd", "project_name": "proj"},
        {**BASE_COST_FACT, "session_id": "s2", "repository": "repo-x", "branch": "sweep-cd-b", "project_name": "proj"},
    ]
    handler = make_repo_handler(cost_facts)
    handler._repo.get_tool_success_by_session = AsyncMock(
        return_value=[
            {"session_id": "s1", "tool_calls": 1, "tool_calls_success": 0},
            {"session_id": "s2", "tool_calls": 3, "tool_calls_success": 3},
        ]
    )

    nested_rows = (await handler.get_repositories(make_filter(), page=0, per_page=50, include_branches=True))[0]["rows"]
    repo_x = next(r for r in nested_rows if r["repository"] == "repo-x")
    assert (repo_x["tool_calls"], repo_x["tool_calls_success"]) == (4, 3)
    assert repo_x["tool_success_rate"] == 75.0

    branches = {b["branch"]: b for b in repo_x["branches"]}
    assert (branches["sweep-cd"]["tool_calls"], branches["sweep-cd"]["tool_calls_success"]) == (1, 0)
    assert branches["sweep-cd"]["tool_success_rate"] == 0.0
    assert (branches["sweep-cd-b"]["tool_calls"], branches["sweep-cd-b"]["tool_calls_success"]) == (3, 3)
    assert branches["sweep-cd-b"]["tool_success_rate"] == 100.0

    flat_rows = (await handler.get_repositories(make_filter(), page=0, per_page=50, include_branches=False))[0]["rows"]
    repo_x_flat = next(r for r in flat_rows if r["repository"] == "repo-x")
    assert (repo_x_flat["tool_calls"], repo_x_flat["tool_calls_success"]) == (4, 3)
    assert repo_x_flat["tool_success_rate"] == 75.0


@pytest.mark.asyncio
async def test_repositories_zero_call_branch_does_not_drag_rate_down():
    """EPMCDME-15585 AC3: a branch with zero tool calls must not pull the repository's
    rate toward 0%. It contributes 0/0 to the sums, which changes neither side."""
    cost_facts = [
        {**BASE_COST_FACT, "session_id": "s1", "repository": "repo-y", "branch": "has-calls", "project_name": "proj"},
        {**BASE_COST_FACT, "session_id": "s2", "repository": "repo-y", "branch": "no-calls", "project_name": "proj"},
    ]
    handler = make_repo_handler(cost_facts)
    handler._repo.get_tool_success_by_session = AsyncMock(
        return_value=[
            {"session_id": "s1", "tool_calls": 2, "tool_calls_success": 2},
            {"session_id": "s2", "tool_calls": 0, "tool_calls_success": 0},
        ]
    )

    rows = (await handler.get_repositories(make_filter(), page=0, per_page=50, include_branches=True))[0]["rows"]
    repo_y = next(r for r in rows if r["repository"] == "repo-y")
    assert repo_y["tool_success_rate"] == 100.0
    branches = {b["branch"]: b for b in repo_y["branches"]}
    assert branches["no-calls"]["tool_success_rate"] == 0.0  # denominator-guarded display, not a vote


def _project_facts():
    """The facts of the page's sessions, as the restricted detail reads return them."""
    return [
        {**BASE_COST_FACT, "session_id": "a1", "repository": "repo-a", "branch": "main", "project_name": "alpha"},
        {**BASE_COST_FACT, "session_id": "a2", "repository": "repo-b", "branch": "dev", "project_name": "alpha"},
        {**BASE_COST_FACT, "session_id": "n1", "repository": "repo-d", "branch": "main", "project_name": None},
    ]


def _project_page(**overrides) -> ProjectPage:
    fields = {
        "total": 4,
        "project_names": ["alpha", None],
        "session_ids": ["a1", "a2", "n1"],
        "matched_session_ids": None,
        "model_flags": [],
    }
    return ProjectPage(**{**fields, **overrides})


def _project_handler(page: ProjectPage | None = None, facts: list[dict] | None = None):
    handler = make_repo_handler(_project_facts() if facts is None else facts)
    repo = handler._repo
    repo.get_cost_models = AsyncMock(return_value=["m"])
    repo.get_project_page = AsyncMock(return_value=page or _project_page())
    repo.get_tool_success_by_session = AsyncMock(
        return_value=[
            {"session_id": "a1", "tool_calls": 1, "tool_calls_success": 0},
            {"session_id": "a2", "tool_calls": 3, "tool_calls_success": 3},
        ]
    )
    return handler


@pytest.mark.asyncio
async def test_projects_sum_repositories_and_compute_success_from_counts():
    handler = _project_handler()
    data, _ = await handler.get_projects(make_filter(), page=0, per_page=10)
    alpha = next(p for p in data["rows"] if p["project_name"] == "alpha")
    assert (alpha["session_count"], alpha["repository_count"]) == (2, 2)
    assert (alpha["tool_calls"], alpha["tool_calls_success"]) == (4, 3)
    assert alpha["tool_success_rate"] == 75.0  # 3/4, not the average of 0% and 100%
    assert {r["repository"] for r in alpha["repositories"]} == {"repo-a", "repo-b"}
    assert all(r["branches"] for r in alpha["repositories"])
    assert "_sids" not in alpha and all("_sids" not in r for r in alpha["repositories"])
    assert alpha["net_lines"] is None  # no lines reported anywhere


@pytest.mark.asyncio
async def test_projects_keep_the_database_order_and_total():
    # The database put the unattributed project last and counted 4 projects; the page is not re-sorted here.
    handler = _project_handler(_project_page(project_names=[None, "alpha"], total=7))
    data, _ = await handler.get_projects(make_filter(), page=1, per_page=2)
    assert [p["project_name"] for p in data["rows"]] == [None, "alpha"]
    assert data["pagination"] == {"page": 1, "per_page": 2, "total_count": 7, "has_more": True}


@pytest.mark.asyncio
async def test_projects_page_is_decided_by_the_database_with_the_window_rates():
    handler = _project_handler()
    f = make_filter()
    await handler.get_projects(f, page=3, per_page=5, search="  Alpha ")
    repo = handler._repo
    repo.get_cost_models.assert_awaited_once_with(f)
    (_, page, per_page, rates, search), _ = repo.get_project_page.await_args
    assert (page, per_page, search) == (3, 5, "Alpha")
    assert [r.model for r in rates] == ["m"]


@pytest.mark.asyncio
async def test_projects_detail_reads_are_restricted_to_the_page_sessions():
    handler = _project_handler()
    await handler.get_projects(make_filter(), page=0, per_page=10)
    detail_filter = handler._repo.get_session_cost_facts.await_args.args[0]
    assert detail_filter.session_ids == ["a1", "a2", "n1"]


@pytest.mark.asyncio
async def test_projects_window_buckets_read_the_whole_window_without_a_search():
    handler = _project_handler()
    f = make_filter()
    await handler.get_projects(f, page=0, per_page=10)
    assert handler._repo.get_session_funding_costs.await_args.args[0].session_ids is None


@pytest.mark.asyncio
async def test_projects_window_buckets_read_only_the_matching_sessions_with_a_search():
    handler = _project_handler(_project_page(matched_session_ids=["a1", "a2", "x9"]))
    await handler.get_projects(make_filter(), page=0, per_page=10, search="alpha")
    assert handler._repo.get_session_funding_costs.await_args.args[0].session_ids == ["a1", "a2", "x9"]


@pytest.mark.asyncio
async def test_projects_blank_search_is_no_search():
    handler = _project_handler()
    await handler.get_projects(make_filter(), page=0, per_page=10, search="   ")
    assert handler._repo.get_project_page.await_args.args[4] is None


@pytest.mark.asyncio
async def test_projects_empty_page_reads_no_details_but_reports_the_window_unpriced_models():
    page = _project_page(total=2, project_names=[], session_ids=[], model_flags=[("uncatalogued-model", (True,) * 5)])
    handler = _project_handler(page, facts=[])
    data, pricer = await handler.get_projects(make_filter(), page=5, per_page=10)
    assert data["rows"] == []
    assert data["pagination"] == {"page": 5, "per_page": 10, "total_count": 2, "has_more": False}
    handler._repo.get_session_cost_facts.assert_not_awaited()
    assert "uncatalogued-model" in pricer.unpriced
    assert "funding" in data and "personal_billed" in data


def test_projects_sort_repositories_by_cost_and_follow_the_given_project_order():
    def repo(project, name, cost):
        return {
            "project_name": project,
            "repository": name,
            "session_count": 1,
            "turns": 1,
            "cost_usd": cost,
            "files_changed": 0,
            "lines_added": None,
            "lines_removed": None,
            "tool_calls": 0,
            "tool_calls_success": 0,
            "subscription_types": [],
            "project_origins": [],
            "_sids": {f"{project}-{name}"},
        }

    rows = [repo(None, "r0", 99.0), repo("cheap", "r1", 1.0), repo("big", "r2", 5.0), repo("big", "r3", 7.0)]
    projects = _group_repositories_into_projects(rows, ["big", "cheap", None])
    assert [p["project_name"] for p in projects] == ["big", "cheap", None]
    assert projects[0]["cost_usd"] == 12.0
    assert [r["repository"] for r in projects[0]["repositories"]] == ["r3", "r2"]


@pytest.mark.parametrize(
    ("calls", "success", "expected_pct"),
    [
        (16, 1, 6.3),  # exact .5 tie: 6.25 -> 6.3, half away from zero
        (80, 23, 28.8),  # float-error near-tie: 23/80*100 = 28.749999999999996 in IEEE754
        (80, 41, 51.3),
        (80, 51, 63.8),
    ],
)
@pytest.mark.asyncio
async def test_pct_tie_and_float_error_values(calls, success, expected_pct):
    """EPMCDME-15585 review: these exact values disagreed between the backend's _pct and the
    UI's independent calc (23/80 gave 28.8 here vs 28.7 in JS, from float-arithmetic order,
    not a rounding-rule difference). Locks _pct's output for them now that it is the only
    producer of this percentage."""
    cost_facts = [{**BASE_COST_FACT, "session_id": "s1", "repository": "repo-z", "branch": "main"}]
    handler = make_repo_handler(cost_facts)
    handler._repo.get_tool_success_by_session = AsyncMock(
        return_value=[{"session_id": "s1", "tool_calls": calls, "tool_calls_success": success}]
    )
    rows = (await handler.get_repositories(make_filter(), page=0, per_page=50, include_branches=False))[0]["rows"]
    repo_z = next(r for r in rows if r["repository"] == "repo-z")
    assert repo_z["tool_success_rate"] == expected_pct


@pytest.mark.asyncio
async def test_is_unattributed_filter_delegates_to_repository():
    """is_unattributed=True must be forwarded to get_session_cost_facts; handler does not filter in-memory."""
    unattributed_rows = [
        {**BASE_COST_ROW, "session_id": "s2", "repository": "", "branch": ""},
        {**BASE_COST_ROW, "session_id": "s3", "repository": None, "branch": None},
    ]
    repo = MagicMock()
    repo.get_session_cost_facts = AsyncMock(return_value=unattributed_rows)
    repo.get_session_page = page_reader(unattributed_rows)
    with_window_reads(repo)
    repo.get_turns_by_session = AsyncMock(return_value=[])
    repo.get_tool_success_by_session = AsyncMock(return_value=[])
    repo.get_lines_by_session = AsyncMock(return_value=[])
    repo.get_model_breakdown = AsyncMock(return_value=[])
    repo.get_session_model_costs = AsyncMock(return_value=[])
    repo.get_session_funding_costs = AsyncMock(return_value=[])
    repo.get_skill_names_by_session = AsyncMock(return_value=[])
    handler = LocalAnalyticsHandler(bounded_to_itself(repo))

    await handler.get_sessions(
        make_filter(), page=0, per_page=20, sort_by="start_time", search=None, is_unattributed=True
    )
    repo.get_session_cost_facts.assert_called_once_with(ANY, is_unattributed=True, include_costless=True)
    assert repo.get_session_page.call_args.args[-1] is True


@pytest.mark.asyncio
async def test_is_unattributed_false_delegates_to_repository():
    """is_unattributed=False must be forwarded to get_session_cost_facts with is_unattributed=False."""
    rows = [
        {**BASE_COST_ROW, "session_id": "s1", "repository": "my-repo", "branch": "main"},
        {**BASE_COST_ROW, "session_id": "s2", "repository": None, "branch": None},
    ]
    repo = MagicMock()
    repo.get_session_cost_facts = AsyncMock(return_value=rows)
    repo.get_session_page = page_reader(rows)
    with_window_reads(repo)
    repo.get_turns_by_session = AsyncMock(return_value=[])
    repo.get_tool_success_by_session = AsyncMock(return_value=[])
    repo.get_lines_by_session = AsyncMock(return_value=[])
    repo.get_model_breakdown = AsyncMock(return_value=[])
    repo.get_session_model_costs = AsyncMock(return_value=[])
    repo.get_session_funding_costs = AsyncMock(return_value=[])
    repo.get_skill_names_by_session = AsyncMock(return_value=[])
    handler = LocalAnalyticsHandler(bounded_to_itself(repo))

    await handler.get_sessions(
        make_filter(), page=0, per_page=20, sort_by="start_time", search=None, is_unattributed=False
    )
    repo.get_session_cost_facts.assert_called_once_with(ANY, is_unattributed=False, include_costless=True)


@pytest.mark.asyncio
async def test_repositories_search_delegates_to_repository():
    """search param must be forwarded to the reader's ranking and facts statements; handler does not filter in-memory."""
    cost_facts = [
        {**BASE_COST_FACT, "session_id": "s1", "repository": "codemie", "branch": "main"},
    ]
    repo = MagicMock()
    repo.get_session_cost_facts = AsyncMock(return_value=cost_facts)
    repo.get_turns_by_session = AsyncMock(return_value=[])
    repo.get_file_facts_by_session = AsyncMock(return_value=[])
    repo.get_tool_success_by_session = AsyncMock(return_value=[])
    repo.get_lines_by_session = AsyncMock(return_value=[])
    repo.get_session_model_costs = AsyncMock(return_value=[])
    repo.get_session_funding_costs = AsyncMock(return_value=[])
    handler = LocalAnalyticsHandler(bounded_to_itself(repo))

    await handler.get_repositories(make_filter(), page=0, per_page=50, include_branches=False, search="codem")
    repo.get_repository_ranking.assert_called_once_with(ANY, False, "codem", ANY, 0, 50)
    repo.get_repository_facts.assert_called_once_with(ANY, False, "codem", ["codemie"], unattributed=False)


@pytest.mark.asyncio
async def test_repositories_search_none_delegates_to_repository():
    """None search must be forwarded to the reader's ranking and facts statements as search=None."""
    cost_facts = [
        {**BASE_COST_FACT, "session_id": "s1", "repository": "codemie", "branch": "main"},
        {**BASE_COST_FACT, "session_id": "s2", "repository": None, "branch": None},
    ]
    repo = MagicMock()
    repo.get_session_cost_facts = AsyncMock(return_value=cost_facts)
    repo.get_turns_by_session = AsyncMock(return_value=[])
    repo.get_file_facts_by_session = AsyncMock(return_value=[])
    repo.get_tool_success_by_session = AsyncMock(return_value=[])
    repo.get_lines_by_session = AsyncMock(return_value=[])
    repo.get_session_model_costs = AsyncMock(return_value=[])
    repo.get_session_funding_costs = AsyncMock(return_value=[])
    handler = LocalAnalyticsHandler(bounded_to_itself(repo))

    await handler.get_repositories(make_filter(), page=0, per_page=50, include_branches=False, search=None)
    repo.get_repository_ranking.assert_called_once_with(ANY, False, None, ANY, 0, 50)
    repo.get_repository_facts.assert_called_once_with(ANY, False, None, ["codemie"], unattributed=True)


@pytest.mark.asyncio
async def test_branch_filter_forwarded_to_get_session_cost_facts():
    """branch kwarg must be set on the filter before get_session_cost_facts is called."""
    repo = MagicMock()
    repo.get_session_cost_facts = AsyncMock(return_value=[BASE_COST_ROW])
    repo.get_session_page = page_reader([BASE_COST_ROW])
    with_window_reads(repo)
    repo.get_turns_by_session = AsyncMock(return_value=[])
    repo.get_tool_success_by_session = AsyncMock(return_value=[])
    repo.get_lines_by_session = AsyncMock(return_value=[])
    repo.get_model_breakdown = AsyncMock(return_value=[])
    repo.get_session_model_costs = AsyncMock(return_value=[])
    repo.get_session_funding_costs = AsyncMock(return_value=[])
    repo.get_skill_names_by_session = AsyncMock(return_value=[])
    handler = LocalAnalyticsHandler(bounded_to_itself(repo))

    await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None, branch="main")

    call_filter = repo.get_session_cost_facts.call_args.args[0]
    assert call_filter.branch == "main"


@pytest.mark.asyncio
async def test_branch_none_when_not_provided():
    """branch defaults to None — filter.branch must remain None when not passed."""
    repo = MagicMock()
    repo.get_session_cost_facts = AsyncMock(return_value=[BASE_COST_ROW])
    repo.get_session_page = page_reader([BASE_COST_ROW])
    with_window_reads(repo)
    repo.get_turns_by_session = AsyncMock(return_value=[])
    repo.get_tool_success_by_session = AsyncMock(return_value=[])
    repo.get_lines_by_session = AsyncMock(return_value=[])
    repo.get_model_breakdown = AsyncMock(return_value=[])
    repo.get_session_model_costs = AsyncMock(return_value=[])
    repo.get_session_funding_costs = AsyncMock(return_value=[])
    repo.get_skill_names_by_session = AsyncMock(return_value=[])
    handler = LocalAnalyticsHandler(bounded_to_itself(repo))

    await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)

    call_filter = repo.get_session_cost_facts.call_args.args[0]
    assert call_filter.branch is None


@pytest.mark.asyncio
async def test_a_branch_keeps_a_filter_that_denies_every_session_denying():
    # A project admin who administers no project sees nothing, whatever branch /sessions asks for.
    handler = make_handler(cost_facts=[BASE_COST_ROW])
    denied = LocalAnalyticsFilter(
        scope=EXCLUDE_SCOPE, start_dt=datetime(2026, 1, 1), end_dt=datetime(2026, 12, 31), deny_all=True
    )

    await handler.get_sessions(denied, page=0, per_page=20, sort_by="start_time", search=None, branch="main")

    repo = handler._repo
    for query in (
        repo.get_session_page,
        repo.get_session_cost_facts,
        repo.get_turns_by_session,
        repo.get_tool_success_by_session,
        repo.get_lines_by_session,
        repo.get_session_model_costs,
    ):
        call_filter = query.call_args.args[0]
        assert (call_filter.deny_all, call_filter.branch) == (True, "main")


@pytest.mark.parametrize(
    "rows, expected",
    [
        (
            [
                {"model_name": "claude-sonnet-5", "cost_usd": 0.12},
                {"model_name": "claude-opus-5", "cost_usd": 0.62},
                {"model_name": "claude-haiku", "cost_usd": 0.05},
            ],
            "claude-haiku, claude-opus-5, claude-sonnet-5",
        ),
        (
            [
                {"model_name": "claude-opus-5-5", "cost_usd": 0.7},
                {"model_name": "claude-haiku-4-5-20251001", "cost_usd": 0.1},
            ],
            "claude-haiku-4-5-20251001, claude-opus-5-5",
        ),
    ],
)
def test_display_models_lists_all_models_alphabetically(rows, expected):
    assert LocalAnalyticsHandler._display_models(rows, "claude-haiku") == expected


def test_display_models_sorts_equal_cost_models_alphabetically():
    rows = [{"model_name": "zeta", "cost_usd": 1.0}, {"model_name": "alpha", "cost_usd": 1.0}]
    assert LocalAnalyticsHandler._display_models(rows, "zeta") == "alpha, zeta"


def test_display_models_returns_the_only_model_unchanged():
    assert LocalAnalyticsHandler._display_models([{"model_name": "claude", "cost_usd": 0.3}], "claude") == "claude"


def test_display_models_falls_back_when_no_per_model_rows():
    assert LocalAnalyticsHandler._display_models([], "claude-sonnet-5") == "claude-sonnet-5"


def _two_model_catalog():
    """The runtime catalog with claude-haiku and claude-opus-5 at different cache-read prices."""
    models = []
    for name, cache_read in (("claude-haiku", 0.1e-6), ("claude-opus-5", 0.5e-6)):
        model = MagicMock()
        model.base_name = name
        model.deployment_name = name
        model.cost = CostConfig(input=1e-6, output=5e-6, cache_read_input_token_cost=cache_read)
        models.append(model)
    return patch(
        "codemie.service.analytics.handlers.coding_agent_pricing.llm_service.get_all_llm_model_info",
        return_value=models,
    )


@pytest.mark.asyncio
async def test_get_sessions_prices_cache_reads_per_model_and_lists_all_models():
    cost_facts = [{**BASE_COST_ROW, "model_name": "claude-haiku", "cache_read_tokens": 100_000, "cost_usd": 0.8}]
    model_costs = [
        {"session_id": "s1", "model_name": "claude-haiku", "input_tokens": 10_000, "cache_read_tokens": 70_000},
        {"session_id": "s1", "model_name": "claude-opus-5", "input_tokens": 10_000, "cache_read_tokens": 30_000},
    ]
    repo = MagicMock()
    repo.get_session_cost_facts = AsyncMock(return_value=cost_facts)
    repo.get_session_page = page_reader(cost_facts)
    with_window_reads(repo)
    repo.get_turns_by_session = AsyncMock(return_value=[])
    repo.get_tool_success_by_session = AsyncMock(return_value=[])
    repo.get_lines_by_session = AsyncMock(return_value=[])
    repo.get_session_model_costs = AsyncMock(return_value=model_costs)
    repo.get_skill_names_by_session = AsyncMock(return_value=[])
    handler = LocalAnalyticsHandler(bounded_to_itself(repo))

    with _two_model_catalog():
        data, pricer, _ = await handler.get_sessions(
            make_filter(), page=0, per_page=20, sort_by="start_time", search=None
        )
    session = data["sessions"][0]

    # haiku 0.01 + 0.007, opus 0.01 + 0.015: cost 0.042, cache reads 0.022 -> 52.4 %
    assert session["cost_usd"] == pytest.approx(0.042)
    assert session["bloat_pct"] == 52.4
    assert session["model_name"] == "claude-haiku, claude-opus-5"
    assert pricer.unpriced == []


def _opus_events() -> list[dict]:
    """The ticket's Opus 5.5 session split over three api_request events; their sums equal _OPUS_ROW."""
    splits = (
        (124, 22_161, 13_452_179, 629_636),
        (125, 22_161, 13_452_179, 629_636),
        (125, 22_161, 13_452_180, 629_636),
    )
    return [
        {
            "timestamp": datetime(2026, 1, 1, 0, i),
            "event_type": "api_request",
            "model_name": "claude-opus-5-5",
            "input_tokens": inp,
            "output_tokens": out,
            "cache_read_tokens": read,
            "cache_creation_tokens": write,
            "cache_creation_5m_tokens": None,
            "cache_creation_1h_tokens": write,
            "tool_name": "",
        }
        for i, (inp, out, read, write) in enumerate(splits)
    ]


def _priced_detail_handler(model_rows, events=(), dispatches=()):
    handler = make_detail_handler({"session_id": "s1", "started_at": datetime(2026, 1, 1), "duration_ms": 600_000})
    handler._repo.get_session_detail_model_costs = AsyncMock(return_value=model_rows)
    handler._repo.get_session_detail_events = AsyncMock(return_value=list(events))
    handler._repo.get_session_detail_dispatches = AsyncMock(return_value=list(dispatches))
    return handler


@pytest.mark.asyncio
async def test_get_session_detail_prices_the_session_and_each_event_at_the_catalog():
    handler = _priced_detail_handler([_OPUS_ROW], events=_opus_events())

    detail, unpriced, catalog_source = await _priced(handler.get_session_detail("s1", scope=DETAIL_SCOPE))

    assert detail["cost_usd"] == pytest.approx(26.96510036, abs=1e-9)
    assert detail["cache_read_cost_usd"] == pytest.approx(8.87843836, abs=1e-9)
    assert detail["bloat_pct"] == 32.9
    assert sum(e["cost_usd"] for e in detail["events"]) == pytest.approx(26.96510036, abs=1e-9)
    assert detail["dispatches"][0]["cost_usd"] == pytest.approx(26.96510036, abs=1e-9)
    assert detail["model_name"] == "claude-opus-5-5"
    assert detail["cost_available"] is True
    assert (unpriced, catalog_source) == ([], "litellm")


@pytest.mark.asyncio
async def test_get_session_detail_serves_request_cache_writes_and_tool_successes_of_every_tool():
    """EPMCDME-16609 / 16741: an api_request carries its cache-write tokens (a tool_call none), and the success
    total is the scalars' one over all tools, not the sum of the listed rows."""
    events = _opus_events() + [
        {**_opus_events()[0], "event_type": "tool_call", "model_name": "", "tool_name": "Read"},
    ]
    handler = _priced_detail_handler([_OPUS_ROW], events=events)
    handler._repo.get_session_detail_scalars = AsyncMock(
        return_value=[{"tool_call_count": 28, "tool_calls_success": 24}]
    )
    handler._repo.get_session_detail_tools = AsyncMock(
        return_value=[{"tool_name": "Read", "call_count": 20, "success_count": 16}]
    )

    detail, _, _ = await _priced(handler.get_session_detail("s1", scope=DETAIL_SCOPE))

    assert [e["cache_creation_tokens"] for e in detail["events"]] == [629_636, 629_636, 629_636, None]
    assert (detail["tool_call_count"], detail["tool_calls_success"], detail["tool_success_rate"]) == (28, 24, 85.7)
    assert [(t["call_count"], t["success_count"], t["success_rate"]) for t in detail["tools"]] == [(20, 16, 80.0)]


@pytest.mark.asyncio
async def test_get_session_detail_tokens_include_totals_only_subagent_rows():
    """A session whose cost_daily rows are absent (cost query empty) but whose subagent totals rows exist: the
    detail token columns are the per-model sums, the same figures the list reports."""
    handler = _priced_detail_handler(
        [
            {"model_name": "claude-opus-5-5", "input_tokens": 1000, "output_tokens": 200, "cache_read_tokens": 300},
            {
                "model_name": "claude-haiku-4-5",
                "input_tokens": 20,
                "output_tokens": 5,
                "cache_read_tokens": 7,
                "cache_creation_tokens": 50,
            },
        ]
    )
    handler._repo.get_session_detail_cost = AsyncMock(return_value=[{}])

    detail, _, _ = await _priced(handler.get_session_detail("s1", scope=DETAIL_SCOPE))

    assert (
        detail["input_tokens"],
        detail["output_tokens"],
        detail["cache_read_tokens"],
        detail["cache_creation_tokens"],
    ) == (1020, 205, 307, 50)
    session_lane = detail["dispatches"][0]
    assert (session_lane["input_tokens"], session_lane["cache_creation_tokens"]) == (1020, 50)


@pytest.mark.asyncio
async def test_get_session_detail_without_model_rows_reports_cost_available_false():
    handler = _priced_detail_handler([])

    detail, _, _ = await _priced(handler.get_session_detail("s1", scope=DETAIL_SCOPE))

    assert detail["cost_usd"] == 0.0
    assert detail["cost_available"] is False


@pytest.mark.asyncio
async def test_get_session_detail_with_only_an_unpriced_model_reports_cost_available_false():
    mystery = {"model_name": UNPRICED_MODEL, "input_tokens": 1_000, "cache_read_tokens": 100_000}
    handler = _priced_detail_handler([mystery])

    detail, unpriced, _ = await _priced(handler.get_session_detail("s1", scope=DETAIL_SCOPE))

    assert unpriced == [UNPRICED_MODEL]
    assert detail["cost_usd"] == 0.0
    assert detail["cost_available"] is False


@pytest.mark.asyncio
async def test_get_session_detail_with_a_priced_and_an_unpriced_model_reports_cost_available_true():
    mystery = {"model_name": UNPRICED_MODEL, "input_tokens": 1_000, "cache_read_tokens": 100_000}
    handler = _priced_detail_handler([_OPUS_ROW, mystery])

    detail, _, _ = await _priced(handler.get_session_detail("s1", scope=DETAIL_SCOPE))

    assert detail["cost_available"] is True


@pytest.mark.asyncio
async def test_get_session_detail_unpriced_model_contributes_zero_without_breaking_total():
    """A model absent from the runtime catalog among the session's model rows prices to $0.0, is reported
    in the unpriced list, and the priced model keeps its money."""
    mystery = {
        "model_name": "definitely-not-a-real-model-xyz-9999",
        "input_tokens": 1_000,
        "cache_read_tokens": 100_000,
    }
    handler = _priced_detail_handler([_OPUS_ROW, mystery])

    detail, unpriced, _ = await _priced(handler.get_session_detail("s1", scope=DETAIL_SCOPE))

    assert unpriced == ["definitely-not-a-real-model-xyz-9999"]
    assert detail["cost_usd"] == pytest.approx(26.96510036, abs=1e-9)
    assert detail["cache_read_cost_usd"] == pytest.approx(8.87843836, abs=1e-9)


@pytest.mark.asyncio
async def test_get_session_detail_resolves_bedrock_ids_in_detail_money_events_and_header():
    """Raw Bedrock ids price at the catalog rates and show once under the catalog name; unknown ids stay unpriced."""
    haiku = MagicMock()
    haiku.base_name, haiku.deployment_name = "claude-haiku-4-5-20251001", None
    haiku.cost = CostConfig(input=1e-6, output=5e-6, cache_read_input_token_cost=1e-7)
    raw = "anthropic.claude-haiku-4-5-20251001-v1:0"
    unknown = "anthropic.claude-unknown-9-20251001-v1:0"
    rows = [
        _models_row(model_name=raw, input_tokens=796, output_tokens=23),
        _models_row(model_name="converse/eu." + raw, cache_read_tokens=1_000),
        _models_row(model_name="claude-haiku-4-5-20251001", input_tokens=10),
        _models_row(model_name=unknown, input_tokens=5),
    ]
    event = {**_opus_events()[0], "model_name": raw, "input_tokens": 796, "output_tokens": 23, "cache_read_tokens": 0}
    event |= {"cache_creation_tokens": 0, "cache_creation_1h_tokens": None}
    handler = _priced_detail_handler(rows, events=[event])
    _, catalog_source = _ticket_catalog()
    with (
        patch(
            "codemie.service.analytics.handlers.coding_agent_pricing.llm_service.get_all_llm_model_info",
            return_value=[haiku],
        ),
        catalog_source,
    ):
        detail, unpriced, _ = await handler.get_session_detail("s1", scope=DETAIL_SCOPE)

    assert detail["cost_usd"] == pytest.approx(0.000911 + 1_000 * 1e-7 + 10 * 1e-6, abs=1e-12)
    assert detail["cache_read_cost_usd"] == pytest.approx(1_000 * 1e-7, abs=1e-12)
    assert detail["events"][0]["cost_usd"] == pytest.approx(0.000911, abs=1e-12)
    assert detail["events"][0]["model_name"] == "claude-haiku-4-5-20251001"
    assert (
        detail["model_name"] == f"claude-haiku-4-5-20251001, {unknown.removeprefix('anthropic.').removesuffix('-v1:0')}"
    )
    assert unpriced == ["claude-unknown-9-20251001"]


def _models_row(**counts) -> dict:
    return {
        "model_name": counts.pop("model_name"),
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_creation_5m_tokens": None,
        "cache_creation_1h_tokens": None,
    } | counts


@pytest.mark.asyncio
async def test_get_session_detail_prices_each_dispatch_from_its_per_model_sums():
    two_models = _dispatch_row(subagent_type="Explore", offset_s=5) | {
        "subagent_duration_ms": 1000,
        "models": [
            _models_row(model_name="claude-opus-5-5", input_tokens=1_000),
            _models_row(model_name="claude-haiku-4-5", input_tokens=1_000),
        ],
    }
    # the repository already splits a shared fallback window's tokens by request_share before they reach here
    # (get_session_detail_dispatches), so a two-way split shows up as half the tokens, not a separate divisor
    shared = {"models": [_models_row(model_name="claude-opus-5-5", input_tokens=500)]}
    tied = [
        _dispatch_row(skill_name="lint", offset_s=20) | shared,
        _dispatch_row(skill_name="test", offset_s=20) | shared,
    ]
    handler = _priced_detail_handler([], dispatches=[two_models, *tied])

    detail, unpriced, _ = await _priced(handler.get_session_detail("s1", scope=DETAIL_SCOPE))

    costs = {d["label"]: d["cost_usd"] for d in detail["dispatches"][1:]}
    assert costs == {
        "agent · Explore": pytest.approx(0.0054, abs=1e-12),
        "skill · lint": pytest.approx(0.0022, abs=1e-12),
        "skill · test": pytest.approx(0.0022, abs=1e-12),
    }
    assert unpriced == []


@pytest.mark.asyncio
async def test_get_session_detail_edge_events_and_a_dispatch_without_requests():
    unsplit = _opus_events()[0] | {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 1_000,
        "cache_creation_5m_tokens": None,
        "cache_creation_1h_tokens": None,
    }
    no_model = unsplit | {"model_name": "", "cache_creation_tokens": 0, "output_tokens": 5}
    idle = _dispatch_row(subagent_type="Plan", offset_s=5) | {"subagent_duration_ms": 1000, "models": []}
    handler = _priced_detail_handler([], events=[unsplit, no_model], dispatches=[idle])

    detail, unpriced, _ = await _priced(handler.get_session_detail("s1", scope=DETAIL_SCOPE))

    assert [e["cost_usd"] for e in detail["events"]] == [pytest.approx(0.0055, abs=1e-12), 0.0]
    assert detail["dispatches"][1]["cost_usd"] is None
    assert unpriced == [""]


def _dispatch_row(
    *, subagent_type="", skill_name="", duration_ms=9, real_duration_ms=152845, offset_s=10, dispatch_offset_s=None
):
    return {
        "subagent_type": subagent_type,
        "skill_name": skill_name,
        "span_start": datetime(2026, 1, 1) + timedelta(seconds=offset_s),
        "dispatch_start": datetime(2026, 1, 1)
        + timedelta(seconds=offset_s if dispatch_offset_s is None else dispatch_offset_s),
        "duration_ms": duration_ms,
        "real_duration_ms": real_duration_ms,
        "cost_usd": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }


def test_skill_dispatch_uses_real_duration_ms():
    """Skill duration_ms must come from real_duration_ms (interaction end minus skill start), not tool execution time."""
    rows = [_dispatch_row(skill_name="codemie:codemie-analytics", duration_ms=9, real_duration_ms=152845)]
    result = LocalAnalyticsHandler._build_dispatches(rows, datetime(2026, 1, 1, 0, 0, 0), 200000, 1.0)
    skill = next(d for d in result if d["kind"] == "skill")
    assert skill["duration_ms"] == 152845


def test_forked_skill_dispatch_run_is_clamped_to_the_session_and_null_while_running():
    """A forked skill's row takes its fork's run: cut at the session end, and unknown (None) while it has not stopped."""
    rows = [
        _dispatch_row(skill_name="probe-fork", real_duration_ms=152845),
        _dispatch_row(skill_name="probe-skill", real_duration_ms=None),
    ]
    result = LocalAnalyticsHandler._build_dispatches(rows, datetime(2026, 1, 1, 0, 0, 0), 160000, 1.0)
    durations = {d["label"]: d["duration_ms"] for d in result if d["kind"] == "skill"}
    assert durations == {"skill · probe-fork": 150000, "skill · probe-skill": None}


def test_a_negative_skill_run_is_unknown_not_a_bar():
    """A skill row whose run cannot be measured (a negative value) shows no duration instead of a bar backwards."""
    rows = [_dispatch_row(skill_name="probe-skill", real_duration_ms=-1792564820149)]
    result = LocalAnalyticsHandler._build_dispatches(rows, datetime(2026, 1, 1, 0, 0, 0), 160000, 1.0)
    assert next(d for d in result if d["kind"] == "skill")["duration_ms"] is None


def test_agent_dispatch_uses_subagent_run_duration_ms():
    """Agent duration_ms is the subagent's real run time (launch to its stop), not the Agent tool-execution span."""
    rows = [_dispatch_row(subagent_type="Explore", duration_ms=6, offset_s=25) | {"subagent_duration_ms": 64500}]
    result = LocalAnalyticsHandler._build_dispatches(rows, datetime(2026, 1, 1, 0, 0, 0), 160000, 1.0)
    agent = next(d for d in result if d["kind"] == "agent")
    assert agent["duration_ms"] == 64500


def test_agent_dispatch_without_subagent_stop_has_null_duration():
    """A subagent with no stop event (session cut off mid-dispatch) reports null, never the tool span or 0."""
    rows = [_dispatch_row(subagent_type="Explore", duration_ms=6, offset_s=25) | {"subagent_duration_ms": None}]
    result = LocalAnalyticsHandler._build_dispatches(rows, datetime(2026, 1, 1, 0, 0, 0), 160000, 1.0)
    agent = next(d for d in result if d["kind"] == "agent")
    assert agent["duration_ms"] is None


def test_skill_dispatch_ignores_subagent_duration_ms():
    rows = [
        _dispatch_row(skill_name="codemie:codemie-analytics", real_duration_ms=152845) | {"subagent_duration_ms": 1}
    ]
    result = LocalAnalyticsHandler._build_dispatches(rows, datetime(2026, 1, 1, 0, 0, 0), 200000, 1.0)
    skill = next(d for d in result if d["kind"] == "skill")
    assert skill["duration_ms"] == 152845


def test_forked_skill_command_dispatch_uses_real_duration_ms():
    """A forked skill (skill-tagged agent.subagent.start) is a "command"-kind row whose real_duration_ms
    mirrors subagent_duration_ms at the SQL layer; the handler reads
    real_duration_ms for this kind unchanged, so duration_ms must reflect the forked skill's own run
    time, not render as the free, instant row a main-thread slash command legitimately is."""
    rows = [
        _dispatch_row(skill_name="code-review", duration_ms=0, real_duration_ms=180_000)
        | {"is_slash_command": True, "subagent_duration_ms": 180_000}
    ]
    result = LocalAnalyticsHandler._build_dispatches(rows, datetime(2026, 1, 1, 0, 0, 0), 300_000, 3.5)
    command = next(d for d in result if d["kind"] == "command")
    assert command["duration_ms"] == 180_000


def test_main_thread_slash_command_dispatch_stays_instant():
    """A main-thread slash command (no fork, no agentId) has real_duration_ms 0 and no
    subagent_duration_ms at the SQL layer: it must still render as the free, instant row it is."""
    rows = [
        _dispatch_row(skill_name="code-review", duration_ms=0, real_duration_ms=0)
        | {"is_slash_command": True, "subagent_duration_ms": None}
    ]
    result = LocalAnalyticsHandler._build_dispatches(rows, datetime(2026, 1, 1, 0, 0, 0), 300_000, 0.0)
    command = next(d for d in result if d["kind"] == "command")
    assert command["duration_ms"] == 0


@pytest.mark.parametrize("real_duration_ms", [0, 4000])
def test_a_slash_command_at_the_session_end_keeps_its_duration(real_duration_ms):
    """A command dispatched at the session's last event cannot be clamped: it keeps its known duration, 0 included."""
    rows = [
        _dispatch_row(skill_name="code-review", duration_ms=0, real_duration_ms=real_duration_ms, offset_s=300)
        | {"is_slash_command": True}
    ]
    result = LocalAnalyticsHandler._build_dispatches(rows, datetime(2026, 1, 1, 0, 0, 0), 300_000, 0.0)
    command = next(d for d in result if d["kind"] == "command")
    assert command["duration_ms"] == real_duration_ms


def test_a_skill_in_a_zero_length_session_keeps_its_duration():
    rows = [_dispatch_row(skill_name="probe-skill", real_duration_ms=2500, offset_s=0)]
    result = LocalAnalyticsHandler._build_dispatches(rows, datetime(2026, 1, 1, 0, 0, 0), 0, 0.0)
    assert next(d for d in result if d["kind"] == "skill")["duration_ms"] == 2500


def test_subtypes_count_only_the_agent_rows_the_reader_returns():
    """Denied Agent calls never reach the handler: the reader gates them.
    Subtype counts come from the rows the handler is given, so only executed Agent rows count."""
    rows = [
        _dispatch_row(subagent_type="Explore", duration_ms=53235, real_duration_ms=130460, offset_s=25),
        _dispatch_row(subagent_type="Plan", duration_ms=1000, real_duration_ms=2000, offset_s=40),
    ]
    assert _count_dispatch_subtypes(rows) == {"Explore": 1, "Plan": 1}


def _users_repo(user_rows, avg=None):
    repo = MagicMock()
    install_users_reads(repo, user_rows, avg=avg)
    return bounded_to_itself(repo)


@pytest.mark.asyncio
async def test_get_users_reports_the_windows_average_duration():
    """The reader's window mean (unknown durations skipped there) is the response figure, whatever the page."""
    repo = _users_repo([{"developer_name": "dev"}], avg=2000)
    result, _ = await LocalAnalyticsHandler(repo).get_users(make_filter(), page=None, per_page=None)
    assert result["avg_session_duration_ms"] == 2000


@pytest.mark.asyncio
async def test_get_users_avg_duration_none_when_no_known_duration():
    repo = _users_repo([])
    result, _ = await LocalAnalyticsHandler(repo).get_users(make_filter(), page=None, per_page=None)
    assert result["avg_session_duration_ms"] is None


def test_iso_attaches_utc_to_naive_datetime():
    """A naive datetime must serialize with an explicit UTC offset, same wall-clock value."""
    result = _iso(datetime(2026, 9, 30, 12, 28, 8, 937211))
    assert result == "2026-09-30T12:28:08.937211+00:00"


def test_iso_leaves_aware_datetime_unchanged():
    """An already-aware datetime (including a non-UTC offset) must not be re-converted."""
    aware = datetime(2026, 9, 30, 14, 28, 8, tzinfo=timezone(timedelta(hours=2)))
    result = _iso(aware)
    assert result == aware.isoformat() == "2026-09-30T14:28:08+02:00"


def test_iso_date_passthrough_unaffected():
    """A plain date value (day buckets) has no time-of-day — it must serialize unchanged."""
    assert _iso(date(2026, 9, 30)) == "2026-09-30"


def test_iso_none_still_returns_empty_string():
    """None must keep falling through to the non-datetime/date branch, unchanged by this fix."""
    assert _iso(None) == ""


@pytest.mark.asyncio
async def test_get_sessions_start_time_carries_utc_offset_for_naive_started_at():
    """Sessions-list start_time must carry an explicit UTC offset even when the repository returns a naive started_at."""
    row = {**BASE_COST_ROW, "repository": "my-repo", "branch": "main"}
    handler = make_handler(cost_facts=[row])
    data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)
    session = data["sessions"][0]
    assert session["start_time"] == "2026-01-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_rows_with_developer_name_also_carry_user_email() -> None:
    """user, cost-by-user, session and detail rows carry user_email equal to developer_name."""
    from codemie.rest_api.models.cli_analytics import (
        LocalAnalyticsCostByUserRow,
        LocalAnalyticsSessionDetail,
        LocalAnalyticsSessionRow,
        LocalAnalyticsUserRow,
    )

    email = "dev@example.com"
    repo = MagicMock()
    install_users_reads(repo, [{"developer_name": email, "session_count": 1}])
    for name in (
        "get_turns_by_session",
        "get_file_facts_by_session",
        "get_tool_success_by_session",
        "get_session_durations",
        "get_cost_kpis",
        "get_model_breakdown",
        "get_session_model_costs",
        "get_session_funding_costs",
        "get_session_detail_cost",
        "get_session_detail_model_costs",
        "get_session_detail_scalars",
        "get_session_detail_tools",
        "get_session_detail_events",
        "get_session_detail_dispatches",
        "get_skill_names_by_session",
        "get_lines_by_session",
        "get_class_lists",
    ):
        setattr(repo, name, AsyncMock(return_value=[]))
    repo.get_cost_by_user = AsyncMock(return_value=[{"developer_name": email}])
    repo.get_cost_money = fake_cost_money([])
    repo.get_session_cost_facts = AsyncMock(return_value=[{**BASE_COST_ROW, "developer_name": email}])
    repo.get_session_page = page_reader([BASE_COST_ROW])
    with_window_reads(repo)
    repo.get_session_detail_meta = AsyncMock(
        return_value=[
            {
                "session_id": "s1",
                "developer_name": email,
                "started_at": datetime(2026, 1, 1),
                "duration_ms": 0,
            }
        ]
    )
    handler = LocalAnalyticsHandler(bounded_to_itself(repo))

    users, _ = await handler.get_users(make_filter(), page=None, per_page=None)
    cost, _ = await handler.get_cost(make_filter())
    sessions, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)
    detail, _, _ = await handler.get_session_detail("s1", scope=DETAIL_SCOPE)

    dumped = [
        LocalAnalyticsUserRow(**users["rows"][0]).model_dump(by_alias=True),
        LocalAnalyticsCostByUserRow(**cost["cost_by_user"][0]).model_dump(by_alias=True),
        LocalAnalyticsSessionRow(**sessions["sessions"][0]).model_dump(by_alias=True),
        LocalAnalyticsSessionDetail(**detail).model_dump(by_alias=True),
    ]
    for row in dumped:
        assert row["developer_name"] == email
        assert row["user_email"] == email


def test_ctx_per_call_helper_divides_and_zero_guards():
    from codemie.service.analytics.handlers.cli_analytics_handler import _ctx_per_call

    assert _ctx_per_call(1000, 10) == 100.0
    assert _ctx_per_call(1000, 0) == 0.0  # zero-guard: api_call_count == 0 must not raise
    assert _ctx_per_call(0, 5) == 0.0


@pytest.mark.asyncio
async def test_overview_avg_context_per_call_divides_by_api_call_count():
    """One turn can trigger many API calls (tool use); turns must not be the divisor."""
    repo = MagicMock()
    repo.get_cost_kpis = AsyncMock(
        return_value=[
            {
                "total_sessions": 1,
                "total_cost_usd": 1.0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_cache_read_tokens": 1000,
                "total_cache_creation_tokens": 0,
                "total_api_calls": 10,
            }
        ]
    )
    repo.get_model_breakdown = AsyncMock(return_value=[])
    repo.get_lines_totals = AsyncMock(return_value=[])
    repo.get_lines_daily = AsyncMock(return_value=[])
    repo.get_turns_by_session = AsyncMock(return_value=[{"session_id": "s1", "turns": 1}])
    repo.get_file_facts_by_session = AsyncMock(return_value=[])
    repo.get_tool_success_by_session = AsyncMock(return_value=[])
    repo.get_session_durations = AsyncMock(return_value=[])
    repo.get_session_model_costs = AsyncMock(return_value=[])
    repo.get_session_funding_costs = AsyncMock(return_value=[])
    repo.get_class_lists = AsyncMock(return_value=[])
    repo.get_session_cost_facts = AsyncMock(return_value=[{**BASE_COST_ROW, "session_id": "s1"}])
    handler = LocalAnalyticsHandler(bounded_to_itself(repo))

    data, _, _ = await handler.get_overview(make_filter())

    # 1000 cache_read_tokens / 10 api calls = 100 — the old formula (/1 turn) would give 1000.
    assert data["kpis"]["avg_context_per_call"] == 100.0


@pytest.mark.asyncio
async def test_sessions_ctx_per_call_field_uses_api_call_count_and_sorts_by_it():
    s1 = {**BASE_COST_ROW, "session_id": "s1", "cache_read_tokens": 1000, "api_call_count": 200}
    s2 = {**BASE_COST_ROW, "session_id": "s2", "cache_read_tokens": 1000, "api_call_count": 20}
    repo = MagicMock()
    repo.get_session_cost_facts = AsyncMock(return_value=[s1, s2])
    # The reader orders the page (highest cache-read tokens per API call first); the handler keeps that order.
    repo.get_session_page = page_reader([s1, s2], ordered_ids=["s2", "s1"])
    with_window_reads(repo)
    # turns-based ratio would rank s1 (1000/10=100) above s2 (1000/100=10) — the opposite order.
    repo.get_turns_by_session = AsyncMock(
        return_value=[{"session_id": "s1", "turns": 10}, {"session_id": "s2", "turns": 100}]
    )
    repo.get_tool_success_by_session = AsyncMock(return_value=[])
    repo.get_lines_by_session = AsyncMock(return_value=[])
    repo.get_model_breakdown = AsyncMock(return_value=[])
    repo.get_skill_names_by_session = AsyncMock(return_value=[])
    repo.get_session_model_costs = AsyncMock(return_value=[])
    repo.get_session_funding_costs = AsyncMock(return_value=[])
    handler = LocalAnalyticsHandler(bounded_to_itself(repo))

    data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="ctx_per_call", search=None)

    rows = data["sessions"]
    sessions = {r["trace_id"]: r for r in rows}
    assert sessions["s1"]["ctx_per_call"] == 5.0  # 1000 / 200 api calls
    assert sessions["s2"]["ctx_per_call"] == 50.0  # 1000 / 20 api calls
    assert [r["trace_id"] for r in rows] == ["s2", "s1"]  # highest ctx_per_call first

    from codemie.rest_api.models.cli_analytics import LocalAnalyticsSessionRow

    # rows[0] is "s2" (highest ctx_per_call, sorted above) — its source row carries api_call_count=20.
    dumped = LocalAnalyticsSessionRow(**rows[0]).model_dump(by_alias=True)
    assert dumped["ctx_per_call"] == rows[0]["ctx_per_call"]
    assert dumped["api_call_count"] == 20


@pytest.mark.asyncio
async def test_efficiency_worst_session_and_avg_use_api_call_count():
    """Heavy tool use (many calls per turn) must not look less bloated than few calls."""
    s1 = {**BASE_COST_ROW, "session_id": "s1", "cache_read_tokens": 1000, "api_call_count": 10}
    s2 = {**BASE_COST_ROW, "session_id": "s2", "cache_read_tokens": 1000, "api_call_count": 100}
    repo = MagicMock()
    repo.get_cost_kpis = AsyncMock(
        return_value=[
            {
                "total_sessions": 2,
                "total_cost_usd": 2.0,
                "total_cache_read_tokens": 2000,
                "total_api_calls": 110,
            }
        ]
    )
    repo.get_session_model_costs = AsyncMock(return_value=[])
    repo.get_session_funding_costs = AsyncMock(return_value=[])
    repo.get_session_cost_facts = AsyncMock(return_value=[s1, s2])
    # turns-based ratio would make s2 (1000/5=200) "worse" than s1 (1000/100=10) — reversed.
    repo.get_turns_by_session = AsyncMock(
        return_value=[{"session_id": "s1", "turns": 100}, {"session_id": "s2", "turns": 5}]
    )
    repo.get_file_facts_by_session = AsyncMock(return_value=[])
    repo.get_lines_totals = AsyncMock(return_value=[])
    repo.get_lines_by_session = AsyncMock(return_value=[])
    repo.get_class_lists = AsyncMock(return_value=[])
    handler = LocalAnalyticsHandler(bounded_to_itself(repo))

    data, _ = await handler.get_efficiency(make_filter())

    kpis = data["kpis"]
    assert kpis["worst_session_trace_id"] == "s1"  # 1000/10=100 beats 1000/100=10
    assert kpis["avg_context_per_call"] == pytest.approx(2000 / 110)


def make_detail_handler(meta_row):
    repo = MagicMock()
    repo.get_session_detail_meta = AsyncMock(return_value=[meta_row])
    repo.get_session_detail_cost = AsyncMock(
        return_value=[
            {
                "model_name": "claude",
                "cost_usd": 1.0,
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
            }
        ]
    )
    repo.get_session_detail_scalars = AsyncMock(
        return_value=[
            {
                "turns": 1,
                "tool_call_count": 0,
                "lines_added": 0,
                "lines_removed": 0,
                "active_ms": 0,
                "agent_count": 0,
                "skill_count": 0,
            }
        ]
    )
    repo.get_session_detail_tools = AsyncMock(return_value=[])
    repo.get_session_detail_events = AsyncMock(return_value=[])
    repo.get_session_detail_dispatches = AsyncMock(return_value=[])
    repo.get_session_detail_model_costs = AsyncMock(return_value=[])
    repo.get_skill_names_by_session = AsyncMock(return_value=[{"skill_names": []}])
    return LocalAnalyticsHandler(bounded_to_itself(repo))


@pytest.mark.asyncio
async def test_session_detail_harness_name_is_humanized():
    handler = make_detail_handler({"session_id": "s1", "harness_service_name": "claude-code"})
    detail, _, _ = await handler.get_session_detail("s1", scope=DETAIL_SCOPE)
    assert detail["harness_name"] == "Claude Code"


@pytest.mark.asyncio
async def test_session_detail_classifies_a_hook_only_session_by_the_reader_skill_names():
    handler = make_detail_handler({"session_id": "s1"})
    handler._repo.get_skill_names_by_session = AsyncMock(
        return_value=[{"session_id": "s1", "skill_names": ["stlc-factory:exploratory"]}]
    )

    detail, _, _ = await handler.get_session_detail("s1", scope=DETAIL_SCOPE)

    assert detail["delivery_framework"] == "STLC Factory (QA factory)"


@pytest.mark.asyncio
async def test_session_detail_classifies_a_session_that_only_dispatched_a_factory_subagent():
    handler = make_detail_handler({"session_id": "s1"})
    handler._repo.get_skill_names_by_session = AsyncMock(
        return_value=[{"session_id": "s1", "skill_names": ["sdlc-factory:complexity-assessor"]}]
    )

    detail, _, _ = await handler.get_session_detail("s1", scope=DETAIL_SCOPE)

    assert detail["delivery_framework"] == "CodeMie AI Factory"


@pytest.mark.asyncio
async def test_session_detail_without_skill_names_is_pure_chat():
    handler = make_detail_handler({"session_id": "s1"})
    handler._repo.get_skill_names_by_session = AsyncMock(return_value=[{"session_id": "s1", "skill_names": []}])

    detail, _, _ = await handler.get_session_detail("s1", scope=DETAIL_SCOPE)

    assert detail["delivery_framework"] == "Pure chat"


@pytest.mark.asyncio
async def test_session_detail_places_an_agent_launched_before_the_first_hook():
    # EPMCDME-15506: the meta start is the activity range start (00:00:00), earlier than the session's
    # first hook (00:00:12). An agent launched at 00:00:05 is placed from that start, not dropped as
    # launched before the session.
    handler = make_detail_handler(
        {"session_id": "s1", "started_at": datetime(2026, 1, 1, 0, 0, 0), "duration_ms": 600_000}
    )
    rows = [_dispatch_row(subagent_type="Explore", offset_s=5) | {"subagent_duration_ms": 1000}]
    handler._repo.get_session_detail_dispatches = AsyncMock(return_value=rows)

    detail, _, _ = await handler.get_session_detail("s1", scope=DETAIL_SCOPE)

    agent = next(d for d in detail["dispatches"] if d["kind"] == "agent")
    assert agent["start_offset_ms"] == 5000
    assert agent["duration_ms"] == 1000


@pytest.mark.asyncio
async def test_session_detail_harness_name_unknown_when_unresolved():
    handler = make_detail_handler({"session_id": "s1", "harness_service_name": None})
    detail, _, _ = await handler.get_session_detail("s1", scope=DETAIL_SCOPE)
    assert detail["harness_name"] == "Unknown"


def test_agent_dispatch_starts_at_dispatch_start_not_span_start():
    # The bar starts when the subagent launched (dispatch_start), not when the Agent tool span began.
    rows = [_dispatch_row(subagent_type="Explore", duration_ms=6, offset_s=7, dispatch_offset_s=10)]
    rows[0] |= {"subagent_duration_ms": 1000}
    result = LocalAnalyticsHandler._build_dispatches(rows, datetime(2026, 1, 1, 0, 0, 0), 160000, 1.0)
    agent = next(d for d in result if d["kind"] == "agent")
    assert agent["start_offset_ms"] == 10000


def test_agent_run_is_clamped_to_the_session_end():
    # A subagent that stopped after the session ended must not extend past it: start + duration <= session.
    rows = [_dispatch_row(subagent_type="Explore", offset_s=25) | {"subagent_duration_ms": 200000}]
    result = LocalAnalyticsHandler._build_dispatches(rows, datetime(2026, 1, 1, 0, 0, 0), 160000, 1.0)
    agent = next(d for d in result if d["kind"] == "agent")
    assert agent["start_offset_ms"] == 25000
    assert agent["duration_ms"] == 135000
    assert agent["start_offset_ms"] + agent["duration_ms"] == 160000


def test_agent_launched_after_the_session_end_has_null_duration():
    rows = [_dispatch_row(subagent_type="Explore", offset_s=200) | {"subagent_duration_ms": 5000}]
    result = LocalAnalyticsHandler._build_dispatches(rows, datetime(2026, 1, 1, 0, 0, 0), 160000, 1.0)
    agent = next(d for d in result if d["kind"] == "agent")
    assert agent["duration_ms"] is None


def test_agent_launched_before_the_session_has_null_duration_and_a_zero_offset():
    # A launch before the session start cannot be placed on the timeline: no bar, and no negative offset.
    rows = [_dispatch_row(subagent_type="Explore", offset_s=-5) | {"subagent_duration_ms": 9000}]
    result = LocalAnalyticsHandler._build_dispatches(rows, datetime(2026, 1, 1, 0, 0, 0), 160000, 1.0)
    agent = next(d for d in result if d["kind"] == "agent")
    assert agent["start_offset_ms"] == 0
    assert agent["duration_ms"] is None


def test_agent_without_a_dispatch_start_has_null_duration():
    # With no known launch the bar cannot be placed, so no duration is shown at the session start.
    rows = [_dispatch_row(subagent_type="Explore") | {"dispatch_start": None, "subagent_duration_ms": 9000}]
    result = LocalAnalyticsHandler._build_dispatches(rows, datetime(2026, 1, 1, 0, 0, 0), 160000, 1.0)
    agent = next(d for d in result if d["kind"] == "agent")
    assert agent["duration_ms"] is None


def _agent_bars(rows: list[dict]) -> list[tuple[int, int]]:
    """Each agent bar as [start, end) in ms from the session start, in start order."""
    result = LocalAnalyticsHandler._build_dispatches(rows, datetime(2026, 1, 1, 0, 0, 0), 160000, 1.0)
    agents = [d for d in result if d["kind"] == "agent"]
    return sorted((d["start_offset_ms"], d["start_offset_ms"] + d["duration_ms"]) for d in agents)


def test_parallel_agents_render_as_overlapping_bars():
    # EPMCDME-15512 AC3: two subagents launched together overlap on the Timeline (session 52d226aa's shape),
    # so the second bar starts before the first one ends; not dots from the 3-6 ms tool-execution span.
    rows = [
        _dispatch_row(subagent_type="Explore", dispatch_offset_s=7.661) | {"subagent_duration_ms": 57102},
        _dispatch_row(subagent_type="Explore", dispatch_offset_s=8.597) | {"subagent_duration_ms": 27502},
    ]
    (first_start, first_end), (second_start, second_end) = _agent_bars(rows)
    assert (first_start, first_end) == (7661, 64763)
    assert (second_start, second_end) == (8597, 36099)
    assert second_start < first_end


def test_sequential_agents_render_as_separate_bars():
    # EPMCDME-15512 AC3: a subagent called after the previous one stopped does not overlap it.
    rows = [
        _dispatch_row(subagent_type="Explore", dispatch_offset_s=13.524) | {"subagent_duration_ms": 20600},
        _dispatch_row(subagent_type="Explore", dispatch_offset_s=40) | {"subagent_duration_ms": 15000},
    ]
    (first_start, first_end), (second_start, second_end) = _agent_bars(rows)
    assert (first_start, first_end) == (13524, 34124)
    assert (second_start, second_end) == (40000, 55000)
    assert second_start >= first_end


# ── Catalog money on the aggregate surfaces ──

_MONEY_READS = (
    "get_cost_kpis",
    "get_model_breakdown",
    "get_lines_totals",
    "get_lines_daily",
    "get_turns_by_session",
    "get_file_facts_by_session",
    "get_tool_success_by_session",
    "get_session_durations",
    "get_session_cost_facts",
    "get_lines_by_session",
    "get_skill_names_by_session",
    "get_users_daily_activity",
    "get_users_last_active",
    "get_lines_by_user",
    "get_cost_by_user",
    "get_users",
    "get_class_lists",
)

# The ticket's sessions: Opus 5.5 with every cache write at 1h, Sonnet 4.6 likewise.
_OPUS_ROW = {
    "session_id": "s-opus",
    "model_name": "claude-opus-5-5",
    "normalized_model_name": "claude-opus-5-5",
    "input_tokens": 374,
    "output_tokens": 66_483,
    "cache_read_tokens": 40_356_538,
    "cache_creation_tokens": 1_888_908,
    "cache_creation_5m_tokens": None,
    "cache_creation_1h_tokens": 1_888_908,
}
_SONNET_ROW = {
    "session_id": "s-sonnet",
    "model_name": "us.anthropic.claude-sonnet-4-6",
    "normalized_model_name": "claude-sonnet-4-6",
    "input_tokens": 6,
    "output_tokens": 708,
    "cache_read_tokens": 107_215,
    "cache_creation_tokens": 41_644,
    "cache_creation_5m_tokens": 0,
    "cache_creation_1h_tokens": 41_644,
}


def _ticket_catalog(source: str = "litellm"):
    """The runtime catalog of the ticket: Opus 5.5 at 4.4 / 22 / 0.22 / 5.5 / 8.8 per MTok, Sonnet 4.6 × 0.75,
    Haiku 4.5 at 1 / 5 / 0.1 / 1.25 / 2."""
    models = []
    for name, prices in (
        ("claude-opus-5-5", (4.4e-6, 22e-6, 0.22e-6, 5.5e-6, 8.8e-6)),
        ("claude-sonnet-4-6", (3.3e-6, 16.5e-6, 0.33e-6, 4.125e-6, 6.6e-6)),
        ("claude-haiku-4-5", (1.0e-6, 5.0e-6, 0.10e-6, 1.25e-6, 2.0e-6)),
    ):
        model = MagicMock()
        model.base_name = name
        model.deployment_name = name
        model.cost = CostConfig(
            input=prices[0],
            output=prices[1],
            cache_read_input_token_cost=prices[2],
            cache_creation_input_token_cost=prices[3],
            cache_creation_input_token_cost_above_1hr=prices[4],
        )
        models.append(model)
    module = "codemie.service.analytics.handlers.coding_agent_pricing.llm_service"
    return (
        patch(f"{module}.get_all_llm_model_info", return_value=models),
        patch(f"{module}.catalog_source", return_value=source),
    )


def _money_handler(model_rows, **overrides):
    repo = MagicMock()
    for name in _MONEY_READS:
        setattr(repo, name, AsyncMock(return_value=[]))
    repo.get_session_model_costs = AsyncMock(return_value=model_rows)
    repo.get_session_funding_costs = AsyncMock(return_value=[])
    user_rows = overrides.pop("get_users", None)
    for name, rows in overrides.items():
        setattr(repo, name, AsyncMock(return_value=rows))
    repo.get_cost_models = AsyncMock(return_value=[])
    sessions = {sid: u["developer_name"] for u in overrides.get("get_cost_by_user", []) for sid in u["session_ids"]}
    repo.get_cost_money = fake_cost_money(model_rows, sessions or None)
    if user_rows is not None:  # the users' own money is priced from the sessions their rows list
        by_session = {sid: u["developer_name"] for u in user_rows for sid in u.get("session_ids", [])}
        install_users_reads(repo, user_rows, money=lambda: price_groups(model_rows, by_session))
    return LocalAnalyticsHandler(bounded_to_itself(repo))


async def _priced(call, source: str = "litellm"):
    catalog, catalog_source = _ticket_catalog(source)
    with catalog, catalog_source:
        return await call


@pytest.mark.asyncio
async def test_overview_and_efficiency_price_money_at_the_catalog():
    facts = [{**BASE_COST_ROW, "session_id": "s-opus", "cost_usd": 24.5}]
    handler = _money_handler([_OPUS_ROW], get_session_cost_facts=facts)

    overview, pricer, _ = await _priced(handler.get_overview(make_filter()))
    efficiency, _ = await _priced(handler.get_efficiency(make_filter()))

    for kpis in (overview["kpis"], efficiency["kpis"]):
        assert kpis["total_cost_usd"] == pytest.approx(26.96510036, abs=1e-9)
        assert kpis["cache_read_cost_usd"] == pytest.approx(8.87843836, abs=1e-9)
        assert kpis["bloat_pct"] == 32.9
    assert efficiency["dead_sessions"]["wasted_cost_usd"] == pytest.approx(26.96510036, abs=1e-9)
    assert (pricer.unpriced, pricer.catalog_source) == ([], "litellm")


@pytest.mark.asyncio
async def test_cost_page_per_user_and_per_model_sum_to_the_priced_total():
    handler = _money_handler(
        [_OPUS_ROW, _SONNET_ROW],
        get_cost_by_user=[
            {"developer_name": "alice@example.com", "session_ids": ["s-sonnet"]},
            {"developer_name": "bob@example.com", "session_ids": ["s-opus"]},
        ],
    )

    data, _ = await _priced(handler.get_cost(make_filter()))
    overview, _, _ = await _priced(handler.get_overview(make_filter()))

    assert data["kpis"]["total_cost_usd"] == pytest.approx(27.28703351, abs=1e-9)
    by_user = {r["developer_name"]: r["cost_usd"] for r in data["cost_by_user"]}
    assert [r["developer_name"] for r in data["cost_by_user"]] == ["bob@example.com", "alice@example.com"]
    assert by_user["alice@example.com"] == pytest.approx(0.32193315, abs=1e-9)
    assert sum(by_user.values()) == pytest.approx(27.28703351, abs=1e-9)
    by_model = {r["model_name"]: r["cost_usd"] for r in data["cost_by_model"]}
    assert by_model == {
        "claude-opus-5-5": pytest.approx(26.96510036, abs=1e-9),
        "claude-sonnet-4-6": pytest.approx(0.32193315, abs=1e-9),
    }
    # Opus 8.87843836 + Sonnet 0.03538095
    assert overview["kpis"]["cache_read_cost_usd"] == pytest.approx(8.91381931, abs=1e-9)


@pytest.mark.asyncio
async def test_users_are_ordered_and_paged_by_priced_money():
    # The producer cost orders carol > alice > bob; the catalog orders bob > alice > carol.
    model_rows = [
        {**_SONNET_ROW, "session_id": "s-alice"},
        {**_OPUS_ROW, "session_id": "s-bob"},
    ]
    user_rows = [
        {"developer_name": "carol@example.com", "cost_usd": 30.0, "session_ids": ["s-carol"]},
        {"developer_name": "alice@example.com", "cost_usd": 20.0, "session_ids": ["s-alice"]},
        {"developer_name": "bob@example.com", "cost_usd": 10.0, "session_ids": ["s-bob"]},
    ]
    handler = _money_handler(model_rows, get_users=user_rows)

    everyone, _ = await _priced(handler.get_users(make_filter(), page=None, per_page=None))
    second, _ = await _priced(handler.get_users(make_filter(), page=1, per_page=1))

    assert [(r["developer_name"], round(r["cost_usd"], 8)) for r in everyone["rows"]] == [
        ("bob@example.com", 26.96510036),
        ("alice@example.com", 0.32193315),
        ("carol@example.com", 0.0),
    ]
    assert [r["developer_name"] for r in second["rows"]] == ["alice@example.com"]
    assert second["total_count"] == 3


@pytest.mark.asyncio
async def test_repositories_and_users_report_a_model_without_config_as_unpriced():
    mystery = {**_OPUS_ROW, "model_name": "mystery-model", "normalized_model_name": "mystery-model"}
    facts = [{**BASE_COST_FACT, "session_id": "s-opus", "repository": "repo"}]
    handler = _money_handler(
        [_OPUS_ROW, mystery],
        get_session_cost_facts=facts,
        get_users=[{"developer_name": "bob@example.com", "session_ids": ["s-opus"]}],
    )

    repos, repo_pricer = await _priced(handler.get_repositories(make_filter(), 0, 20, False))
    users, user_pricer = await _priced(handler.get_users(make_filter(), page=None, per_page=None))

    assert repos["rows"][0]["cost_usd"] == pytest.approx(26.96510036, abs=1e-9)
    assert users["rows"][0]["cost_usd"] == pytest.approx(26.96510036, abs=1e-9)
    assert repo_pricer.unpriced == user_pricer.unpriced == ["mystery-model"]


@pytest.mark.asyncio
async def test_repositories_and_sessions_price_only_the_sessions_they_return():
    excluded = {**_SONNET_ROW, "session_id": "s-other", "model_name": "mystery-model"}
    model_rows = [_OPUS_ROW, excluded]
    repo_handler = _money_handler(
        model_rows, get_session_cost_facts=[{**BASE_COST_FACT, "session_id": "s-opus", "repository": "repo"}]
    )
    # The reader's search finds s-opus alone; the window also holds s-other, whose model is uncatalogued.
    session_handler = _money_handler(
        [_OPUS_ROW],
        get_session_cost_facts=[{**BASE_COST_ROW, "session_id": "s-opus", "prompt": "fix the parser"}],
        get_session_page=(1, ["s-opus"]),
        get_cost_models=["claude-opus-5-5", "mystery-model"],
        get_cost_page=(1, ["s-opus"], [("claude-opus-5-5", (True,) * 5), ("mystery-model", (True,) * 5)]),
    )

    repos, repo_pricer = await _priced(repo_handler.get_repositories(make_filter(), 0, 20, False, search="repo"))
    sessions, session_pricer, _ = await _priced(
        session_handler.get_sessions(make_filter(), 0, 20, "cost_usd", "parser")
    )

    assert [r["cost_usd"] for r in repos["rows"]] == [pytest.approx(26.96510036, abs=1e-9)]
    assert [(s["trace_id"], s["cost_usd"]) for s in sessions["sessions"]] == [
        ("s-opus", pytest.approx(26.96510036, abs=1e-9))
    ]
    assert session_handler._repo.get_cost_page.call_args.args[4] == "parser"
    # Unpriced models describe the whole filtered window, not only the listed page.
    assert (repo_pricer.unpriced, session_pricer.unpriced) == ([], ["mystery-model"])


@pytest.mark.asyncio
async def test_an_empty_selection_has_no_money():
    overview, pricer, _ = await _priced(_money_handler([]).get_overview(make_filter()))

    kpis = overview["kpis"]
    assert (kpis["total_cost_usd"], kpis["cache_read_cost_usd"], kpis["bloat_pct"]) == (0.0, 0.0, 0.0)
    assert pricer.unpriced == []


@pytest.mark.asyncio
async def test_a_row_without_a_model_is_unpriced_as_empty_and_the_yaml_source_is_reported():
    nameless = {**_SONNET_ROW, "model_name": "", "normalized_model_name": ""}
    handler = _money_handler([_SONNET_ROW, nameless])

    data, pricer = await _priced(handler.get_cost(make_filter()), source="yaml")

    assert data["kpis"]["total_cost_usd"] == pytest.approx(0.32193315, abs=1e-9)
    assert [r["model_name"] for r in data["cost_by_model"]] == ["claude-sonnet-4-6"]
    assert (pricer.unpriced, pricer.catalog_source) == ([""], "yaml")


_MARKUP = '<pasted_content id="b23a">\nStep 1: log in\n</pasted_content id="b23a">\n'
_CLEAN = "Step 1: log in"


async def _served_sessions(*rows):
    handler = make_handler(cost_facts=[{**BASE_COST_ROW, **r} for r in rows])
    data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="cost_usd", search=None)
    return data["sessions"]


def _efficiency_handler(row):
    repo = MagicMock()
    repo.get_cost_kpis = AsyncMock(return_value=[{"total_sessions": 1, "total_api_calls": 10}])
    repo.get_session_model_costs = AsyncMock(return_value=[])
    repo.get_session_funding_costs = AsyncMock(return_value=[])
    repo.get_session_cost_facts = AsyncMock(return_value=[row])
    repo.get_turns_by_session = AsyncMock(return_value=[])
    repo.get_file_facts_by_session = AsyncMock(return_value=[])
    repo.get_lines_totals = AsyncMock(return_value=[])
    repo.get_lines_by_session = AsyncMock(return_value=[])
    repo.get_class_lists = AsyncMock(return_value=[])
    return LocalAnalyticsHandler(bounded_to_itself(repo))


@pytest.mark.asyncio
async def test_a_named_session_is_served_under_its_name():
    (row,) = await _served_sessions({"prompt": "first words", "session_title": "Release plan"})
    assert row["prompt"] == "Release plan"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prompt",
    [
        "Run the smoke test",
        "Why does the title show <pasted_content id=b23a instead of my text?",  # a stray opener is typed text
    ],
)
async def test_an_unnamed_session_is_served_under_its_typed_first_prompt(prompt):
    (row,) = await _served_sessions({"prompt": prompt, "session_title": ""})
    assert row["prompt"] == prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("title", [None, "", "   ", "\u00a0"])
async def test_a_blank_or_missing_title_falls_back_to_the_first_prompt(title):
    (row,) = await _served_sessions({"prompt": "Run the smoke test", "session_title": title})
    assert row["prompt"] == "Run the smoke test"


@pytest.mark.asyncio
async def test_a_stored_first_prompt_that_still_holds_the_markup_is_served_clean():
    (row,) = await _served_sessions({"prompt": "Do this\n\n" + _MARKUP, "session_title": ""})
    assert "pasted_content" not in row["prompt"]
    assert row["prompt"] == "Do this\n\n\n" + _CLEAN


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("repository", "branch", "expected"),
    [
        ("my-repo", "feature-x", "my-repo"),
        ("", "feature-x", "feature-x"),
        (None, None, ""),
    ],
)
async def test_a_first_prompt_that_cleans_to_nothing_is_served_as_the_repository_then_the_branch(
    repository, branch, expected
):
    (row,) = await _served_sessions(
        {"prompt": '<pasted_content id="a', "session_title": "", "repository": repository, "branch": branch}
    )
    assert row["prompt"] == expected


@pytest.mark.asyncio
async def test_a_session_row_carries_no_first_prompt_key():
    (row,) = await _served_sessions({"prompt": "x", "session_title": "Name"})
    assert "first_prompt" not in row and "session_title" not in row


@pytest.mark.asyncio
async def test_session_detail_serves_the_name_when_set():
    handler = make_detail_handler({"session_id": "s1", "prompt": "first words", "session_title": "Release plan"})
    detail, _, _ = await handler.get_session_detail("s1", scope=DETAIL_SCOPE)
    assert detail["prompt"] == "Release plan"


@pytest.mark.asyncio
async def test_session_detail_serves_the_first_prompt_without_markup_when_unnamed():
    handler = make_detail_handler({"session_id": "s1", "prompt": "Do this\n\n" + _MARKUP, "session_title": None})
    detail, _, _ = await handler.get_session_detail("s1", scope=DETAIL_SCOPE)
    assert detail["prompt"] == "Do this\n\n\n" + _CLEAN


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("repository", "branch", "expected"),
    [
        ("my-repo", "feature-x", "my-repo"),
        ("", "feature-x", "feature-x"),
        (None, None, ""),
    ],
)
async def test_session_detail_serves_the_repository_then_the_branch_for_a_markup_only_first_prompt(
    repository, branch, expected
):
    handler = make_detail_handler(
        {"session_id": "s1", "prompt": '<pasted_content id="a">', "repository": repository, "branch": branch}
    )
    detail, _, _ = await handler.get_session_detail("s1", scope=DETAIL_SCOPE)
    assert detail["prompt"] == expected


@pytest.mark.asyncio
async def test_efficiency_worst_session_prompt_is_the_session_title_when_named():
    row = {**BASE_COST_ROW, "cache_read_tokens": 1000, "api_call_count": 10}
    row |= {"prompt": "Do this\n\n" + _MARKUP, "session_title": "Release plan"}

    data, _ = await _efficiency_handler(row).get_efficiency(make_filter())

    assert data["kpis"]["worst_session_prompt"] == "Release plan"


@pytest.mark.asyncio
async def test_efficiency_worst_session_prompt_is_the_cleaned_first_prompt_when_unnamed():
    row = {**BASE_COST_ROW, "cache_read_tokens": 1000, "api_call_count": 10}
    row |= {"prompt": "Do this\n\n" + _MARKUP, "session_title": ""}

    data, _ = await _efficiency_handler(row).get_efficiency(make_filter())

    assert data["kpis"]["worst_session_prompt"] == "Do this\n\n\n" + _CLEAN


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("repository", "branch", "expected"),
    [
        ("my-repo", "feature-x", "my-repo"),
        ("", "feature-x", "feature-x"),
        (None, None, None),
    ],
)
async def test_efficiency_worst_session_prompt_falls_back_to_the_repository_then_the_branch(
    repository, branch, expected
):
    row = {**BASE_COST_ROW, "cache_read_tokens": 1000, "api_call_count": 10}
    row |= {"prompt": '<pasted_content id="a', "session_title": "", "repository": repository, "branch": branch}

    data, _ = await _efficiency_handler(row).get_efficiency(make_filter())

    assert data["kpis"]["worst_session_prompt"] == expected


@pytest.mark.asyncio
async def test_efficiency_worst_session_prompt_is_truncated_after_cleaning():
    from codemie.service.analytics.handlers.cli_analytics_handler import _PROMPT_PREVIEW_LEN

    row = {**BASE_COST_ROW, "cache_read_tokens": 1000, "api_call_count": 10, "session_title": ""}
    row["prompt"] = _MARKUP + "y" * (_PROMPT_PREVIEW_LEN * 2)

    data, _ = await _efficiency_handler(row).get_efficiency(make_filter())

    worst = data["kpis"]["worst_session_prompt"]
    assert len(worst) == _PROMPT_PREVIEW_LEN and worst.startswith(_CLEAN)


# ── EPMCDME-16713: unknown lines are null, real zeros stay 0 ─────────────────


def test_nsum_is_none_only_when_every_input_is_none():
    from codemie.service.analytics.handlers.cli_analytics_handler import _nsum

    assert _nsum(None, None) is None
    assert _nsum() is None
    assert _nsum(None, 3) == 3
    assert _nsum(0, None) == 0
    assert _nsum(2, 3) == 5


def test_nnet_is_none_only_when_both_are_none():
    from codemie.service.analytics.handlers.cli_analytics_handler import _nnet

    assert _nnet(None, None) is None
    assert _nnet(0, 0) == 0
    assert _nnet(5, None) == 5
    assert _nnet(None, 2) == -2
    assert _nnet(5, 2) == 3


@pytest.mark.asyncio
async def test_sessions_net_lines_none_for_unknown_and_zero_for_known_zero():
    facts = [{**BASE_COST_ROW, "session_id": "unknown"}, {**BASE_COST_ROW, "session_id": "zero"}]
    handler = make_handler(cost_facts=facts)
    handler._repo.get_lines_by_session = AsyncMock(
        return_value=[{"session_id": "zero", "lines_added": 0, "lines_removed": 0}]
    )
    data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)
    by_id = {r["trace_id"]: r for r in data["sessions"]}
    assert by_id["unknown"]["net_lines"] is None
    assert by_id["zero"]["net_lines"] == 0


@pytest.mark.asyncio
async def test_session_detail_net_lines_none_when_scalars_null_and_zero_when_known():
    handler = make_detail_handler({"session_id": "s1"})
    handler._repo.get_session_detail_scalars = AsyncMock(
        return_value=[{"turns": 1, "lines_added": None, "lines_removed": None}]
    )
    detail, _, _ = await handler.get_session_detail("s1", scope=DETAIL_SCOPE)
    assert detail["net_lines"] is None

    known = make_detail_handler({"session_id": "s1"})  # fixture scalars: lines 0/0
    detail, _, _ = await known.get_session_detail("s1", scope=DETAIL_SCOPE)
    assert detail["net_lines"] == 0


@pytest.mark.asyncio
async def test_repositories_lines_unknown_partial_and_known():
    cost_facts = [
        {**BASE_COST_FACT, "session_id": "u1", "repository": "all-unknown", "branch": "b", "project_name": "p"},
        {**BASE_COST_FACT, "session_id": "m1", "repository": "mixed", "branch": "b1", "project_name": "p"},
        {**BASE_COST_FACT, "session_id": "m2", "repository": "mixed", "branch": "b2", "project_name": "p"},
        {**BASE_COST_FACT, "session_id": "k1", "repository": "known", "branch": "b", "project_name": "p"},
    ]
    handler = make_repo_handler(cost_facts)
    handler._repo.get_lines_by_session = AsyncMock(
        return_value=[
            {"session_id": "m1", "lines_added": 10, "lines_removed": 4},
            {"session_id": "k1", "lines_added": 0, "lines_removed": 0},
        ]
    )
    for include_branches in (True, False):
        rows = (await handler.get_repositories(make_filter(), 0, 50, include_branches=include_branches))[0]["rows"]
        by_repo = {r["repository"]: r for r in rows}
        assert (by_repo["all-unknown"]["lines_added"], by_repo["all-unknown"]["lines_removed"]) == (None, None)
        assert by_repo["all-unknown"]["net_lines"] is None
        assert (by_repo["mixed"]["lines_added"], by_repo["mixed"]["lines_removed"]) == (10, 4)
        assert by_repo["mixed"]["net_lines"] == 6
        assert (by_repo["known"]["lines_added"], by_repo["known"]["net_lines"]) == (0, 0)

    nested = (await handler.get_repositories(make_filter(), 0, 50, include_branches=True))[0]["rows"]
    mixed = next(r for r in nested if r["repository"] == "mixed")
    branches = {b["branch"]: b for b in mixed["branches"]}
    assert branches["b1"]["net_lines"] == 6
    assert branches["b2"]["lines_added"] is None and branches["b2"]["net_lines"] is None


# ── Users / overview / efficiency: unknown lines are null, not 0 ──


@pytest.mark.asyncio
async def test_users_net_lines_none_for_user_without_lines_row_and_zero_for_known_zero():
    repo = _users_repo(
        [
            {"developer_name": "none"},
            {"developer_name": "zero"},
            {"developer_name": "some"},
        ]
    )
    repo.get_lines_by_user = AsyncMock(
        return_value=[
            {"developer_name": "zero", "net_lines": 0},
            {"developer_name": "some", "net_lines": 7},
        ]
    )
    result, _ = await LocalAnalyticsHandler(repo).get_users(make_filter(), page=None, per_page=None)
    by_name = {r["developer_name"]: r["net_lines"] for r in result["rows"]}
    assert by_name == {"none": None, "zero": 0, "some": 7}


@pytest.mark.parametrize(
    ("totals", "expected"),
    [
        ([], None),
        ([{"lines_added": None, "lines_removed": None}], None),
        ([{"lines_added": 10, "lines_removed": 4}], 6),
        ([{"lines_added": 5, "lines_removed": None}], 5),
        ([{"lines_added": 0, "lines_removed": 0}], 0),
    ],
)
@pytest.mark.asyncio
async def test_overview_and_efficiency_kpi_net_lines_null_only_when_totals_unknown(totals, expected):
    handler = _money_handler([], get_lines_totals=totals)
    overview, _, _ = await handler.get_overview(make_filter())
    efficiency, _ = await handler.get_efficiency(make_filter())
    assert overview["kpis"]["net_lines"] == expected
    assert efficiency["kpis"] is not None
    assert efficiency["code_changes"]["net_lines"] == expected


@pytest.mark.asyncio
async def test_overview_daily_bucket_net_lines_none_unknown_and_zero_known():
    days = [
        {"day": date(2026, 9, 1), "net_lines": None, "nonzero": False},
        {"day": date(2026, 9, 2), "net_lines": 0, "nonzero": False},
        {"day": date(2026, 9, 3), "net_lines": 3, "nonzero": True},
    ]
    handler = _money_handler([], get_lines_daily=days)
    data, _, _ = await handler.get_overview(make_filter())
    assert [b["net_lines"] for b in data["daily_buckets"]] == [None, 0, 3]


@pytest.mark.asyncio
async def test_overview_data_as_of_uses_only_nonzero_days():
    days = [
        {"day": date(2026, 9, 1), "net_lines": 4, "nonzero": True},
        {"day": date(2026, 9, 5), "net_lines": 0, "nonzero": False},
    ]
    _, _, as_of = await _money_handler([], get_lines_daily=days).get_overview(make_filter())
    assert as_of == "2026-09-01"
    zero_only = [{"day": date(2026, 9, 5), "net_lines": 0, "nonzero": False}]
    _, _, as_of = await _money_handler([], get_lines_daily=zero_only).get_overview(make_filter())
    assert as_of is None


@pytest.mark.asyncio
async def test_efficiency_code_ids_exclude_zero_sum_known_sessions():
    facts = [{**BASE_COST_ROW, "session_id": sid} for sid in ("zero", "code", "none")]
    classes = [
        {"key": "zero", "subscription_types": ["codemie"], "project_origins": ["none"]},
        {"key": "code", "subscription_types": ["corporate"], "project_origins": ["profile"]},
        {"key": "none", "subscription_types": ["unknown"], "project_origins": ["linked_folder"]},
    ]
    handler = _money_handler(
        [],
        get_session_cost_facts=facts,
        get_class_lists=classes,
        get_lines_by_session=[
            {"session_id": "zero", "lines_added": 0, "lines_removed": 0},
            {"session_id": "code", "lines_added": 3, "lines_removed": 0},
        ],
    )
    data, _ = await handler.get_efficiency(make_filter())
    assert data["code_changes"]["subscription_types"] == ["corporate"]
    assert data["code_changes"]["project_origins"] == ["profile"]


@pytest.mark.asyncio
async def test_overview_total_sessions_is_read_from_get_cost_kpis_not_recomputed_from_the_sessions_list():
    """Overview's `total_sessions` must come from `repo.get_cost_kpis` verbatim, never be silently
    recomputed from the Sessions list's own row count (or vice versa): the two repo calls are
    independent queries, each already proven against a real database (see
    test_get_cost_kpis_and_get_users_count_a_costless_session_like_the_sessions_list, a live-Postgres
    test, for whether they actually agree on the same costless-inclusive universe). Deliberately mocking
    them to different counts here proves the handler reports each one as given, rather than papering
    over a disagreement by deriving one count from the other."""
    repo = MagicMock()
    repo.get_cost_kpis = AsyncMock(return_value=[{**BASE_COST_ROW, "total_sessions": 5, "total_cost_usd": 1.0}])
    repo.get_model_breakdown = AsyncMock(return_value=[])
    repo.get_lines_totals = AsyncMock(return_value=[{}])
    repo.get_lines_daily = AsyncMock(return_value=[])
    repo.get_turns_by_session = AsyncMock(return_value=[])
    repo.get_file_facts_by_session = AsyncMock(return_value=[])
    repo.get_tool_success_by_session = AsyncMock(return_value=[])
    repo.get_session_durations = AsyncMock(return_value=[])
    repo.get_session_model_costs = AsyncMock(return_value=[])
    repo.get_session_funding_costs = AsyncMock(return_value=[])
    repo.get_skill_names_by_session = AsyncMock(return_value=[])
    repo.get_lines_by_session = AsyncMock(return_value=[])
    repo.get_class_lists = AsyncMock(return_value=[])
    repo.get_session_cost_facts = AsyncMock(return_value=[BASE_COST_ROW])
    repo.get_session_page = page_reader([BASE_COST_ROW])
    with_window_reads(repo, [BASE_COST_ROW])
    handler = LocalAnalyticsHandler(bounded_to_itself(repo))

    overview, _, _ = await handler.get_overview(make_filter())
    sessions, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)

    assert overview["kpis"]["total_sessions"] == 5
    assert len(sessions["sessions"]) == 1


@pytest.mark.parametrize("search", ["", " ", "  \t\n"])
@pytest.mark.asyncio
async def test_a_blank_search_is_read_as_no_search(search):
    handler = make_handler(cost_facts=[{**BASE_COST_ROW, "session_id": "s7"}])
    repo = handler._repo
    await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=search)
    assert repo.get_session_page.call_args.args[4] is None


@pytest.mark.asyncio
async def test_a_failing_detail_statement_raises_itself_and_cancels_its_siblings_before_the_reader_is_released():
    handler = make_handler(cost_facts=[{**BASE_COST_ROW, "session_id": "s7"}])
    repo = handler._repo
    repo.get_session_page = AsyncMock(return_value=(1, ["s7"]))
    cancelled = []

    async def pending(*_args, **_kwargs):
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.append("turns")
            raise

    async def failing(*_args, **_kwargs):
        await asyncio.sleep(0)  # let the sibling start first
        raise TimeoutError("statement timeout")

    repo.get_turns_by_session = pending
    repo.get_lines_by_session = failing
    with pytest.raises(TimeoutError, match="statement timeout"):  # not an ExceptionGroup
        await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)
    assert cancelled == ["turns"]  # cancelled and awaited before get_sessions returned


# ── /cost on one bounded connection (EPMCDME-17051) ──


def _cg(keys, cost, sessions=1, unpriced=(), cache=0.0):
    return CostGroup(keys, cost, cache, sessions, tuple(unpriced), bool(unpriced))


class _CostReader:
    """The bounded reader of a /cost request: records the statements in order and answers from `money`."""

    def __init__(self, money, by_user=(), calls=None, hold=0.0):
        self.calls = [] if calls is None else calls
        self.exited = False
        self._hold = hold

        async def record(name, value):
            self.calls.append(name)
            if self._hold:
                await asyncio.sleep(self._hold)
            return value

        async def models(f):
            return await record("get_cost_models", ["claude-sonnet-4-6"])

        async def get_money(f, rates, buckets=True):
            self.rates = list(rates)
            return await record("get_cost_money", money)

        async def kpis(f):
            return await record("get_cost_kpis", [{"total_sessions": 2, "spend_sessions": 2}])

        async def classes(f, by="all"):
            return await record(f"get_class_lists:{by}", [])

        async def users(f, with_classes=True):
            return await record("get_cost_by_user", [{"developer_name": u} for u in by_user])

        async def materialize_sel(f):
            self.calls.append("materialize_sel")

        self.materialize_sel = materialize_sel
        self.get_cost_models, self.get_cost_money, self.get_cost_kpis = models, get_money, kpis
        self.get_class_lists, self.get_cost_by_user = classes, users

    def top_level(self):
        outer = MagicMock(spec=["bounded"])
        reader = self

        @asynccontextmanager
        async def bounded():
            try:
                yield reader
            finally:
                reader.exited = True

        outer.bounded = bounded
        return outer


def _cost_handler(money, by_user=(), hold=0.0):
    reader = _CostReader(money, by_user, hold=hold)
    return LocalAnalyticsHandler(reader.top_level()), reader


@pytest.mark.asyncio
async def test_cost_runs_in_one_bounded_connection_in_order():
    handler, reader = _cost_handler(GroupedMoney({}), by_user=["alice"])

    await _priced(handler.get_cost(make_filter()))

    assert reader.calls == [
        "materialize_sel",
        "get_cost_models",
        "get_cost_money",
        "get_cost_kpis",
        "get_class_lists:all",
        "get_class_lists:model",
        "get_cost_by_user",
    ]
    assert [r.model for r in reader.rates] == ["claude-sonnet-4-6"]
    assert reader.exited


@pytest.mark.asyncio
async def test_cost_without_the_class_lists_skips_their_statements_and_leaves_them_empty():
    handler, reader = _cost_handler(GroupedMoney({}), by_user=["alice"])

    data, _ = await handler.get_cost(make_filter(), include_class_lists=False)

    assert reader.calls == [
        "materialize_sel",
        "get_cost_models",
        "get_cost_money",
        "get_cost_kpis",
        "get_cost_by_user",
    ]
    assert data["kpis"]["subscription_types"] == [] and data["kpis"]["project_origins"] == []
    assert data["cost_by_user"][0]["subscription_types"] == []


@pytest.mark.asyncio
async def test_cost_without_the_buckets_asks_only_for_the_spend_groupings_and_zeroes_them():
    sonnet = "claude-sonnet-4-6"
    money = GroupedMoney(
        {
            G_TOTAL: [_cg({}, 5.0, 2)],
            G_USER: [_cg({"user": "alice"}, 5.0, 2)],
            G_MODEL: [_cg({"model": sonnet}, 5.0, 2)],
            G_ROUTE: [_cg({"funding_route": "api_based"}, 7.0, 3)],
            G_PERSONAL: [_cg({"personal_billed": True}, 2.0, 1)],
            G_MODEL_PERSONAL: [_cg({"model": "personal-only", "personal_billed": True}, 2.0, 1)],
        }
    )
    handler, reader = _cost_handler(money, by_user=["alice"])
    asked = []
    inner = reader.get_cost_money

    async def spy(f, rates, buckets=True):
        asked.append(buckets)
        return await inner(f, rates, buckets=buckets)

    reader.get_cost_money = spy

    full, _ = await handler.get_cost(make_filter())
    lean, _ = await handler.get_cost(make_filter(), include_buckets=False)

    assert asked == [True, False]
    assert full["models_without_spend"] and full["funding"]["api_based"]["cost_usd"] == 7.0
    assert lean["models_without_spend"] == []
    assert "funding" not in lean and "personal_billed" not in lean  # the response model fills the zero buckets
    # what the Cost tab reads is the same either way
    assert lean["kpis"]["total_cost_usd"] == full["kpis"]["total_cost_usd"] == 5.0
    assert [(r["developer_name"], r["cost_usd"]) for r in lean["cost_by_user"]] == [("alice", 5.0)]
    assert [(r["model_name"], r["cost_usd"]) for r in lean["cost_by_model"]] == [(sonnet, 5.0)]


def _rollup_cost_handler(answer):
    """A /cost handler whose repository answers the rollup read with `answer` and whose live reader records calls."""
    live = _CostReader(GroupedMoney({}), by_user=["live-user"])
    outer = live.top_level()
    outer.rollup_cost = AsyncMock(return_value=answer)
    return LocalAnalyticsHandler(outer), live, outer.rollup_cost


def _rollup_cost():
    return RollupCost(
        money=GroupedMoney(
            {
                G_TOTAL: [_cg({}, 6.0, 4)],
                G_USER: [_cg({"user": "alice"}, 4.0, 3), _cg({"user": "bob"}, 2.0, 1)],
                G_MODEL: [_cg({"model": "claude-sonnet-4-6"}, 6.0, 4)],
            }
        ),
        developers=["alice", "bob", "carol"],
        total_sessions=9,
        spend_sessions=4,
        total_tokens=1234,
    )


@pytest.mark.asyncio
async def test_cost_without_the_optional_parts_is_served_from_the_rollup_when_it_answers():
    handler, live, rollup = _rollup_cost_handler(_rollup_cost())

    data, _ = await handler.get_cost(make_filter(), include_class_lists=False, include_buckets=False)

    rollup.assert_awaited_once()
    assert live.calls == []  # no connection, no statement of the live path
    assert data["kpis"]["total_sessions"] == 9 and data["kpis"]["total_tokens"] == 1234
    assert data["kpis"]["total_cost_usd"] == 6.0 and data["kpis"]["avg_cost_per_session"] == 1.5
    assert [(r["developer_name"], r["cost_usd"]) for r in data["cost_by_user"]] == [
        ("alice", 4.0),
        ("bob", 2.0),
        ("carol", 0.0),  # listed, no spend figure
    ]
    assert [(r["model_name"], r["cost_usd"]) for r in data["cost_by_model"]] == [("claude-sonnet-4-6", 6.0)]


@pytest.mark.asyncio
async def test_cost_takes_the_live_statements_when_the_rollup_does_not_answer():
    handler, live, rollup = _rollup_cost_handler(None)

    await handler.get_cost(make_filter(), include_class_lists=False, include_buckets=False)

    rollup.assert_awaited_once()
    assert "get_cost_money" in live.calls and "get_cost_by_user" in live.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("flags", [{"include_class_lists": True}, {"include_buckets": True}, {}])
async def test_cost_with_an_optional_part_never_asks_the_rollup_which_holds_neither(flags):
    handler, live, rollup = _rollup_cost_handler(_rollup_cost())

    await handler.get_cost(make_filter(), **flags)

    rollup.assert_not_awaited()
    assert "get_cost_money" in live.calls


@pytest.mark.asyncio
async def test_a_window_the_rollup_refused_goes_straight_to_the_live_statements_for_the_cache_ttl(monkeypatch):
    from codemie.service.analytics import dashboard_cache

    monkeypatch.setattr(config, "CLI_ANALYTICS_DASHBOARD_CACHE_TTL_S", 30)
    dashboard_cache.reset_dashboard_cache()
    handler, live, rollup = _rollup_cost_handler(None)
    f = make_filter()  # as the test above: the scope the rollup's cache key is made for

    await handler.get_cost(f, include_class_lists=False, include_buckets=False)
    await handler.get_cost(f, include_class_lists=False, include_buckets=False)

    rollup.assert_awaited_once()  # the second request does not repeat the reads that end in the same refusal
    assert live.calls.count("get_cost_money") == 2
    assert not dashboard_cache.rollup_recently_used()  # a request the rollup did not answer keeps no warm-up going
    dashboard_cache.reset_dashboard_cache()


@pytest.mark.asyncio
async def test_a_second_cost_request_reuses_the_rollup_answer_for_the_cache_ttl(monkeypatch):
    monkeypatch.setattr(config, "CLI_ANALYTICS_DASHBOARD_CACHE_TTL_S", 30)
    handler, _, rollup = _rollup_cost_handler(_rollup_cost())

    await handler.get_cost(make_filter(), include_class_lists=False, include_buckets=False)
    await handler.get_cost(make_filter(), include_class_lists=False, include_buckets=False)

    rollup.assert_awaited_once()


@pytest.mark.asyncio
async def test_cost_budget_timeout_releases_connection(monkeypatch):
    from codemie.configs.config import config as app_config

    monkeypatch.setattr(app_config, "CLI_ANALYTICS_DASHBOARD_REQUEST_BUDGET_S", 0.01)
    handler, reader = _cost_handler(GroupedMoney({}), hold=1.0)

    with pytest.raises(TimeoutError):
        await _priced(handler.get_cost(make_filter()))

    assert reader.exited


@pytest.mark.asyncio
async def test_cost_subagent_only_session_in_totals_not_users():
    """A session with only subagent-totals rows is in the total and the model, but in no user's figure."""
    sonnet = "claude-sonnet-4-6"
    money = GroupedMoney(
        {
            G_TOTAL: [_cg({}, 5.0, 2)],
            G_USER: [_cg({"user": "alice"}, 3.0)],
            G_MODEL: [_cg({"model": sonnet}, 5.0, 2)],
        }
    )
    handler, _ = _cost_handler(money, by_user=["alice", "bob"])

    data, _ = await _priced(handler.get_cost(make_filter()))

    assert data["kpis"]["total_cost_usd"] == 5.0
    assert [(r["developer_name"], r["cost_usd"]) for r in data["cost_by_user"]] == [("alice", 3.0), ("bob", 0.0)]
    assert [(r["model_name"], r["cost_usd"]) for r in data["cost_by_model"]] == [(sonnet, 5.0)]


@pytest.mark.asyncio
async def test_cost_empty_filter_returns_todays_empty_response():
    handler, _ = _cost_handler(GroupedMoney({}))

    data, pricer = await _priced(handler.get_cost(make_filter()))

    assert data["kpis"]["total_cost_usd"] == 0.0 and data["kpis"]["avg_cost_per_session"] == 0.0
    assert (data["cost_by_user"], data["cost_by_model"], data["models_without_spend"]) == ([], [], [])
    assert data["funding"] == buckets_from_groups([], [])["funding"]
    assert data["personal_billed"] == buckets_from_groups([], [])["personal_billed"]
    assert pricer.unpriced == []


@pytest.mark.asyncio
async def test_cost_unpriced_from_spend_total_only():
    """metadata.unpriced is the spend total's; an estimate bucket's unpriced models stay in that bucket."""
    money = GroupedMoney(
        {
            G_TOTAL: [_cg({}, 1.0, unpriced=["mystery"])],
            G_ROUTE: [_cg({"funding_route": "api_based"}, 2.0, unpriced=["estimate-only"])],
        }
    )
    handler, _ = _cost_handler(money)

    data, pricer = await _priced(handler.get_cost(make_filter()))

    assert pricer.unpriced == ["mystery"]
    assert data["funding"]["api_based"]["unpriced_models"] == ["estimate-only"]
    assert data["funding"]["api_based"]["cost_incomplete"] is True


@pytest.mark.asyncio
async def test_cost_joins_buckets_by_user_and_model_and_lists_models_without_spend():
    money = GroupedMoney(
        {
            G_TOTAL: [_cg({}, 4.0, 2)],
            G_USER: [_cg({"user": "alice"}, 4.0, 2)],
            G_MODEL: [_cg({"model": "m-spend"}, 4.0, 2)],
            G_USER_PERSONAL: [
                _cg({"user": "alice", "personal_billed": True}, 7.0),
                _cg({"user": "alice", "personal_billed": False}, 4.0),
            ],
            G_MODEL_PERSONAL: [_cg({"model": "m-personal", "personal_billed": True}, 9.0)],
            G_MODEL_ROUTE: [_cg({"model": "m-spend", "funding_route": "api_based"}, 4.0)],
        }
    )
    handler, _ = _cost_handler(money, by_user=["alice"])

    data, _ = await _priced(handler.get_cost(make_filter()))

    assert data["cost_by_user"][0]["personal_billed"]["cost_usd"] == 7.0
    assert data["cost_by_model"][0]["funding"]["api_based"]["cost_usd"] == 4.0
    assert [
        (r["model_name"], r["cost_usd"], r["personal_billed"]["cost_usd"]) for r in data["models_without_spend"]
    ] == [("m-personal", 0.0, 9.0)]


# ── /users and /user-charts on one bounded connection (EPMCDME-17051) ──


class _UsersReader:
    """The bounded reader of a /users request: records the statements in order, with the developers each got."""

    def __init__(self, user_rows, money=None, facts=None, avg=None, hold=0.0):
        self.calls: list = []
        self.exited = False
        self.file_facts_fetched = False
        repo = MagicMock()
        install_users_reads(repo, user_rows, money=money, facts=facts, avg=avg, calls=self.calls)
        self.repo = repo

        async def record(name, rows, f=None):
            self.calls.append((name, f.developers) if f is not None else name)
            if hold:
                await asyncio.sleep(hold)
            return rows

        async def lines(f):
            return await record("get_lines_by_user", [], f)

        async def last_active(f):
            return await record("get_users_last_active", [], f)

        async def daily(f):
            return await record("get_users_daily_activity", [], f)

        async def file_facts(f):
            self.file_facts_fetched = True
            return []

        async def materialize_sel(f):
            self.calls.append("materialize_sel")

        repo.materialize_sel = materialize_sel
        repo.get_lines_by_user, repo.get_users_last_active = lines, last_active
        repo.get_users_daily_activity, repo.get_file_facts_by_session = daily, file_facts
        if hold:
            inner = repo.get_users_money_page

            async def slow_page(f, rates, limit, offset):
                await asyncio.sleep(hold)
                return await inner(f, rates, limit, offset)

            repo.get_users_money_page = slow_page

    def top_level(self):
        outer = MagicMock(spec=["bounded"])
        reader = self

        @asynccontextmanager
        async def bounded():
            try:
                yield reader.repo
            finally:
                reader.exited = True

        outer.bounded = bounded
        return outer


def _users_handler(user_rows, **kwargs):
    reader = _UsersReader(user_rows, **kwargs)
    return LocalAnalyticsHandler(reader.top_level()), reader


def _page_money():
    return GroupedMoney(
        {
            G_TOTAL: [_cg({}, 9.0, 3)],
            G_USER: [_cg({"user": "alice"}, 3.0), _cg({"user": "bob"}, 6.0)],
            G_USER_ROUTE: [_cg({"user": "bob", "funding_route": "api_based"}, 6.0)],
        }
    )


_TWO_USERS = [{"developer_name": "alice", "session_count": 1}, {"developer_name": "bob", "session_count": 2}]


@pytest.mark.asyncio
async def test_users_runs_in_one_bounded_connection_in_order():
    handler, reader = _users_handler(_TWO_USERS, money=_page_money())

    data, _ = await handler.get_users(make_filter(), 0, 1)  # the page is bob alone

    assert reader.calls == [
        "materialize_sel",
        "get_cost_models",
        ("get_users_money_page", None, 0),  # the money of everyone; the page is cut from the ranking
        "get_avg_session_duration_ms",
        ("get_users", ["bob"]),
        ("get_developer_session_facts", ["bob"]),
        ("get_lines_by_user", ["bob"]),
        ("get_users_last_active", ["bob"]),
        ("get_users_daily_activity", ["bob"]),
    ]
    assert reader.exited
    assert [(r["developer_name"], r["cost_usd"]) for r in data["rows"]] == [("bob", 6.0)]
    assert data["total_count"] == 2
    assert data["rows"][0]["funding"]["api_based"]["cost_usd"] == 6.0


@pytest.mark.asyncio
async def test_user_charts_returns_every_user():
    handler, reader = _users_handler(_TWO_USERS, money=_page_money())

    data, _ = await handler.get_users(make_filter(), None, None)

    assert [r["developer_name"] for r in data["rows"]] == ["bob", "alice"]
    assert data["total_count"] == 2  # the router replaces it with None for /user-charts
    assert ("get_users_money_page", None, 0) in reader.calls
    assert ("get_users", None) in reader.calls  # no developer restriction


@pytest.mark.asyncio
async def test_user_charts_only_reads_the_money_and_leaves_the_other_fields_at_their_defaults():
    handler, reader = _users_handler(_TWO_USERS, money=_page_money())

    data, _ = await handler.get_users(make_filter(), None, None, charts_only=True)

    assert reader.calls == ["materialize_sel", "get_cost_models", ("get_users_money_page", None, 0)]
    assert reader.exited
    assert [(r["developer_name"], r["cost_usd"]) for r in data["rows"]] == [("bob", 6.0), ("alice", 3.0)]
    assert data["rows"][0]["funding"]["api_based"]["cost_usd"] == 6.0  # the buckets come from the money statement
    assert data["avg_session_duration_ms"] is None
    bob = data["rows"][0]
    assert (bob["session_count"], bob["input_tokens"], bob["turns"], bob["tool_calls"]) == (0, 0, 0, 0)
    assert (bob["subscription_types"], bob["project_origins"], bob["daily_activity"]) == ([], [], [])
    assert bob["net_lines"] is None and bob["last_active"] is None


@pytest.mark.asyncio
async def test_users_page_with_the_chart_ranks_everyone_once_and_serves_the_page_and_the_chart():
    handler, reader = _users_handler(_TWO_USERS, money=_page_money())

    data, _ = await handler.get_users(make_filter(), 1, 1, include_chart=True)  # the second page: alice

    assert ("get_users_money_page", None, 0) in reader.calls  # the money of everyone, in one statement
    assert not any(
        c[0] == "get_users_money_page" and c != ("get_users_money_page", None, 0)
        for c in reader.calls
        if isinstance(c, tuple)
    )
    assert [r["developer_name"] for r in data["rows"]] == ["alice"]
    assert data["total_count"] == 2
    assert data["chart"] == [{"developer_name": "bob", "cost_usd": 6.0}, {"developer_name": "alice", "cost_usd": 3.0}]
    assert ("get_users", ["alice"]) in reader.calls  # the facts stay those of the page's developers


@pytest.mark.asyncio
async def test_users_page_with_the_chart_gives_the_rows_of_the_page_without_it():
    plain, _ = await _users_handler(_TWO_USERS, money=_page_money())[0].get_users(make_filter(), 0, 1)
    charted, _ = await _users_handler(_TWO_USERS, money=_page_money())[0].get_users(
        make_filter(), 0, 1, include_chart=True
    )

    assert charted.pop("chart") is not None
    assert charted == plain


@pytest.mark.asyncio
async def test_users_page_past_the_end_still_reports_the_chart_and_the_count():
    handler, _ = _users_handler(_TWO_USERS, money=_page_money())

    data, _ = await handler.get_users(make_filter(), 5, 50, include_chart=True)

    assert data["rows"] == [] and data["total_count"] == 2
    assert [c["developer_name"] for c in data["chart"]] == ["bob", "alice"]


@pytest.mark.asyncio
async def test_users_page_reads_daily_activity_and_average_duration_only_when_asked():
    asked, asked_reader = _users_handler(_TWO_USERS, money=_page_money(), avg=1234)
    skipped, skipped_reader = _users_handler(_TWO_USERS, money=_page_money(), avg=1234)

    full, _ = await asked.get_users(make_filter(), 0, 10)
    slim, _ = await skipped.get_users(make_filter(), 0, 10, include_daily_activity=False, include_avg_duration=False)

    assert "get_avg_session_duration_ms" in asked_reader.calls
    assert ("get_users_daily_activity", ["bob", "alice"]) in asked_reader.calls
    assert full["avg_session_duration_ms"] == 1234
    assert "get_avg_session_duration_ms" not in skipped_reader.calls
    assert not any(c[0] == "get_users_daily_activity" for c in skipped_reader.calls if isinstance(c, tuple))
    assert slim["avg_session_duration_ms"] is None
    assert all(r["daily_activity"] == [] for r in slim["rows"])
    assert [r["developer_name"] for r in slim["rows"]] == [
        r["developer_name"] for r in full["rows"]
    ]  # the rest is intact


@pytest.mark.asyncio
async def test_a_second_page_reuses_the_ranking_and_reads_only_its_own_facts():
    handler, reader = _users_handler(_TWO_USERS, money=_page_money())

    first, _ = await handler.get_users(make_filter(), 0, 1)
    reader.calls.clear()
    second, _ = await handler.get_users(make_filter(), 1, 1)

    assert [r["developer_name"] for r in first["rows"]] == ["bob"]
    assert [r["developer_name"] for r in second["rows"]] == ["alice"]
    assert second["total_count"] == 2
    assert "get_cost_models" not in reader.calls  # the ranking came from the cache
    assert not any(c[0] == "get_users_money_page" for c in reader.calls if isinstance(c, tuple))
    assert ("get_users", ["alice"]) in reader.calls  # the facts are those of the new page


@pytest.mark.asyncio
async def test_user_charts_shares_the_ranking_with_the_pages():
    handler, reader = _users_handler(_TWO_USERS, money=_page_money())

    await handler.get_users(make_filter(), 0, 50)
    reader.calls.clear()
    charts, _ = await handler.get_users(make_filter(), None, None, charts_only=True)

    assert [r["developer_name"] for r in charts["rows"]] == ["bob", "alice"]
    assert not any(c[0] == "get_users_money_page" for c in reader.calls if isinstance(c, tuple))


@pytest.mark.asyncio
async def test_another_viewer_scope_never_reuses_the_ranking():
    handler, reader = _users_handler(_TWO_USERS, money=_page_money())
    other = make_filter()
    other.scope = ViewerScope(
        viewer_email="b@example.com", unrestricted=False, admin_projects=frozenset({"p"}), mode="detail"
    )

    await handler.get_users(make_filter(), 0, 50)
    reader.calls.clear()
    await handler.get_users(other, 0, 50)

    assert ("get_users_money_page", None, 0) in reader.calls


@pytest.mark.asyncio
async def test_a_zero_ttl_computes_the_ranking_for_every_request():
    handler, reader = _users_handler(_TWO_USERS, money=_page_money())

    with patch.object(config, "CLI_ANALYTICS_DASHBOARD_CACHE_TTL_S", 0):
        await handler.get_users(make_filter(), 0, 50)
        reader.calls.clear()
        await handler.get_users(make_filter(), 0, 50)

    assert ("get_users_money_page", None, 0) in reader.calls


# ── served from the user rollup ──

# What the route passes: the two reads over the whole window are off unless asked for.
SLIM = {"include_daily_activity": False, "include_avg_duration": False}


def _rollup_users():
    from codemie.repository.cli_analytics.ports import RollupUsers, UsersMoneyPage

    money = _page_money()
    page = UsersMoneyPage(["bob", "alice"], 2, money)
    users = [
        {"developer_name": "bob", "session_count": 2, "input_tokens": 5, "top_model": "m", "subscription_types": ["x"]},
        {"developer_name": "alice", "session_count": 1, "input_tokens": 1, "subscription_types": []},
    ]
    facts = [{"developer": "bob", "turns": 4, "tool_calls": 3, "tool_successes": 3}, {"developer": "alice", "turns": 1}]
    return RollupUsers(page, users, facts, [{"developer_name": "bob", "net_lines": 7}], [])


class _RollupRepo:
    """A repo with the rollup read and nothing else: the live statements must not be reached."""

    def __init__(self, served):
        self.served, self.calls = served, []

    async def rollup_users(self, f, rate_of, max_age_s):
        self.calls.append(max_age_s)
        return self.served

    def bounded(self):
        raise AssertionError("the live statements ran")


@pytest.mark.asyncio
async def test_the_rollup_answers_a_page_without_touching_the_live_statements():
    repo = _RollupRepo(_rollup_users())

    data, _ = await LocalAnalyticsHandler(repo).get_users(make_filter(), 1, 1, include_chart=True, **SLIM)

    assert [r["developer_name"] for r in data["rows"]] == ["alice"]  # the second page of the ranking
    assert data["total_count"] == 2
    assert data["chart"] == [{"developer_name": "bob", "cost_usd": 6.0}, {"developer_name": "alice", "cost_usd": 3.0}]
    assert data["avg_session_duration_ms"] is None and data["rows"][0]["daily_activity"] == []


@pytest.mark.asyncio
async def test_the_rollup_rows_of_a_page_carry_the_facts_of_its_developers():
    repo = _RollupRepo(_rollup_users())

    data, _ = await LocalAnalyticsHandler(repo).get_users(make_filter(), 0, 1, **SLIM)

    (bob,) = data["rows"]
    assert (bob["session_count"], bob["turns"], bob["tool_calls"], bob["net_lines"], bob["top_model"]) == (
        2,
        4,
        3,
        7,
        "m",
    )


@pytest.mark.asyncio
async def test_user_charts_from_the_rollup_returns_every_user_with_money_only():
    repo = _RollupRepo(_rollup_users())

    data, _ = await LocalAnalyticsHandler(repo).get_users(make_filter(), None, None, charts_only=True)

    assert [(r["developer_name"], r["cost_usd"]) for r in data["rows"]] == [("bob", 6.0), ("alice", 3.0)]
    assert data["rows"][0]["turns"] == 0  # the facts are not read for the chart


@pytest.mark.asyncio
async def test_a_second_request_reuses_the_rollup_answer():
    repo = _RollupRepo(_rollup_users())
    handler = LocalAnalyticsHandler(repo)

    await handler.get_users(make_filter(), 0, 1, **SLIM)
    await handler.get_users(make_filter(), 1, 1, **SLIM)

    assert len(repo.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{"include_daily_activity": True}, {"include_avg_duration": True}])
async def test_reads_over_the_whole_window_skip_the_rollup(kwargs):
    handler, reader = _users_handler(_TWO_USERS, money=_page_money())
    served = _RollupRepo(_rollup_users())
    handler._repo.rollup_users = served.rollup_users

    await handler.get_users(make_filter(), 0, 1, **kwargs)

    assert served.calls == []
    assert "materialize_sel" in reader.calls  # the live statements ran


@pytest.mark.asyncio
async def test_without_a_rollup_answer_the_live_statements_run():
    handler, reader = _users_handler(_TWO_USERS, money=_page_money())
    served = _RollupRepo(None)
    handler._repo.rollup_users = served.rollup_users

    data, _ = await handler.get_users(make_filter(), 0, 1, **SLIM)

    assert len(served.calls) == 1
    assert [r["developer_name"] for r in data["rows"]] == ["bob"]
    assert "materialize_sel" in reader.calls


@pytest.mark.asyncio
async def test_a_switched_off_rollup_is_not_asked():
    repo = _RollupRepo(_rollup_users())
    handler, reader = _users_handler(_TWO_USERS, money=_page_money())
    handler._repo.rollup_users = repo.rollup_users

    with patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", False):
        await handler.get_users(make_filter(), 0, 1, **SLIM)

    assert repo.calls == []


@pytest.mark.asyncio
async def test_users_without_include_chart_carry_no_chart():
    handler, _ = _users_handler(_TWO_USERS, money=_page_money())

    data, _ = await handler.get_users(make_filter(), 0, 1)

    assert "chart" not in data


@pytest.mark.asyncio
async def test_users_never_fetches_file_facts():
    handler, reader = _users_handler(_TWO_USERS, money=_page_money())

    await handler.get_users(make_filter(), None, None)

    assert not reader.file_facts_fetched


@pytest.mark.asyncio
async def test_users_budget_timeout_releases_connection(monkeypatch):
    from codemie.configs.config import config as app_config

    monkeypatch.setattr(app_config, "CLI_ANALYTICS_DASHBOARD_REQUEST_BUDGET_S", 0.01)
    handler, reader = _users_handler(_TWO_USERS, money=_page_money(), hold=1.0)

    with pytest.raises(TimeoutError):
        await handler.get_users(make_filter(), 0, 10)

    assert reader.exited


@pytest.mark.asyncio
async def test_users_empty_filter_returns_todays_empty_response():
    handler, reader = _users_handler([], money=GroupedMoney({}))

    data, pricer = await handler.get_users(make_filter(), 0, 50)

    assert data["rows"] == [] and data["total_count"] == 0 and data["avg_session_duration_ms"] is None
    assert data["funding"] == buckets_from_groups([], [])["funding"]
    assert data["personal_billed"] == buckets_from_groups([], [])["personal_billed"]
    assert pricer.unpriced == []
    # An empty page must not turn into "no developer restriction" for the page facts.
    assert [
        c for c in reader.calls if isinstance(c, tuple) and c[0] in ("get_users", "get_developer_session_facts")
    ] == []


@pytest.mark.asyncio
async def test_users_row_facts_come_from_the_developer_reads_and_overall_avg_from_the_window():
    facts = [{"developer": "bob", "turns": 7, "tool_calls": 4, "tool_successes": 3}]
    handler, _ = _users_handler(_TWO_USERS, money=_page_money(), facts=facts, avg=1234)

    data, _ = await handler.get_users(make_filter(), None, None)

    bob = next(r for r in data["rows"] if r["developer_name"] == "bob")
    alice = next(r for r in data["rows"] if r["developer_name"] == "alice")
    assert (bob["turns"], bob["tool_calls"], bob["tool_success_rate"]) == (7, 4, 75.0)
    assert (alice["turns"], alice["tool_calls"]) == (0, 0)
    assert data["avg_session_duration_ms"] == 1234
