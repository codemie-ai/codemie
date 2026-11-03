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

"""Unit tests for leaderboard collector helpers, ES aggregation logic and the spend-port reads."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.monitoring.metrics_constants import INVOKED_BY_SCRIPT, MetricsAttributes
from codemie.service.leaderboard.collector import (
    ATTR_ASSISTANT_ID_KEYWORD,
    ATTR_CLI_REQUEST,
    ATTR_MCP_NAME_KEYWORD,
    ATTR_USER_ID_KEYWORD,
    CLI_ASSISTANT_ID,
    METRIC_NAME_KEYWORD,
    LeaderboardCollector,
    RawUserMetrics,
    UserSpend,
    _creator_email_sql,
    _creator_name_sql,
    _creator_user_id_sql,
    _validate_column,
)
from codemie.service.spend.coverage import SpendResult, TimeRange
from codemie.service.spend.ledger import ComposedScope, PersonalHolder, PersonTotalHolder
from codemie.service.spend.rendering import render_spend
from codemie.service.spend.traffic_class import TrafficClass

MODULE = "codemie.service.leaderboard.collector"

PERIOD = TimeRange(datetime(2026, 3, 1, tzinfo=timezone.utc), datetime(2026, 4, 1, tzinfo=timezone.utc))

# One person's spend, split the two ways that matter. The **project-budget** CLI row is the whole
# point: `PersonalHolder(user, CLI)` is what the person's own customer was charged and cannot see it,
# so a CLI figure read through that holder silently under-reports every CLI call made against a
# project budget.
PRINCIPAL_CLI = 3.0
PROJECT_CLI = 4.0
PROJECT_PLATFORM = 5.0
WHOLE_CLI = PRINCIPAL_CLI + PROJECT_CLI
WHOLE_TOTAL = PRINCIPAL_CLI + PROJECT_CLI + PROJECT_PLATFORM


def _figure(amount: float) -> SpendResult:
    return SpendResult(figure=Decimal(str(amount)))


def _holder_total(holder) -> float:
    """What each holder's own predicate would sum over the rows above.

    Encodes the asymmetry the port's docstrings state: `PersonTotalHolder` spans the personal **and**
    project pots across every category, while `PersonalHolder` is the person's own customer only.
    """
    if isinstance(holder, PersonTotalHolder):
        return WHOLE_TOTAL
    if isinstance(holder, PersonalHolder):
        return PRINCIPAL_CLI if holder.category is BudgetCategory.CLI else PROJECT_PLATFORM
    raise AssertionError(f"the leaderboard has no business reading {holder!r}")


class FakeSpendService:
    """A tiny ledger over the rows above, answering each port read by its documented meaning.

    It records what it was asked, so a test can assert *which* read served a figure - which is what
    makes swapping the composed CLI read for `PersonalHolder(user, CLI)` fail rather than pass with a
    quietly smaller number.
    """

    def __init__(self) -> None:
        self.holders_read: list = []
        self.composed_scopes: list[ComposedScope] = []
        self.access_seen: list = []

    async def consumed_for_holders(self, holders, period, **passed):
        assert period == PERIOD
        self.holders_read.extend(holders)
        # Recorded rather than accepted silently: this collector is a **tenant-wide snapshot with no
        # per-request caller**, so an access boundary arriving here could only be whoever happened to
        # trigger the snapshot - and the leaderboard would then shrink to their own visibility.
        self.access_seen.append(passed)
        return {holder: _figure(_holder_total(holder)) for holder in holders}

    async def consumed_for_holder(self, holder, period, **passed):
        assert period == PERIOD
        self.holders_read.append(holder)
        self.access_seen.append(passed)
        return _figure(_holder_total(holder))

    async def aggregate_composed_by_traffic_class(self, period, scope):
        assert period == PERIOD
        self.composed_scopes.append(scope)
        return {
            TrafficClass.CLI: _figure(WHOLE_CLI),
            TrafficClass.WORKFLOW: _figure(0.0),
            TrafficClass.ASSISTANT: _figure(0.0),
            TrafficClass.DATASOURCE: _figure(0.0),
        }


def _user_record(user_id: str, username: str, email: str) -> SimpleNamespace:
    return SimpleNamespace(id=user_id, username=username, email=email, name=username)


@pytest.fixture
def fake_spend():
    return FakeSpendService()


@pytest.fixture
def resolvable_users():
    """A repository that resolves every identifier it is handed."""
    repo = MagicMock()
    repo.afind_users_by_identifiers = AsyncMock(
        side_effect=lambda _session, identifiers: {
            identifier: _user_record(identifier, f"{identifier}-name", f"{identifier}@epam.com")
            for identifier in identifiers
        }
    )
    return repo


@pytest.fixture
def mock_session():
    return AsyncMock()


@pytest.fixture
def mock_es_repository():
    repo = MagicMock()
    repo.execute_aggregation_query = AsyncMock()
    return repo


@pytest.fixture
def collector(mock_session, mock_es_repository):
    with patch(f"{MODULE}.config") as mock_config:
        mock_config.DEFAULT_DB_SCHEMA = "public"
        yield LeaderboardCollector(mock_session, mock_es_repository)


class TestHelperFunctions:
    def test_validate_column_accepts_whitelisted_value(self):
        assert _validate_column("a.created_by") == "a.created_by"

    def test_validate_column_rejects_non_whitelisted_value(self):
        with pytest.raises(ValueError, match="allowed columns whitelist"):
            _validate_column("users.created_by")

    def test_creator_sql_helpers_render_expected_sql(self):
        assert (
            _creator_user_id_sql("a.created_by")
            == "COALESCE(a.created_by->>'user_id', a.created_by->>'id', a.created_by->>'username')"
        )
        assert (
            _creator_name_sql("a.created_by")
            == "NULLIF(COALESCE(a.created_by->>'name', a.created_by->>'username', ''), '')"
        )
        assert (
            _creator_email_sql("a.created_by")
            == "CASE WHEN COALESCE(a.created_by->>'username', '') LIKE '%%@%%' THEN LOWER(a.created_by->>'username') ELSE NULL END"
        )

    @patch(f"{MODULE}.config")
    def test_init_rejects_invalid_schema_name(self, mock_config, mock_session, mock_es_repository):
        mock_config.DEFAULT_DB_SCHEMA = "bad-schema!"

        with pytest.raises(ValueError, match="Invalid DB schema name"):
            LeaderboardCollector(mock_session, mock_es_repository)


class TestCollectorStaticHelpers:
    def test_build_period_bounds_normalizes_dates(self, collector):
        start_dt, end_exclusive, start_iso, end_iso = collector._build_period_bounds(
            datetime(2026, 3, 15, 12, 30),
            datetime(2026, 3, 20, 18, 45),
        )

        assert start_dt == datetime(2026, 3, 15, 0, 0, 0)
        assert end_exclusive == datetime(2026, 3, 21, 0, 0, 0)
        assert start_iso == "2026-03-15"
        assert end_iso == "2026-03-20"

    def test_normalize_metric_result_returns_empty_dict_for_exception(self, collector):
        log_fn = MagicMock()

        result = collector._normalize_metric_result(RuntimeError("boom"), "failed", log_fn)

        assert result == {}
        log_fn.assert_called_once()

    def test_merge_es_users_adds_and_merges_identity_fields(self, collector):
        users = {
            "u1": {"user_id": "u1", "user_name": "", "user_email": None, "projects": ["alpha"]},
        }
        es_metrics = {
            "u1": {"identity": {"user_name": "Alice", "user_email": "alice@epam.com", "projects": ["beta", ""]}},
            "u2": {"identity": {"user_name": "Bob", "user_email": None, "projects": ["gamma"]}},
        }

        collector._merge_es_users(users, es_metrics)

        assert users["u1"]["user_name"] == "Alice"
        assert users["u1"]["user_email"] == "alice@epam.com"
        assert users["u1"]["projects"] == ["alpha", "beta"]
        assert users["u2"] == {
            "user_id": "u2",
            "user_name": "Bob",
            "user_email": None,
            "projects": ["gamma"],
        }

    def test_build_raw_metrics_merges_pg_es_and_spend_payloads(self, collector):
        users = {
            "u1": {"user_name": "John Smith", "user_email": None, "projects": ["alpha"]},
        }
        pg_metrics = {
            "u1": {
                "usage": {"web_conversations": 3},
                "creation": {"assistants_created": 1},
                "workflow_usage": {"workflow_executions": 2},
                "workflow_creation": {"workflows_created": 4},
                "cli": {"cli_sessions": 5},
                "impact": {"kata_completed": 6},
            }
        }
        es_metrics = {
            "u1": {
                "usage": {"active_days": 7},
                "creation": {"datasources_created": 8},
                "workflow_usage": {"workflow_successes": 9},
                "cli": {"cli_lines_added": 40},
            }
        }
        spend_metrics = {"u1": UserSpend(total=render_spend(_figure(11.5)), cli=render_spend(_figure(7.5)))}

        result = collector._build_raw_metrics(users, pg_metrics, es_metrics, spend_metrics)

        assert result == [
            RawUserMetrics(
                user_id="u1",
                user_name="John Smith",
                user_email=None,
                projects=["alpha"],
                usage={"web_conversations": 3, "active_days": 7},
                creation={"assistants_created": 1, "datasources_created": 8},
                workflow_usage={"workflow_executions": 2, "workflow_successes": 9},
                workflow_creation={"workflows_created": 4},
                cli={"cli_sessions": 5, "cli_lines_added": 40},
                impact={"kata_completed": 6},
                spend=UserSpend(total=render_spend(_figure(11.5)), cli=render_spend(_figure(7.5))),
            )
        ]

    def test_build_raw_metrics_leaves_an_unread_person_with_no_figure(self, collector):
        """A person the spend read never answered for carries absence, never a zero."""
        users = {"u1": {"user_name": "John Smith", "user_email": None, "projects": []}}

        result = collector._build_raw_metrics(users, {}, {}, {})

        assert result[0].spend.total.figure is None
        assert result[0].spend.cli.figure is None
        assert result[0].spend.total.unavailable is True
        assert result[0].spend.cli.unavailable is True


class TestQueryBuilders:
    def test_build_range_filter(self, collector):
        assert collector._build_range_filter("2026-03-01", "2026-03-31") == {
            "range": {
                "@timestamp": {
                    "gte": "2026-03-01",
                    "lte": "2026-03-31",
                    "format": "yyyy-MM-dd",
                }
            }
        }

    def test_by_user_terms_and_metric_term_use_constants(self, collector):
        assert collector._by_user_terms() == {
            "field": ATTR_USER_ID_KEYWORD,
            "size": 10000,
            "min_doc_count": 1,
        }
        assert collector._metric_term("x") == {"term": {METRIC_NAME_KEYWORD: "x"}}

    def test_build_activity_query_uses_expected_fields(self, collector):
        query = collector._build_activity_query({"range": "value"})

        assert query["query"] == {"range": "value"}
        by_user = query["aggs"]["by_user"]
        assert by_user["terms"]["field"] == ATTR_USER_ID_KEYWORD
        web_usage_bool = by_user["aggs"]["web_usage"]["filter"]["bool"]
        assert web_usage_bool["must"] == [{"term": {METRIC_NAME_KEYWORD: "conversation_assistant_usage"}}]
        assert web_usage_bool["must_not"] == [{"term": {ATTR_ASSISTANT_ID_KEYWORD: CLI_ASSISTANT_ID}}]

    def test_build_tool_usage_query_uses_cli_and_mcp_constants(self, collector):
        query = collector._build_tool_usage_query({"range": "value"})

        bool_query = query["query"]["bool"]
        assert {"term": {ATTR_CLI_REQUEST: True}} in bool_query["must_not"]
        assert {"exists": {"field": ATTR_MCP_NAME_KEYWORD}} in bool_query["must"][1]["bool"]["should"]

    def test_build_tool_usage_query_excludes_script_calls_from_unique_tools_only(self, collector):
        query = collector._build_tool_usage_query({"range": "value"})

        tool_usage = query["aggs"]["by_user"]["aggs"]["tool_usage"]
        assert tool_usage["filter"]["bool"]["must"] == [collector._metric_term("codemie_tools_usage_total")]
        assert tool_usage["filter"]["bool"]["must_not"] == [
            {"term": {f"attributes.{MetricsAttributes.INVOKED_BY}.keyword": INVOKED_BY_SCRIPT}}
        ]
        # totals terms (the top-level should clauses) are unchanged
        should = query["query"]["bool"]["must"][1]["bool"]["should"]
        assert collector._metric_term("codemie_tools_usage_total") in should

    def test_build_cli_query_aggregates_no_money(self, collector):
        """AD-29: money never comes from Elasticsearch, so the CLI query asks for none."""
        query = collector._build_cli_query({"range": "value"})

        sub_aggs = query["aggs"]["by_user"]["aggs"]
        assert set(sub_aggs) == {"sessions", "tool_usage"}
        assert "money_spent" not in repr(query)


class TestAggregationMergers:
    def test_merge_activity_data_populates_identity_and_usage(self, collector):
        result = defaultdict(dict)
        activity_data = {
            "aggregations": {
                "by_user": {
                    "sum_other_doc_count": 0,
                    "buckets": [
                        {
                            "key": "u1",
                            "user_name": {"buckets": [{"key": "Alice"}]},
                            "user_email": {"buckets": [{"key": "alice@epam.com"}]},
                            "projects": {"buckets": [{"key": "proj-b"}, {"key": "proj-a"}]},
                            "active_days": {"value": 4},
                            "web_usage": {"assistants": {"value": 2}},
                        }
                    ],
                }
            }
        }

        collector._merge_activity_data(result, activity_data)

        assert result["u1"]["identity"] == {
            "user_name": "Alice",
            "user_email": "alice@epam.com",
            "projects": ["proj-a", "proj-b"],
        }
        assert result["u1"]["usage"] == {
            "active_days": 4,
            "es_assistants_used": 2,
        }

    def test_merge_tool_usage_data_preserves_existing_usage(self, collector):
        result = defaultdict(dict, {"u1": {"usage": {"active_days": 4}}})
        tool_data = {
            "aggregations": {
                "by_user": {
                    "buckets": [
                        {
                            "key": "u1",
                            "tool_usage": {"unique_tools": {"value": 3}},
                            "skill_usage": {"count": {"value": 5}},
                            "mcp_usage": {"unique_mcps": {"value": 2}},
                        }
                    ]
                }
            }
        }

        collector._merge_tool_usage_data(result, tool_data)

        assert result["u1"]["usage"] == {
            "active_days": 4,
            "unique_tools": 3,
            "skill_usage_events": 5,
            "unique_mcps_used": 2,
        }

    def test_merge_cli_data_sums_files_and_tokens(self, collector):
        result = defaultdict(dict)
        cli_data = {
            "aggregations": {
                "by_user": {
                    "buckets": [
                        {
                            "key": "u1",
                            "sessions": {"count": {"value": 2}},
                            "tool_usage": {
                                "repos": {"value": 3},
                                "lines_added": {"value": 40},
                                "lines_removed": {"value": 10},
                                "files_created": {"value": 1},
                                "files_modified": {"value": 2},
                                "files_deleted": {"value": 3},
                                "input_tokens": {"value": 100},
                                "output_tokens": {"value": 50},
                                "cache_read_tokens": {"value": 25},
                            },
                        }
                    ]
                }
            }
        }

        collector._merge_cli_data(result, cli_data)

        assert result["u1"]["cli"] == {
            "cli_sessions": 2,
            "cli_repos": 3,
            "cli_lines_added": 40,
            "cli_lines_removed": 10,
            "cli_files_changed": 6,
            "cli_total_tokens": 175,
        }
        # No money key at all. The person's CLI spend is a spend-port read, and an ES copy of it
        # beside the port's figure is the second answer to one question this rewire removed.
        assert "cli_total_spend" not in result["u1"]["cli"]


class TestSpendCollection:
    """The money figures, and the one read that serves each."""

    @pytest.mark.asyncio
    async def test_person_total_is_one_holder_read_over_both_pots(self, collector, fake_spend, resolvable_users):
        """`summary["total_spend"]`'s source: one `PersonTotalHolder` read, with nothing added to it."""
        with patch(f"{MODULE}.spend_service", fake_spend), patch(f"{MODULE}.user_repository", resolvable_users):
            spend = await collector._collect_spend({"u1"}, PERIOD)

        assert spend["u1"].total.figure == WHOLE_TOTAL
        # One holder, and it is the one spanning both pots and every category - so the figure needs no
        # second aggregate added to it, which is what the ES web-spend addition used to be.
        assert [type(holder) for holder in fake_spend.holders_read] == [PersonTotalHolder]
        assert fake_spend.holders_read[0].user_id == "u1"
        assert fake_spend.holders_read[0].aliases == frozenset({"u1-name", "u1@epam.com"})

    @pytest.mark.asyncio
    async def test_the_snapshot_passes_no_access_boundary_and_must_not(
        self, collector, fake_spend, resolvable_users
    ) -> None:
        """**A snapshot read is not a report and is not narrowed.**

        The two caller-facing spend tables hand `consumed_for_holders` the caller's boundary, because
        the rows beside their figures are already narrowed to what that caller may see. This collector
        has no caller at all: it runs tenant-wide, off the request path, and the figures it writes are
        read back by everyone. A boundary here would silently shrink the whole leaderboard to whoever
        triggered the snapshot - and an absent figure is never a zero, so the rows would not even look
        wrong.
        """
        with patch(f"{MODULE}.spend_service", fake_spend), patch(f"{MODULE}.user_repository", resolvable_users):
            await collector._collect_spend({"u1"}, PERIOD)

        assert fake_spend.access_seen, "the port was not read at all, so this asserts nothing"
        assert all(passed == {} for passed in fake_spend.access_seen)

    @pytest.mark.asyncio
    async def test_cli_figure_includes_a_project_scoped_row(self, collector, fake_spend, resolvable_users):
        """THE TRAP. The CLI figure is *all* of a person's CLI traffic, project-scoped rows included.

        `PersonalHolder(user, BudgetCategory.CLI)` looks like the read for this and compiles, but it is
        what the person's own customer was charged, so it would return only `PRINCIPAL_CLI` and drop
        every CLI call the person made against a project budget. The composed
        class split spans both pots, which is why it and not a holder serves this figure.
        """
        with patch(f"{MODULE}.spend_service", fake_spend), patch(f"{MODULE}.user_repository", resolvable_users):
            spend = await collector._collect_spend({"u1"}, PERIOD)

        assert spend["u1"].cli.figure == WHOLE_CLI
        assert spend["u1"].cli.figure > PRINCIPAL_CLI, "the project-scoped CLI row is missing from the figure"
        # Read as a composed scope naming only the person - no holder was consulted for the CLI split.
        assert len(fake_spend.composed_scopes) == 1
        scope = fake_spend.composed_scopes[0]
        assert scope.user.user_id == "u1"
        assert (scope.project, scope.dimension, scope.traffic_class) == (None, None, None)
        assert not any(isinstance(holder, PersonalHolder) for holder in fake_spend.holders_read)

    @pytest.mark.asyncio
    async def test_both_cli_uses_share_a_single_read(self, collector, fake_spend, resolvable_users):
        """One aggregation for one quantity: the ranking and the summary read the same object."""
        with patch(f"{MODULE}.spend_service", fake_spend), patch(f"{MODULE}.user_repository", resolvable_users):
            spend = await collector._collect_spend({"u1", "u2"}, PERIOD)

        # One composed read per person, not two - the ranking figure and the display figure are the
        # same `RenderedSpend`, so they cannot disagree.
        assert len(fake_spend.composed_scopes) == 2
        assert {entry.cli.figure for entry in spend.values()} == {WHOLE_CLI}

    @pytest.mark.asyncio
    async def test_unresolvable_person_carries_no_figure(self, collector, fake_spend):
        """A person the identity bridge cannot resolve is not asked about, and is not a zero."""
        repo = MagicMock()
        repo.afind_users_by_identifiers = AsyncMock(
            return_value={"known": _user_record("known", "known-name", "known@epam.com")}
        )

        with patch(f"{MODULE}.spend_service", fake_spend), patch(f"{MODULE}.user_repository", repo):
            spend = await collector._collect_spend({"known", "deleted-user"}, PERIOD)

        assert "deleted-user" not in spend
        assert render_spend(None).figure is None
        assert [holder.user_id for holder in fake_spend.holders_read] == ["known"]
        assert len(fake_spend.composed_scopes) == 1

    @pytest.mark.asyncio
    async def test_unreadable_period_reads_nothing(self, collector, fake_spend, resolvable_users):
        with patch(f"{MODULE}.spend_service", fake_spend), patch(f"{MODULE}.user_repository", resolvable_users):
            assert await collector._collect_spend({"u1"}, None) == {}

        assert fake_spend.holders_read == []
        assert fake_spend.composed_scopes == []

    @pytest.mark.asyncio
    @patch(f"{MODULE}.logger")
    async def test_a_failing_ledger_leaves_every_figure_absent(self, mock_logger, collector, resolvable_users):
        """A ledger outage degrades the figures and never fails the snapshot - and never reports zero."""
        exploding = MagicMock()
        exploding.consumed_for_holders = AsyncMock(side_effect=RuntimeError("ledger down"))

        with patch(f"{MODULE}.spend_service", exploding), patch(f"{MODULE}.user_repository", resolvable_users):
            spend = await collector._collect_spend_or_nothing({"u1"}, PERIOD)

        assert spend == {}
        mock_logger.warning.assert_called_once()
