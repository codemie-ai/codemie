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

"""Unit tests for AssistantHandler."""

from __future__ import annotations

import contextlib
import json

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.configs import config
from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.assistant_handler import (
    AGENTS_USAGE_MONEY_BUCKET,
    AGENTS_USAGE_MONEY_KEY,
    AGENTS_USAGE_MONEY_SUM_AGG,
    MONEY_SPENT_FIELD,
    TIMESTAMP_FIELD,
    AssistantHandler,
)
from codemie.service.analytics.handlers.money_coverage import SETTLED_AGG, UNSETTLED_AGG
from codemie.service.analytics.handlers.workflow_handler import WorkflowHandler
from codemie.service.analytics.metric_names import MetricName
from codemie.service.spend.coverage import (
    DEFAULT_EMISSION_LAG,
    LedgerCoverage,
    Portion,
    SpendDimension,
    SpendResult,
    TimeRange,
)
from codemie.service.spend.ledger import AccessScope, ReportFilter
from codemie.service.spend.ledger_registry import register_ledger_coverage, reset_ledgers
from codemie.service.spend.rendering import RenderedSpend, render_spend

#: The boundary the `mock_user` fixture below is entitled to: a plain member of no project at all.
#: **Restricted with an empty project set, which matches nothing rather than everything** - the rule
#: `AccessScope` holds and `SecureQueryBuilder`'s `minimum_should_match=0` already applied to the rows
#: this table's figures foot.
_EXPECTED_SCOPE = AccessScope(unrestricted=False, user_id="test-user-id")

#: The report window these tests measure, and the floors that decide which side of the ledger's
#: coverage each leg of a figure is served from.
PERIOD_START = datetime(2026, 8, 1, tzinfo=timezone.utc)
PERIOD_END = datetime(2026, 8, 31, tzinfo=timezone.utc)
PERIOD = TimeRange(PERIOD_START, PERIOD_END)

#: A floor sitting inside `PERIOD`, so the same window is crossing, wholly below or wholly above
#: depending only on which floor a test publishes.
FLOOR = datetime(2026, 8, 15, tzinfo=timezone.utc)
AT_OR_ABOVE_FLOOR_LEG = TimeRange(FLOOR, PERIOD_END)
#: A floor above the whole of `PERIOD`: every instant in the window is below it.
BELOW_FLOOR_ONLY = datetime(2026, 9, 1, tzinfo=timezone.utc)
#: A floor below the whole of `PERIOD`: every instant in the window is at or above it.
ABOVE_FLOOR_ONLY = datetime(2026, 7, 1, tzinfo=timezone.utc)
#: A floor beyond any clock a test can run under, for the one test whose window the pipeline derives
#: from "now" rather than taking from the test.
EVERY_WINDOW_BELOW = datetime(2999, 1, 1, tzinfo=timezone.utc)

PORT = "codemie.service.analytics.handlers.assistant_handler.spend_service.aggregate_by_dimension"
ASSISTANT_DIRECTORY = "codemie.rest_api.models.assistant.Assistant.get_ids_by_names"
REGISTERED_COVERAGE = "codemie.service.analytics.handlers.assistant_handler.ledger_coverage"


@pytest.fixture
def mock_user():
    """Create mock user."""
    user = MagicMock(spec=User)
    user.project_names = []
    user.admin_project_names = []
    user.is_global_user = False
    user.is_admin = False
    user.id = "test-user-id"
    return user


@pytest.fixture
def mock_repository():
    """Create mock repository."""
    return MagicMock(spec=MetricsElasticRepository)


@pytest.fixture
def handler(mock_user, mock_repository):
    """Create handler with mocked dependencies."""
    return AssistantHandler(mock_user, mock_repository)


class TestGetAssistantsChats:
    """Tests for get_assistants_chats method."""

    @pytest.mark.asyncio
    async def test_get_assistants_chats_uses_esql_query(self, handler, mock_repository):
        """Verify complex assistant analytics uses ES|QL."""
        # Arrange
        mock_repository.execute_esql_query.return_value = {
            "columns": [
                {"name": "attributes.assistant_id.keyword", "type": "keyword"},
                {"name": "assistant_name", "type": "keyword"},
                {"name": "total_conversations", "type": "long"},
                {"name": "total_messages", "type": "long"},
                {"name": "unique_users", "type": "long"},
                {"name": "min_messages_per_chat", "type": "long"},
                {"name": "median_messages_rounded", "type": "double"},
                {"name": "avg_messages_rounded", "type": "double"},
                {"name": "max_messages_per_chat", "type": "long"},
                {"name": "successful_operations", "type": "long"},
                {"name": "total_errors", "type": "long"},
                {"name": "error_rate_percent", "type": "double"},
                {"name": "total_tokens", "type": "long"},
                {"name": "avg_execution_rounded", "type": "double"},
            ],
            "values": [],
        }

        # Act
        result = await handler.get_assistants_chats(time_period="last_30_days")

        # Assert
        # Verify ES|QL query was called (not regular aggregation query)
        mock_repository.execute_esql_query.assert_called_once()

        # Verify result structure
        assert "data" in result
        assert isinstance(result["data"], dict)
        assert "columns" in result["data"]
        assert "rows" in result["data"]

    @pytest.mark.asyncio
    async def test_the_esql_query_asks_elasticsearch_for_no_money_at_all(self, handler, mock_repository):
        """AD-14: Elasticsearch carries tokens and counts, and the query may not name a money field.

        Asserted on the query **text** rather than on a parsed row, because that is where the rule can
        be broken silently: a re-added `SUM(attributes.money_spent)` reaches a screen as a plausible
        number, and only the absence of the token itself proves the second source is gone.
        """
        # No rows: the subject is the query text, and the grouping column is all the parser needs.
        mock_repository.execute_esql_query.return_value = {
            "columns": [{"name": "attributes.assistant_id.keyword", "type": "keyword"}],
            "values": [],
        }

        await handler.get_assistants_chats(time_period="last_30_days")

        query = mock_repository.execute_esql_query.call_args.args[0]
        assert "money_spent" not in query
        assert "total_cost" not in query
        assert "cost_per_operation" not in query
        # The two-stage shape and everything else it computes are untouched.
        assert query.count("| STATS") == 2
        assert "BY attributes.assistant_id.keyword, attributes.conversation_id.keyword" in query
        assert "total_tokens_per_conv = SUM(attributes.input_tokens + attributes.output_tokens)" in query
        assert "| SORT total_messages DESC, attributes.assistant_id.keyword ASC" in query
        assert "| LIMIT 1000" in query


class TestAssistantsChatsResultParser:
    """Tests for assistants/chats result parser."""

    def test_parse_assistants_chats_result_extracts_all_fields(self, handler):
        """Verify all fields are extracted from ES|QL result."""
        # Arrange
        result = {
            "columns": [
                {"name": "attributes.assistant_id.keyword", "type": "keyword"},
                {"name": "assistant_name", "type": "keyword"},
                {"name": "total_conversations", "type": "long"},
                {"name": "total_messages", "type": "long"},
                {"name": "unique_users", "type": "long"},
                {"name": "min_messages_per_chat", "type": "long"},
                {"name": "median_messages_rounded", "type": "double"},
                {"name": "avg_messages_rounded", "type": "double"},
                {"name": "max_messages_per_chat", "type": "long"},
                {"name": "successful_operations", "type": "long"},
                {"name": "total_errors", "type": "long"},
                {"name": "error_rate_percent", "type": "double"},
                {"name": "total_tokens", "type": "long"},
                {"name": "avg_execution_rounded", "type": "double"},
                {"name": "last_error_time", "type": "date"},
            ],
            "values": [
                [
                    "assistant-42",  # attributes.assistant_id.keyword - the grouping key
                    "test-assistant",  # assistant_name - display only
                    10,  # total_conversations
                    100,  # total_messages
                    5,  # unique_users
                    3,  # min_messages_per_chat
                    8.5,  # median_messages_rounded
                    10.2,  # avg_messages_rounded
                    25,  # max_messages_per_chat
                    95,  # successful_operations
                    5,  # total_errors
                    50.0,  # error_rate_percent
                    15000,  # total_tokens
                    1.23,  # avg_execution_rounded
                    "2024-01-15T10:30:00Z",  # last_error_time
                ]
            ],
        }

        # Act. The display name is resolved from the assistant entity, not from the metric row, so
        # that lookup is stubbed here: this test's subject is field extraction, and leaving it live
        # would make the case depend on a database and on which assistants happen to exist in it.
        # Name resolution itself is covered by TestTheDisplayNameComesFromTheOwningEntity.
        with patch.object(
            handler.__class__,
            "_resolve_assistant_names",
            return_value=({"assistant-42": "test-assistant"}, False),
        ):
            rows = handler._parse_assistants_chats_result(result)

        # Assert
        assert len(rows) == 1
        row = rows[0]

        assert row["assistant"] == "test-assistant"
        assert row["total_chats"] == 10
        assert row["total_messages"] == 100
        assert row["unique_users"] == 5
        assert row["min_msg_per_chat"] == 3
        assert row["median_msg_per_chat"] == 8.5
        assert row["avg_msg_per_chat"] == 10.2
        assert row["max_msg_per_chat"] == 25
        assert row["success_ops"] == 95
        assert row["total_errors"] == 5
        assert row["error_rate_percent"] == 50.0
        assert row["total_tokens"] == 15000
        assert row["avg_time_seconds"] == 1.23
        assert row["last_error_timestamp"] == "2024-01-15T10:30:00Z"

        # The grouping id travels to the enricher, which is what keys the ledger read.
        assert row["assistant_id"] == "assistant-42"
        # Neither money cell is written here: they are not in the aggregation any more, and a
        # placeholder for an unread figure is the fabricated zero this surface used to emit.
        assert "total_cost_usd" not in row
        assert "cost_per_op_usd" not in row

    def test_parse_assistants_chats_result_handles_null_values(self, handler):
        """Verify None/null values are handled gracefully."""
        # Arrange
        result = {
            "columns": [
                {"name": "attributes.assistant_id.keyword", "type": "keyword"},
                {"name": "assistant_name", "type": "keyword"},
                {"name": "total_conversations", "type": "long"},
                {"name": "total_messages", "type": "long"},
                {"name": "unique_users", "type": "long"},
                {"name": "min_messages_per_chat", "type": "long"},
                {"name": "median_messages_rounded", "type": "double"},
                {"name": "avg_messages_rounded", "type": "double"},
                {"name": "max_messages_per_chat", "type": "long"},
                {"name": "successful_operations", "type": "long"},
                {"name": "total_errors", "type": "long"},
                {"name": "error_rate_percent", "type": "double"},
                {"name": "total_tokens", "type": "long"},
                {"name": "avg_execution_rounded", "type": "double"},
            ],
            "values": [
                [
                    "assistant-42",
                    "test-assistant",
                    10,
                    100,
                    5,
                    3,
                    8.5,
                    10.2,
                    25,
                    95,
                    0,  # no errors
                    None,  # null error_rate
                    15000,
                    None,  # null avg_execution
                ]
            ],
        }

        # Act
        rows = handler._parse_assistants_chats_result(result)

        # Assert
        assert len(rows) == 1
        row = rows[0]

        # Null values should be converted to 0 or None
        assert row["error_rate_percent"] == 0
        assert row["avg_time_seconds"] == 0
        assert row["last_error_timestamp"] is None

    def test_parse_assistants_chats_result_falls_back_to_id_when_name_unresolved(self, handler):
        """AD-29: a deleted assistant (no name ever emitted for its id) still renders as a
        bucket - as the id itself - rather than being dropped and truncating the total."""
        result = {
            "columns": [
                {"name": "attributes.assistant_id.keyword", "type": "keyword"},
                {"name": "assistant_name", "type": "keyword"},
                {"name": "total_conversations", "type": "long"},
                {"name": "total_messages", "type": "long"},
                {"name": "unique_users", "type": "long"},
                {"name": "min_messages_per_chat", "type": "long"},
                {"name": "median_messages_rounded", "type": "double"},
                {"name": "avg_messages_rounded", "type": "double"},
                {"name": "max_messages_per_chat", "type": "long"},
                {"name": "successful_operations", "type": "long"},
                {"name": "total_errors", "type": "long"},
                {"name": "error_rate_percent", "type": "double"},
                {"name": "total_tokens", "type": "long"},
                {"name": "avg_execution_rounded", "type": "double"},
            ],
            "values": [
                [
                    "assistant-deleted-99",
                    None,  # assistant_name never resolved
                    2,
                    4,
                    1,
                    2,
                    2.0,
                    2.0,
                    2,
                    4,
                    0,
                    0.0,
                    100,
                    0.5,
                ]
            ],
        }

        rows = handler._parse_assistants_chats_result(result)

        assert rows[0]["assistant"] == "assistant-deleted-99"


class TestGetAgentsUsage:
    """Tests for get_agents_usage method."""

    def test_get_agents_usage_aggregation_structure(self, handler):
        """Verify complex multi-bucket aggregation for agents."""
        # Arrange
        query = {"bool": {"filter": []}}

        # Act
        agg_body = handler._build_agents_usage_aggregation(query, fetch_size=30)

        # Assert
        assert agg_body["query"] == query
        assert agg_body["size"] == 0
        assert "aggs" in agg_body

        # Verify paginated_results structure
        assert "paginated_results" in agg_body["aggs"]

        # Verify terms aggregation groups by assistant_name
        assistants_agg = agg_body["aggs"]["paginated_results"]
        assert assistants_agg["terms"]["field"] == "attributes.assistant_name.keyword"
        assert assistants_agg["terms"]["size"] == 30
        assert assistants_agg["terms"]["order"] == {"1-bucket": "desc"}

        # Verify the counting sub-aggregations exist. The money bucket is not among them: built with
        # no coverage floor, this table serves no money of its own -
        # `TestTheAgentsUsageAggregationCarriesMoneyOnlyBelowTheFloor` owns that half.
        assert "1-bucket" in assistants_agg["aggs"]
        assert "3-bucket" in assistants_agg["aggs"]
        assert "4-bucket" in assistants_agg["aggs"]
        assert "5-bucket" in assistants_agg["aggs"]
        assert "6-bucket" in assistants_agg["aggs"]
        assert AGENTS_USAGE_MONEY_BUCKET not in assistants_agg["aggs"]

        # Verify filters for specific metric types
        assert (
            assistants_agg["aggs"]["1-bucket"]["filter"]["bool"]["filter"][0]["bool"]["should"][0]["term"][
                "metric_name.keyword"
            ]["value"]
            == MetricName.CONVERSATION_ASSISTANT_USAGE.value
        )
        assert (
            assistants_agg["aggs"]["4-bucket"]["filter"]["bool"]["filter"][0]["bool"]["should"][0]["term"][
                "metric_name.keyword"
            ]["value"]
            == MetricName.CODEMIE_TOOLS_USAGE_TOTAL.value
        )

        # Note: The assistant handler implementation doesn't add cardinality yet
        # This is expected behavior for this handler

    def test_the_aggregation_resolves_no_assistant_id_for_the_name_it_grouped(self, handler):
        """The refused design, still refused.

        A `terms` sub-aggregation on `attributes.assistant_id` with `"size": 1` was here to key the port
        per name bucket. It double-counted a rename and silently truncated a name collision, so a name
        is now resolved against the assistants directory instead - and a name that is not exactly one
        live assistant gets no figure at all. Nothing in the metric documents decides an id.
        """
        sub_aggs = handler._build_agents_usage_aggregation({}, fetch_size=30, pre_floor=FLOOR)["aggs"][
            "paginated_results"
        ]["aggs"]

        assert "assistant_id" not in sub_aggs[AGENTS_USAGE_MONEY_BUCKET].get("aggs", {})
        assert "attributes.assistant_id.keyword" not in json.dumps(sub_aggs)

    def test_parse_agents_usage_result_extracts_metrics(self, handler):
        """Verify agent usage metrics are extracted."""
        # Arrange
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": "test-assistant",
                            "1-bucket": {"doc_count": 100},
                            "2-bucket": {
                                "2-metric": {"value": 45.6789},
                                "money_settled": {"value": 100},
                                "money_unsettled": {"doc_count": 0},
                            },
                            "3-bucket": {"3-metric": {"value": 10}},
                            "4-bucket": {"4-metric": {"value": 5}},
                            "5-bucket": {"doc_count": 3},
                            "6-bucket": {
                                "6-metric": {"top": [{"metrics": {"attributes.error_class.keyword": "TimeoutError"}}]}
                            },
                        }
                    ]
                },
                "total_buckets": {"value": 1},
            }
        }

        # Act
        rows = handler._parse_agents_usage_result(result)

        # Assert
        assert len(rows) == 1
        row = rows[0]

        assert row["assistant_name"] == "test-assistant"
        assert row["total_conversations"] == 100
        # Re-baselined twice, and this is where it landed. The parser carries neither a bare money cell
        # (that sum was a settled remainder published as exact) nor the `_assistant_id` the discarded
        # id-keyed design resolved per name bucket. It carries the money bucket's **read** - the sum
        # plus what its coverage counts say about it - and the renderer turns that into cells.
        assert "total_cost" not in row
        assert "_assistant_id" not in row
        assert row["_money"].figure == 45.68
        assert row["_money"].is_exact
        assert row["unique_users"] == 10
        assert row["unique_tools_used"] == 5
        assert row["tool_errors"] == 3
        assert row["last_error"] == "TimeoutError"

    def test_parse_agents_usage_result_handles_no_errors(self, handler):
        """Verify last_error defaults to N/A when no errors."""
        # Arrange
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": "test-assistant",
                            "1-bucket": {"doc_count": 100},
                            "2-bucket": {"2-metric": {"value": 45.6789}},
                            "3-bucket": {"3-metric": {"value": 10}},
                            "4-bucket": {"4-metric": {"value": 5}},
                            "5-bucket": {"doc_count": 0},
                            "6-bucket": {"6-metric": {"top": []}},
                        }
                    ]
                },
                "total_buckets": {"value": 1},
            }
        }

        # Act
        rows = handler._parse_agents_usage_result(result)

        # Assert
        assert len(rows) == 1
        assert rows[0]["last_error"] == "N/A"


class TestTheDisplayNameComesFromTheOwningEntity:
    """AD-29: a renamed entity renders its CURRENT name; a deleted one renders its id.

    Grouping by the stable id fixes the buckets. Labelling those buckets from the same metric rows
    would leave the labels wrong after a rename — the harder of the two defects to notice, because
    the numbers are right.
    """

    def test_a_renamed_assistant_renders_its_current_name_not_the_stored_one(self):
        from codemie.service.analytics.handlers.assistant_handler import AssistantHandler

        assert (
            AssistantHandler._display_name(
                assistant_id="a-1",
                current_names={"a-1": "Renamed Assistant"},
                resolution_failed=False,
                name_in_metrics="Old Name",
            )
            == "Renamed Assistant"
        )

    def test_a_deleted_assistant_renders_its_id_and_not_the_stale_metric_name(self):
        """Presenting the stale name would claim an entity still exists under it."""
        from codemie.service.analytics.handlers.assistant_handler import AssistantHandler

        assert (
            AssistantHandler._display_name(
                assistant_id="a-gone",
                current_names={},
                resolution_failed=False,
                name_in_metrics="Deleted Assistant",
            )
            == "a-gone"
        )

    def test_a_failed_lookup_degrades_to_the_metric_name_rather_than_a_screen_of_ids(self):
        """A different situation from 'not found', so it must not render the same way."""
        from codemie.service.analytics.handlers.assistant_handler import AssistantHandler

        assert (
            AssistantHandler._display_name(
                assistant_id="a-1",
                current_names={},
                resolution_failed=True,
                name_in_metrics="Some Name",
            )
            == "Some Name"
        )

    def test_a_lookup_failure_is_reported_and_never_raised(self):
        """A name lookup failing degrades the report's labels; it never fails the report."""
        from codemie.service.analytics.handlers.assistant_handler import AssistantHandler

        with patch(
            "codemie.rest_api.models.assistant.Assistant.get_by_ids_no_permission_check",
            side_effect=RuntimeError("database is down"),
        ):
            names, failed = AssistantHandler._resolve_assistant_names(["a-1", "a-2"])

        assert names == {}
        assert failed is True

    def test_ids_are_deduplicated_and_blanks_dropped_before_the_lookup(self):
        from codemie.service.analytics.handlers.assistant_handler import AssistantHandler

        with patch(
            "codemie.rest_api.models.assistant.Assistant.get_by_ids_no_permission_check",
            return_value=[],
        ) as lookup:
            AssistantHandler._resolve_assistant_names(["a-1", "a-1", "", None, "a-2"])

        lookup.assert_called_once_with(["a-1", "a-2"])

    def test_no_ids_makes_no_query(self):
        from codemie.service.analytics.handlers.assistant_handler import AssistantHandler

        with patch("codemie.rest_api.models.assistant.Assistant.get_by_ids_no_permission_check") as lookup:
            assert AssistantHandler._resolve_assistant_names([]) == ({}, False)
        lookup.assert_not_called()


def _candidate(assistant_id: str, *, success_ops: int = 10) -> dict:
    """One parsed candidate row, carrying only what the enricher reads."""
    return {
        "assistant_id": assistant_id,
        "assistant": f"name-of-{assistant_id}",
        "success_ops": success_ops,
    }


def _patched_assistants_port(by_assistant_id: dict[str, SpendResult]):
    """The spend port, answering per assistant id over the report's own window.

    **`access` is asserted on every call rather than merely accepted**, for the reason the port makes
    it a required keyword: both assistant tables read the same dimension, and a double that took the
    argument loosely would let one of them thread its boundary while the other quietly did not. The same
    holds for `report_filter`, which is required here so a table that dropped the dashboard's filter fails.
    """

    async def _call(period, dimension, values, *, access, report_filter):
        assert period == PERIOD
        assert dimension is SpendDimension.ASSISTANT
        assert access == _EXPECTED_SCOPE, "the figure must be narrowed by the caller's own boundary"
        return {value: by_assistant_id[value] for value in values if value in by_assistant_id}

    return patch(PORT, new=AsyncMock(side_effect=_call))


def _money_bucket(*, money: float | None = 12.5, settled: int | None = 5, unsettled: int | None = 0) -> dict:
    """A below-floor money sub-aggregation as Elasticsearch returns it.

    `money=None` stands for a bucket answered with no sum at all; `settled=0` for one where no document
    in the window ever resolved a cost.
    """
    measured: dict = {}
    if money is not None:
        measured[AGENTS_USAGE_MONEY_SUM_AGG] = {"value": money}
    if settled is not None:
        measured[SETTLED_AGG] = {"value": settled}
    if unsettled is not None:
        measured[UNSETTLED_AGG] = {"doc_count": unsettled}
    return {AGENTS_USAGE_MONEY_BUCKET: measured}


def _agents_usage_bucket(name: str, *, conversations: int = 3, pre_floor: dict | None = None) -> dict:
    """One `get_agents_usage` name bucket, carrying the below-floor money sub-bucket when there is one.

    A bucket built without `pre_floor` is what a query built without a floor comes back as: the table
    measures no money of its own where the ledger covers the whole window.
    """
    return {
        "key": name,
        "1-bucket": {"doc_count": conversations},
        "3-bucket": {"3-metric": {"value": 2}},
        "4-bucket": {"4-metric": {"value": 1}},
        "5-bucket": {"doc_count": 0},
        "6-bucket": {"6-metric": {"top": []}},
        **(pre_floor or {}),
    }


def _agents_usage_result(buckets: list[dict]) -> dict:
    return {"aggregations": {"paginated_results": {"buckets": buckets}}}


def _directory(**ids_by_name: str | list[str]):
    """The assistants directory, answering with every LIVE id carrying each name."""
    pairs = [(one_id, name) for name, ids in ids_by_name.items() for one_id in ([ids] if isinstance(ids, str) else ids)]
    return patch(ASSISTANT_DIRECTORY, return_value=pairs)


def _patched_port(by_assistant_id: dict[str, SpendResult], *, expect_period: TimeRange | None = PERIOD):
    """The spend port, answering per assistant id over the leg it is handed.

    **`access` is asserted on every call rather than merely accepted**, for the reason the port makes it
    a required keyword: a double that took the argument loosely would let one table thread its boundary
    while another quietly did not.
    """

    async def _call(period, dimension, values, *, access, report_filter):
        if expect_period is not None:
            assert period == expect_period
        assert dimension is SpendDimension.ASSISTANT
        assert access == _EXPECTED_SCOPE, "the figure must be narrowed by the caller's own boundary"
        return {value: by_assistant_id[value] for value in values if value in by_assistant_id}

    return patch(PORT, new=AsyncMock(side_effect=_call))


@contextlib.contextmanager
def _cutover_at(floor: datetime | None, *, enabled: bool = True):
    """Publish `floor` as the **registered** coverage floor for the body of a test.

    Patched at `ledger_coverage()` rather than by setting config alone, because that is the whole of
    what this handler is required to read. `floor=None` is the unactivated ledger: nothing splits.
    """
    coverage = LedgerCoverage(coverage_floor=floor, ready=floor is not None)
    with (
        patch(REGISTERED_COVERAGE, return_value=coverage),
        patch.object(config, "SPEND_LEDGER_LEGACY_BELOW_FLOOR", enabled),
    ):
        yield


def _agents_usage_candidate(name: str, *, conversations: int = 3, pre_floor: RenderedSpend | None = None) -> dict:
    """A parsed `get_agents_usage` row as the renderer receives it."""
    return {
        "assistant_name": name,
        "total_conversations": conversations,
        AGENTS_USAGE_MONEY_KEY: render_spend(None) if pre_floor is None else pre_floor,
    }


def _measured(money: float) -> RenderedSpend:
    return RenderedSpend(figure=money, unavailable=False, partial=False, missing_amount=None)


class TestAgentsUsageMoneyBelowTheFloorIsNameKeyedAndQualified:
    """Below the coverage floor this table serves its own money, from the same name buckets its rows
    come from, and says what that sum is worth.

    The ledger holds nothing below `F`, so the figure there can only be the bucket's own
    `attributes.money_spent` sum - and that sum is a settled remainder, so it is qualified by the
    coverage counts measured beside it rather than published bare.
    """

    async def _render(self, handler, buckets: list[dict], *, pre_floor: datetime | None = BELOW_FLOOR_ONLY):
        """The real path: parse the buckets, then render the money column and its footer."""
        rows = handler._parse_agents_usage_result(_agents_usage_result(buckets))
        return await handler._render_agents_usage_money(rows, PERIOD_START, PERIOD_END, pre_floor)

    @pytest.mark.asyncio
    async def test_below_the_floor_each_name_bucket_keeps_its_own_money(self, handler):
        """`asst-42`, renamed mid-window, $120.00 of true spend split across its two names.

        Each name bucket holds its own documents and nothing else, so the two figures are disjoint and
        the footer is the money that was actually spent - not one id's whole spend counted twice.
        """
        with _directory(**{"Helper": "asst-42", "Assistant Helper": "asst-42"}) as directory, _patched_port({}) as port:
            enriched = await self._render(
                handler,
                [
                    _agents_usage_bucket("Helper", pre_floor=_money_bucket(money=70.0, settled=4)),
                    _agents_usage_bucket("Assistant Helper", pre_floor=_money_bucket(money=50.0, settled=3)),
                ],
            )

        port.assert_not_awaited()
        directory.assert_not_called()
        assert [row["total_cost"] for row in enriched.rows] == [70.0, 50.0]
        assert enriched.totals["total_cost"] == 120.0

    @pytest.mark.asyncio
    async def test_above_the_floor_the_current_name_takes_the_ledger_figure_and_the_other_takes_none(self, handler):
        """The other truth of the same rename, at or above `F`: the ledger keys on the id, and the id
        carries exactly one current name. The old name resolves to no live assistant, so it gets no
        figure and a label rather than a share of someone else's money."""
        with (
            _directory(**{"New Name": "asst-42"}),
            _patched_port({"asst-42": SpendResult(figure=Decimal("9"))}, expect_period=PERIOD),
        ):
            enriched = await self._render(
                handler,
                [_agents_usage_bucket("Old Name"), _agents_usage_bucket("New Name")],
                pre_floor=ABOVE_FLOOR_ONLY,
            )

        by_name = {row["assistant_name"]: row for row in enriched.rows}
        assert by_name["New Name"]["total_cost"] == 9.0
        assert by_name["Old Name"]["total_cost"] is None
        assert by_name["Old Name"]["spend_unavailable"] is True
        assert enriched.totals["total_cost"] == 9.0

    @pytest.mark.asyncio
    async def test_a_name_collision_below_the_floor_loses_nothing_because_the_bucket_holds_both(self, handler):
        """Two assistants both displayed "Reviewer": `asst-A` $90.00 and `asst-B` $410.00. One name
        bucket holds both assistants' documents, so its sum holds both amounts."""
        enriched = await self._render(handler, [_agents_usage_bucket("Reviewer", pre_floor=_money_bucket(money=500.0))])

        assert enriched.rows[0]["total_cost"] == 500.0
        assert enriched.totals["total_cost"] == 500.0

    @pytest.mark.asyncio
    async def test_nothing_settled_in_a_bucket_is_no_figure_and_never_a_zero(self, handler):
        """No document in the window resolved a cost, which says nothing about whether money was spent."""
        enriched = await self._render(
            handler, [_agents_usage_bucket("Quiet", pre_floor=_money_bucket(money=0.0, settled=0, unsettled=4))]
        )

        row = enriched.rows[0]
        assert row["total_cost"] is None
        assert row["spend_unavailable"] is True
        assert row["spend_partial"] is False
        # The row survives: it is an activity record, and its conversations are real either way.
        assert row["total_conversations"] == 3

    @pytest.mark.asyncio
    async def test_some_unsettled_publishes_the_subtotal_and_marks_it_partial(self, handler):
        """The middle state, and the reason the coverage counts exist: a remainder that says so."""
        enriched = await self._render(
            handler, [_agents_usage_bucket("Busy", pre_floor=_money_bucket(money=12.5, settled=3, unsettled=2))]
        )

        row = enriched.rows[0]
        assert row["total_cost"] == 12.5
        assert row["spend_partial"] is True
        assert row["spend_unavailable"] is False
        # The unsettled documents carry no money, so the size of the hole is genuinely unknown.
        assert row["spend_missing_amount"] is None

    @pytest.mark.asyncio
    async def test_everything_settled_publishes_the_exact_figure_unqualified(self, handler):
        enriched = await self._render(
            handler, [_agents_usage_bucket("Complete", pre_floor=_money_bucket(money=12.5, settled=5, unsettled=0))]
        )

        row = enriched.rows[0]
        assert row["total_cost"] == 12.5
        assert row["spend_unavailable"] is False
        assert row["spend_partial"] is False
        assert row["spend_missing_amount"] is None

    @pytest.mark.asyncio
    async def test_a_measured_zero_stays_an_unqualified_zero(self, handler):
        """Documents settled and their costs summed to nothing. That is a measurement, not an absence."""
        enriched = await self._render(
            handler, [_agents_usage_bucket("Free", pre_floor=_money_bucket(money=0.0, settled=5, unsettled=0))]
        )

        row = enriched.rows[0]
        assert row["total_cost"] == 0.0
        assert row["spend_unavailable"] is False
        assert row["spend_partial"] is False

    @pytest.mark.asyncio
    async def test_a_bucket_whose_coverage_was_never_measured_is_published_partial_not_bare(self, handler):
        """An older response shape cannot certify a sum whole, so the unknown surfaces."""
        enriched = await self._render(
            handler, [_agents_usage_bucket("Legacy", pre_floor=_money_bucket(money=9.0, settled=None, unsettled=None))]
        )

        assert enriched.rows[0]["total_cost"] == 9.0
        assert enriched.rows[0]["spend_partial"] is True

    @pytest.mark.asyncio
    async def test_the_private_money_key_is_consumed_and_never_reaches_the_response(self, handler):
        enriched = await self._render(handler, [_agents_usage_bucket("Helper", pre_floor=_money_bucket())])

        assert AGENTS_USAGE_MONEY_KEY not in enriched.rows[0]
        assert enriched.rows[0]["assistant_name"] == "Helper"

    @pytest.mark.asyncio
    async def test_the_spend_port_is_not_asked_below_the_floor(self, handler):
        """One live source per leg. Two would be two "Total Cost ($)" answers for one row."""
        with _patched_port({}) as port:
            await self._render(handler, [_agents_usage_bucket("Helper", pre_floor=_money_bucket())])

        port.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_row_order_the_aggregation_produced_is_left_alone(self, handler):
        """This is an activity table and money was never its sort key - re-ranking it would change a
        screen this work did not touch."""
        enriched = await self._render(
            handler,
            [
                _agents_usage_bucket("Cheap", pre_floor=_money_bucket(money=1.0)),
                _agents_usage_bucket("Expensive", pre_floor=_money_bucket(money=99.0)),
            ],
        )

        assert [row["assistant_name"] for row in enriched.rows] == ["Cheap", "Expensive"]

    @pytest.mark.asyncio
    async def test_the_renderer_owns_the_money_column_so_one_writer_fills_the_footer(self, handler):
        enriched = await self._render(handler, [_agents_usage_bucket("Helper", pre_floor=_money_bucket())])

        assert enriched.columns == ("total_cost",)

    @pytest.mark.asyncio
    async def test_a_footer_over_no_settled_bucket_at_all_omits_the_money_key(self, handler):
        """A sum of nothing must not present itself as $0.00 in the footer either."""
        enriched = await self._render(
            handler,
            [
                _agents_usage_bucket("Quiet", pre_floor=_money_bucket(money=0.0, settled=0, unsettled=1)),
                _agents_usage_bucket("Also Quiet", pre_floor=_money_bucket(money=0.0, settled=0, unsettled=2)),
            ],
        )

        assert "total_cost" not in enriched.totals
        assert enriched.totals["total_cost_spend_unavailable"] is True

    @pytest.mark.asyncio
    async def test_the_endpoint_runs_end_to_end_and_publishes_the_qualified_column(self, handler, mock_repository):
        """The whole table, through the pipeline, over a window wholly below the floor."""
        mock_repository.execute_aggregation_query = AsyncMock(
            return_value=_agents_usage_result(
                [
                    _agents_usage_bucket("Helper", pre_floor=_money_bucket(money=70.0, settled=4)),
                    _agents_usage_bucket("Quiet", pre_floor=_money_bucket(money=0.0, settled=0, unsettled=2)),
                ]
            )
        )

        with _cutover_at(EVERY_WINDOW_BELOW), _patched_port({}, expect_period=None) as port:
            response = await handler.get_agents_usage(time_period="last_30_days")

        port.assert_not_awaited()
        rows = response["data"]["rows"]
        assert [row["total_cost"] for row in rows] == [70.0, None]
        assert rows[1]["spend_unavailable"] is True
        assert response["data"]["totals"]["total_cost"] == 70.0
        assert response["data"]["totals"]["total_cost_spend_unavailable"] is True
        assert response["pagination"]["total_count"] == 2


class TestTheAgentsUsageAggregationCarriesMoneyOnlyBelowTheFloor:
    """Elasticsearch supplies conversations, users, tools and errors - and its own money only where the
    ledger has none, inside a bucket bounded at `F`."""

    def test_with_a_floor_the_only_money_sum_sits_in_a_bucket_bounded_below_it(self, handler):
        sub_aggs = handler._build_agents_usage_aggregation({"bool": {"filter": []}}, fetch_size=30, pre_floor=FLOOR)[
            "aggs"
        ]["paginated_results"]["aggs"]
        money = sub_aggs[AGENTS_USAGE_MONEY_BUCKET]

        assert money["filter"]["bool"]["filter"][0] == {
            "range": {TIMESTAMP_FIELD: {"lt": FLOOR.isoformat(), "format": "strict_date_optional_time"}}
        }
        # The same metric term the conversations beside it are counted over: one set of documents.
        assert money["filter"]["bool"]["filter"][1] == sub_aggs["1-bucket"]["filter"]
        assert money["aggs"][AGENTS_USAGE_MONEY_SUM_AGG] == {"sum": {"field": MONEY_SPENT_FIELD}}
        # The sum says whether it is whole.
        assert money["aggs"][SETTLED_AGG] == {"value_count": {"field": MONEY_SPENT_FIELD}}
        assert money["aggs"][UNSETTLED_AGG]["filter"]["bool"]["must_not"] == [{"exists": {"field": MONEY_SPENT_FIELD}}]
        # And it is the only money anywhere in the body.
        assert json.dumps(sub_aggs).count(MONEY_SPENT_FIELD) == 3  # the sum, the settled count, the unsettled exists

    def test_the_unsettled_count_is_not_narrowed_because_the_bucket_is_already_cost_bearing(self, handler):
        """`cost_bearing` exists for a sum whose scope is wider than the cost-bearing metrics.

        This bucket is filtered to `conversation_assistant_usage`, which is in `SPENDING_METRICS`, so
        every document in it is one a cost is expected on. A second filter here would narrow nothing and
        only give the builder and `money_coverage_aggs` a second place to disagree.
        """
        money = handler._build_agents_usage_aggregation({}, fetch_size=30, pre_floor=FLOOR)["aggs"][
            "paginated_results"
        ]["aggs"][AGENTS_USAGE_MONEY_BUCKET]

        assert MetricName.CONVERSATION_ASSISTANT_USAGE in MetricName.SPENDING_METRICS
        assert "filter" not in money["aggs"][UNSETTLED_AGG]["filter"]["bool"]

    def test_the_floor_instant_belongs_to_the_at_or_above_leg(self, handler):
        """`lt`, never `lte`: adjacent half-open ranges tile exactly, and the boundary row lands once."""
        money = handler._build_agents_usage_aggregation({}, fetch_size=30, pre_floor=FLOOR)["aggs"][
            "paginated_results"
        ]["aggs"][AGENTS_USAGE_MONEY_BUCKET]

        below_floor = money["filter"]["bool"]["filter"][0]["range"][TIMESTAMP_FIELD]
        assert "lt" in below_floor
        assert "lte" not in below_floor

    def test_without_a_floor_the_aggregation_names_no_money_field_at_all(self, handler):
        """Asserted on the serialised body, because that is where the rule can be broken silently: a
        re-added whole-window `sum` reaches a screen as a plausible number, and only the absence of the
        token itself proves the second source is gone."""
        body = json.dumps(handler._build_agents_usage_aggregation({"bool": {"filter": []}}, fetch_size=30))

        assert MONEY_SPENT_FIELD not in body
        assert AGENTS_USAGE_MONEY_BUCKET not in body

    def test_the_floor_leaves_every_other_bucket_exactly_as_it_was(self, handler):
        without = handler._build_agents_usage_aggregation({}, fetch_size=30)["aggs"]["paginated_results"]["aggs"]
        with_floor = handler._build_agents_usage_aggregation({}, fetch_size=30, pre_floor=FLOOR)["aggs"][
            "paginated_results"
        ]["aggs"]

        assert set(without) == {"1-bucket", "3-bucket", "4-bucket", "5-bucket", "6-bucket"}
        assert set(with_floor) == set(without) | {AGENTS_USAGE_MONEY_BUCKET}
        assert {name: with_floor[name] for name in without} == without


class TestAgentsUsageNameResolution:
    """The seam between a name and the ledger's id: exactly one live assistant, or nothing."""

    @pytest.mark.asyncio
    async def test_a_unique_live_name_reads_the_port_by_its_id(self, handler):
        rows = [_agents_usage_candidate("Helper")]

        with _directory(Helper="asst-42"), _patched_port({"asst-42": SpendResult(figure=Decimal("3"))}) as port:
            enriched = await handler._render_agents_usage_money(rows, PERIOD_START, PERIOD_END, ABOVE_FLOOR_ONLY)

        assert port.await_args.args[2] == ["asst-42"]
        assert enriched.rows[0]["total_cost"] == 3.0

    @pytest.mark.asyncio
    async def test_a_name_two_live_assistants_share_is_absent_and_never_asked_about(self, handler):
        """Placing the money in both buckets doubles it; placing it in one is a silently wrong bucket."""
        rows = [_agents_usage_candidate("Reviewer"), _agents_usage_candidate("Helper")]

        with (
            _directory(Reviewer=["asst-A", "asst-B"], Helper="asst-42"),
            _patched_port({"asst-42": SpendResult(figure=Decimal("3"))}) as port,
        ):
            enriched = await handler._render_agents_usage_money(rows, PERIOD_START, PERIOD_END, ABOVE_FLOOR_ONLY)

        assert port.await_args.args[2] == ["asst-42"]
        by_name = {row["assistant_name"]: row for row in enriched.rows}
        assert by_name["Reviewer"]["total_cost"] is None
        assert by_name["Reviewer"]["spend_unavailable"] is True
        assert by_name["Helper"]["total_cost"] == 3.0

    @pytest.mark.asyncio
    async def test_a_name_no_live_assistant_carries_is_absent(self, handler):
        """A deleted assistant's history keeps its row and its counts; its ledger money has no id to be
        read by."""
        rows = [_agents_usage_candidate("Deleted Assistant")]

        with _directory() as directory, _patched_port({}) as port:
            enriched = await handler._render_agents_usage_money(rows, PERIOD_START, PERIOD_END, ABOVE_FLOOR_ONLY)

        directory.assert_called_once()
        port.assert_not_awaited()
        assert enriched.rows[0]["assistant_name"] == "Deleted Assistant"
        assert enriched.rows[0]["total_cost"] is None
        assert enriched.rows[0]["spend_unavailable"] is True

    @pytest.mark.asyncio
    async def test_a_directory_failure_leaves_every_at_or_above_leg_absent_and_never_raises(self, handler):
        rows = [_agents_usage_candidate("Helper"), _agents_usage_candidate("Reviewer")]

        with (
            patch(ASSISTANT_DIRECTORY, side_effect=RuntimeError("database is down")),
            patch("codemie.service.analytics.handlers.assistant_handler.logger") as log,
            _patched_port({}) as port,
        ):
            enriched = await handler._render_agents_usage_money(rows, PERIOD_START, PERIOD_END, ABOVE_FLOOR_ONLY)

        port.assert_not_awaited()
        assert [row["assistant_name"] for row in enriched.rows] == ["Helper", "Reviewer"]
        assert all(row["total_cost"] is None and row["spend_unavailable"] is True for row in enriched.rows)
        # Worth a warning, unlike a name the directory simply does not hold.
        log.warning.assert_called_once()
        assert "database is down" in log.warning.call_args.args[0]

    def test_a_lookup_failure_is_reported_and_never_raised(self):
        with patch(ASSISTANT_DIRECTORY, side_effect=RuntimeError("database is down")):
            ids, failed = AssistantHandler._resolve_assistant_ids(["Helper"])

        assert ids == {}
        assert failed is True

    def test_the_lookup_is_unscoped_and_asks_only_about_the_names(self):
        """Ids never reach the wire and the money read is narrowed by the caller's boundary, so
        resolving a name discloses nothing; scoping it would instead break shared and global
        assistants, which are used from projects other than the one that owns them."""
        with patch(ASSISTANT_DIRECTORY, return_value=[]) as directory:
            AssistantHandler._resolve_assistant_ids(["Helper", "Helper", "", None, "Reviewer"])

        directory.assert_called_once_with(["Helper", "Reviewer"])

    def test_no_names_makes_no_query(self):
        with patch(ASSISTANT_DIRECTORY) as directory:
            assert AssistantHandler._resolve_assistant_ids([]) == ({}, False)
        directory.assert_not_called()

    def test_every_live_id_per_name_is_reported(self):
        with _directory(Reviewer=["asst-A", "asst-B"], Helper="asst-42"):
            ids, failed = AssistantHandler._resolve_assistant_ids(["Reviewer", "Helper", "Gone"])

        assert ids == {"Reviewer": ["asst-A", "asst-B"], "Helper": ["asst-42"]}
        assert failed is False


class TestAgentsUsageAcrossTheCoverageFloor:
    """Where a row's money comes from depends only on where the window sits relative to `F`."""

    @pytest.mark.asyncio
    async def test_a_window_wholly_below_the_floor_uses_the_aggregations_own_money(self, handler):
        rows = [_agents_usage_candidate("Helper", pre_floor=_measured(10.0))]

        with _directory(Helper="asst-42") as directory, _patched_port({}) as port:
            enriched = await handler._render_agents_usage_money(rows, PERIOD_START, PERIOD_END, BELOW_FLOOR_ONLY)

        port.assert_not_awaited()
        directory.assert_not_called()
        row = enriched.rows[0]
        assert row["total_cost"] == 10.0
        assert row["spend_unavailable"] is False
        assert row["spend_partial"] is False

    @pytest.mark.asyncio
    async def test_a_window_wholly_above_the_floor_reads_only_the_port(self, handler):
        """The pre-floor figure a builder may have attached is not this window's money."""
        rows = [_agents_usage_candidate("Helper", pre_floor=_measured(999.0))]

        with (
            _directory(Helper="asst-42"),
            _patched_port({"asst-42": SpendResult(figure=Decimal("4"))}, expect_period=PERIOD),
        ):
            enriched = await handler._render_agents_usage_money(rows, PERIOD_START, PERIOD_END, ABOVE_FLOOR_ONLY)

        assert enriched.rows[0]["total_cost"] == 4.0
        assert enriched.rows[0]["spend_unavailable"] is False

    @pytest.mark.asyncio
    async def test_the_kill_switch_off_keeps_the_above_floor_shape_over_the_whole_window(self, handler):
        rows = [_agents_usage_candidate("Helper", pre_floor=_measured(999.0))]

        with (
            _directory(Helper="asst-42"),
            _patched_port({"asst-42": SpendResult(figure=Decimal("4"))}, expect_period=PERIOD),
        ):
            enriched = await handler._render_agents_usage_money(rows, PERIOD_START, PERIOD_END, None)

        assert enriched.rows[0]["total_cost"] == 4.0

    @pytest.mark.asyncio
    async def test_a_crossing_window_shows_one_seamless_total(self, handler):
        rows = [_agents_usage_candidate("Helper", pre_floor=_measured(10.0))]

        with (
            _directory(Helper="asst-42"),
            _patched_port({"asst-42": SpendResult(figure=Decimal("2.5"))}, expect_period=AT_OR_ABOVE_FLOOR_LEG),
        ):
            enriched = await handler._render_agents_usage_money(rows, PERIOD_START, PERIOD_END, FLOOR)

        row = enriched.rows[0]
        assert row["total_cost"] == 12.5
        assert row["spend_unavailable"] is False
        assert row["spend_partial"] is False
        assert enriched.totals["total_cost"] == 12.5

    @pytest.mark.asyncio
    async def test_a_crossing_window_asks_the_port_over_the_at_or_above_leg_only(self, handler):
        """Handing the port the whole window flags `[start, F)` unavailable on money the aggregation
        already measured there, and the row double-labels."""
        rows = [_agents_usage_candidate("Helper", pre_floor=_measured(10.0))]

        with (
            _directory(Helper="asst-42"),
            _patched_port({"asst-42": SpendResult(figure=Decimal("2.5"))}, expect_period=None) as port,
        ):
            await handler._render_agents_usage_money(rows, PERIOD_START, PERIOD_END, FLOOR)

        assert port.await_args.args[0] == AT_OR_ABOVE_FLOOR_LEG

    @pytest.mark.asyncio
    async def test_a_leg_that_found_no_figure_labels_the_row_rather_than_shortening_it(self, handler):
        rows = [_agents_usage_candidate("Helper", pre_floor=render_spend(None))]

        with (
            _directory(Helper="asst-42"),
            _patched_port({"asst-42": SpendResult(figure=Decimal("2.5"))}, expect_period=AT_OR_ABOVE_FLOOR_LEG),
        ):
            enriched = await handler._render_agents_usage_money(rows, PERIOD_START, PERIOD_END, FLOOR)

        row = enriched.rows[0]
        assert row["total_cost"] == 2.5
        assert row["spend_unavailable"] is True

    @pytest.mark.asyncio
    async def test_a_measured_zero_below_the_floor_is_still_a_measured_zero(self, handler):
        rows = [_agents_usage_candidate("Helper", pre_floor=_measured(0.0))]

        with _directory() as directory, _patched_port({}):
            enriched = await handler._render_agents_usage_money(rows, PERIOD_START, PERIOD_END, BELOW_FLOOR_ONLY)

        directory.assert_not_called()
        assert len(enriched.rows) == 1
        assert enriched.rows[0]["total_cost"] == 0.0
        assert enriched.rows[0]["spend_unavailable"] is False

    @pytest.mark.asyncio
    async def test_a_window_the_port_cannot_read_asks_nothing_and_fabricates_nothing(self, handler):
        """A reversed window is no window: every figure is absent, the pre-floor sum is not published
        for a range nobody asked about, and the private key still never reaches the response."""
        rows = [_agents_usage_candidate("Helper", pre_floor=_measured(10.0))]

        with _directory(Helper="asst-1"), _patched_port({}) as port:
            enriched = await handler._render_agents_usage_money(rows, PERIOD_END, PERIOD_START, BELOW_FLOOR_ONLY)

        port.assert_not_awaited()
        row = enriched.rows[0]
        assert row["total_cost"] is None
        assert row["spend_unavailable"] is True
        assert AGENTS_USAGE_MONEY_KEY not in row


class TestTheAgentsUsagePreFloorBoundaryIsReadOnce:
    """Where the boundary that decides the split comes from, and what turns it off."""

    def test_the_floor_is_the_registered_one(self, handler):
        with _cutover_at(FLOOR):
            assert handler._pre_floor_boundary() == FLOOR

    def test_the_kill_switch_off_means_no_boundary_at_all(self, handler):
        with _cutover_at(FLOOR, enabled=False):
            assert handler._pre_floor_boundary() is None

    def test_unactivated_coverage_publishes_no_boundary(self, handler):
        with _cutover_at(None):
            assert handler._pre_floor_boundary() is None

    def test_a_published_floor_is_ignored_while_the_ledger_is_not_ready(self, handler):
        coverage = LedgerCoverage(coverage_floor=FLOOR, ready=False)
        with (
            patch(REGISTERED_COVERAGE, return_value=coverage),
            patch.object(config, "SPEND_LEDGER_LEGACY_BELOW_FLOOR", True),
        ):
            assert handler._pre_floor_boundary() is None

    def test_the_assistant_and_workflow_tables_read_one_floor(self, handler, mock_user, mock_repository):
        """Both delegate the decision to `money_coverage.pre_floor_boundary` over the same registry, so
        two name-keyed tables cannot sit on opposite sides of one floor. Registered for real rather than
        patched, because the two handlers read the registry through two module names."""
        reset_ledgers()
        try:
            register_ledger_coverage(LedgerCoverage(coverage_floor=FLOOR, ready=True))
            with patch.object(config, "SPEND_LEDGER_LEGACY_BELOW_FLOOR", True):
                assert handler._pre_floor_boundary() == FLOOR
                assert WorkflowHandler(mock_user, mock_repository)._pre_floor_boundary() == FLOOR
        finally:
            reset_ledgers()

    @pytest.mark.asyncio
    async def test_the_boundary_is_read_once_per_request_and_handed_to_both_halves(self, handler):
        """The value scopes the money sub-aggregation and decides which legs the renderer runs; two
        reads a request apart could disagree, leaving money clipped at a floor the split no longer
        believes in."""
        captured: dict[str, object] = {}

        async def _execute(**kwargs):
            captured["agg"] = kwargs["agg_builder"]({"bool": {"filter": []}}, 10)
            captured["enricher"] = kwargs["row_enricher"]
            return {"data": {"rows": []}}

        with (
            _cutover_at(FLOOR),
            patch.object(handler._pipeline, "execute_enriched_tabular_query", new=AsyncMock(side_effect=_execute)),
        ):
            await handler.get_agents_usage(time_period="last_30_days")
            assert AGENTS_USAGE_MONEY_BUCKET in captured["agg"]["aggs"]["paginated_results"]["aggs"]

        # The renderer closes over the floor it was built with, so a change after the read does nothing.
        rows = [_agents_usage_candidate("Helper", pre_floor=_measured(10.0))]
        with _directory(Helper="asst-42"), _patched_port({}, expect_period=AT_OR_ABOVE_FLOOR_LEG) as port:
            enriched = await captured["enricher"](rows, PERIOD_START, PERIOD_END)
        assert port.await_args.args[0] == AT_OR_ABOVE_FLOOR_LEG
        assert enriched.rows[0]["total_cost"] == 10.0
        assert enriched.rows[0]["spend_unavailable"] is True


class TestTheAgentsUsageLegsMeasureTimeOnTwoDifferentClocks:
    """**Known, accepted limitation. These tests assert what this table does, not what it should do.**

    The legs tile the requested window as instants. They do not tile the *rows*: the pre-floor leg is an
    Elasticsearch aggregation clipped on `@timestamp`, metric **emission** time, while the at-or-above
    leg reads the ledger, which admits a row by `start_time`, when the call **started**. A call whose two
    stamps fall on opposite sides of `F` is claimed by neither leg or by both, and the row carries no
    flag and no portion either way: it foots short, or doubled, and reads exact. Inherited whole from the
    surfaces that split first, bounded by the emission lag `L`; the mitigation is to cut at a
    low-traffic hour.
    """

    @staticmethod
    async def _crossing_row(handler, *, below_floor_sum: float, above_floor: str) -> dict:
        rows = [_agents_usage_candidate("atlas", pre_floor=_measured(below_floor_sum))]
        with (
            _directory(atlas="asst-atlas"),
            _patched_port(
                {"asst-atlas": SpendResult(figure=Decimal(above_floor))}, expect_period=AT_OR_ABOVE_FLOOR_LEG
            ),
        ):
            enriched = await handler._render_agents_usage_money(rows, PERIOD_START, PERIOD_END, FLOOR)
        return enriched.rows[0]

    @pytest.mark.asyncio
    async def test_a_call_starting_before_the_floor_but_emitted_after_it_is_dropped_by_both_legs(self, handler):
        started_at = FLOOR - timedelta(minutes=10)
        assert FLOOR - DEFAULT_EMISSION_LAG <= started_at < FLOOR, "inside the band L bounds, by construction"

        row = await self._crossing_row(handler, below_floor_sum=10.0, above_floor="0")

        assert row["total_cost"] == 10.0, "KNOWN LIMITATION: 0.50 was spent inside the window, neither leg has it"
        assert row["spend_unavailable"] is False, "KNOWN LIMITATION: the shortfall raises no flag"
        assert row["spend_partial"] is False, "KNOWN LIMITATION: and labels no dimension"

    @pytest.mark.asyncio
    async def test_a_call_starting_after_the_floor_but_emitted_before_it_is_counted_by_both_legs(self, handler):
        row = await self._crossing_row(handler, below_floor_sum=0.5, above_floor="0.5")

        assert row["total_cost"] == 1.0, "KNOWN LIMITATION: 0.50 spent once, claimed by both legs"
        assert row["spend_unavailable"] is False, "KNOWN LIMITATION: a doubled figure presents as a correct one"
        assert row["spend_partial"] is False


class TestAssistantsChatsMoneyComesFromTheSpendPort:
    """AD-1/AD-14: the figure and the ratio come from the ledger, and every absence stays absent."""

    @pytest.mark.asyncio
    async def test_the_port_is_keyed_on_the_grouped_ids_not_the_display_names(self, handler):
        rows = [_candidate("assistant-42"), _candidate("assistant-7")]

        with _patched_assistants_port({}) as port:
            await handler._enrich_assistants_spending(rows, PERIOD_START, PERIOD_END)

        assert port.await_args.args[2] == ["assistant-42", "assistant-7"]
        # A name is mutable, so asking about it would name a bucket the aggregation never formed.
        assert "name-of-assistant-42" not in port.await_args.args[2]

    @pytest.mark.asyncio
    async def test_the_grouping_id_is_consumed_and_never_reaches_the_response(self, handler):
        rows = [_candidate("assistant-42")]

        with _patched_assistants_port({"assistant-42": SpendResult(figure=Decimal("3"))}):
            enriched = await handler._enrich_assistants_spending(rows, PERIOD_START, PERIOD_END)

        assert "assistant_id" not in enriched.rows[0]
        assert enriched.rows[0]["assistant"] == "name-of-assistant-42"

    @pytest.mark.asyncio
    async def test_a_measured_zero_renders_as_a_zero_and_the_row_survives(self, handler):
        """A spending table drops a zero row; this one must not - it is an activity record."""
        rows = [_candidate("assistant-42")]

        with _patched_assistants_port({"assistant-42": SpendResult(figure=Decimal("0"))}):
            enriched = await handler._enrich_assistants_spending(rows, PERIOD_START, PERIOD_END)

        assert len(enriched.rows) == 1
        assert enriched.rows[0]["total_cost_usd"] == 0.0
        assert enriched.rows[0]["cost_per_op_usd"] == 0.0
        assert enriched.rows[0]["spend_unavailable"] is False

    @pytest.mark.asyncio
    async def test_an_exact_figure_renders_the_ratio_and_clean_annotations(self, handler):
        rows = [_candidate("assistant-42", success_ops=8)]

        with _patched_assistants_port({"assistant-42": SpendResult(figure=Decimal("12.34"))}):
            enriched = await handler._enrich_assistants_spending(rows, PERIOD_START, PERIOD_END)

        row = enriched.rows[0]
        assert row["total_cost_usd"] == 12.34
        assert row["cost_per_op_usd"] == 1.5425
        assert row["spend_unavailable"] is False
        assert row["spend_partial"] is False
        assert row["spend_missing_amount"] is None
        assert enriched.totals["total_cost_usd"] == 12.34

    @pytest.mark.asyncio
    async def test_an_absent_figure_renders_no_number_anywhere_and_says_so(self, handler):
        """AD-23: no figure exists, so nothing numeric is emitted for it - not the cost, not the ratio."""
        rows = [_candidate("assistant-42")]

        with _patched_assistants_port({"assistant-42": SpendResult.no_figure(PERIOD)}):
            enriched = await handler._enrich_assistants_spending(rows, PERIOD_START, PERIOD_END)

        row = enriched.rows[0]
        assert row["total_cost_usd"] is None
        assert row["cost_per_op_usd"] is None
        assert row["spend_unavailable"] is True
        # Not a single fabricated zero in the payload: the row, then the footer with no total to show.
        # Booleans are excluded because `False == 0.0` in Python, so the annotation flags would
        # otherwise read as fabricated figures and make this assertion unfalsifiable.
        assert 0.0 not in [value for value in row.values() if not isinstance(value, bool)]
        assert "total_cost_usd" not in enriched.totals
        assert enriched.totals["total_cost_usd_spend_unavailable"] is True

    @pytest.mark.asyncio
    async def test_a_partial_figure_renders_its_number_with_the_qualification(self, handler):
        rows = [_candidate("assistant-42", success_ops=4)]
        with_hole = SpendResult(
            figure=Decimal("8"),
            partial={SpendDimension.IDENTITY_BRIDGE: Portion(spend=None, rows=None)},
        )

        with _patched_assistants_port({"assistant-42": with_hole}):
            enriched = await handler._enrich_assistants_spending(rows, PERIOD_START, PERIOD_END)

        row = enriched.rows[0]
        assert row["total_cost_usd"] == 8.0
        # The ratio is computable and inherits the row's qualification rather than carrying its own.
        assert row["cost_per_op_usd"] == 2.0
        assert row["spend_partial"] is True
        assert row["spend_missing_amount"] is None

    @pytest.mark.asyncio
    async def test_no_successful_operations_yields_no_ratio_rather_than_a_zero(self, handler):
        """The fabricated zero this unit removes: `CASE(successful_operations > 0, ..., 0)`."""
        rows = [_candidate("assistant-42", success_ops=0)]

        with _patched_assistants_port({"assistant-42": SpendResult(figure=Decimal("5"))}):
            enriched = await handler._enrich_assistants_spending(rows, PERIOD_START, PERIOD_END)

        assert enriched.rows[0]["total_cost_usd"] == 5.0
        assert enriched.rows[0]["cost_per_op_usd"] is None

    @pytest.mark.asyncio
    async def test_the_money_order_puts_an_absent_figure_last_rather_than_at_zero(self, handler):
        """A **measured zero** stands between the figures and the absence, which is what makes this
        assertion falsifiable: ranking an absent figure as `0.0` ties it with the measured zero, and
        only their relative order tells the two apart."""
        rows = [_candidate("absent"), _candidate("zero"), _candidate("small"), _candidate("large")]
        answers = {
            "absent": SpendResult.no_figure(PERIOD),
            "zero": SpendResult(figure=Decimal("0")),
            "small": SpendResult(figure=Decimal("1")),
            "large": SpendResult(figure=Decimal("90")),
        }

        with _patched_assistants_port(answers):
            enriched = await handler._enrich_assistants_spending(rows, PERIOD_START, PERIOD_END)

        assert [row["assistant"] for row in enriched.rows] == [
            "name-of-large",
            "name-of-small",
            "name-of-zero",
            "name-of-absent",
        ]
        # The footer is short by the row nobody has a figure for, and says so rather than looking whole.
        assert enriched.totals["total_cost_usd"] == 91.0
        assert enriched.totals["total_cost_usd_spend_partial"] is True

    @pytest.mark.asyncio
    async def test_a_window_the_port_cannot_read_asks_nothing_and_fabricates_nothing(self, handler):
        rows = [_candidate("assistant-42")]

        with _patched_assistants_port({"assistant-42": SpendResult(figure=Decimal("7"))}) as port:
            enriched = await handler._enrich_assistants_spending(rows, PERIOD_START, PERIOD_START)

        port.assert_not_awaited()
        assert enriched.rows[0]["total_cost_usd"] is None
        assert enriched.rows[0]["cost_per_op_usd"] is None
        assert enriched.rows[0]["spend_unavailable"] is True

    @pytest.mark.asyncio
    async def test_the_enricher_owns_the_money_column_so_one_writer_fills_the_footer(self, handler):
        rows = [_candidate("assistant-42")]

        with _patched_assistants_port({"assistant-42": SpendResult(figure=Decimal("2"))}):
            enriched = await handler._enrich_assistants_spending(rows, PERIOD_START, PERIOD_END)

        assert enriched.columns == ("total_cost_usd",)

    def test_the_advertised_money_columns_are_unchanged(self, handler):
        """The column ids and labels the UI reads are the ones this surface always had."""
        money = {
            column["id"]: column["label"]
            for column in handler._get_assistants_chats_columns()
            if column["id"] in {"total_cost_usd", "cost_per_op_usd"}
        }

        assert money == {"total_cost_usd": "Total Cost ($)", "cost_per_op_usd": "Cost/Op ($)"}


ASSISTANT_LOOKUP = "codemie.rest_api.models.assistant.Assistant.get_by_ids_no_permission_check"


def _top_agents_bucket(assistant_id: str, *, name_in_metrics: str = "Stale Name", invocations: int = 7) -> dict:
    """One `get_top_agents_usage` bucket, keyed on the id the aggregation groups by."""
    return {
        "key": assistant_id,
        "1-bucket": {"doc_count": invocations},
        "3-bucket": {"3-metric": {"value": 3}},
        "4-bucket": {"4-metric": {"top": [{"metrics": {"attributes.user_email.keyword": "someone@example.com"}}]}},
        "display_name": {"top": [{"metrics": {"attributes.assistant_name.keyword": name_in_metrics}}]},
    }


def _named_assistants(**by_id: str):
    """The assistant directory, answering with the entities' CURRENT names."""
    from types import SimpleNamespace

    return patch(
        ASSISTANT_LOOKUP,
        return_value=[SimpleNamespace(id=one, name=name) for one, name in by_id.items()],
    )


class TestTopAgentsUsageGroupsOnTheIdAndCarriesNoMoney:
    """AD-14/AD-29: Elasticsearch supplies invocations, users and labels - never a figure, never a key."""

    def test_the_aggregation_groups_on_the_id(self, handler):
        agg = handler._build_top_agents_usage_aggregation({"bool": {"filter": []}}, fetch_size=100)["aggs"][
            "paginated_results"
        ]

        assert agg["terms"]["field"] == "attributes.assistant_id.keyword"
        # Candidate order stays invocations: the money it could otherwise rank by now lives in the
        # ledger, and Elasticsearch cannot sort by a value it no longer holds.
        assert agg["terms"]["order"] == {"1-bucket": "desc"}

    def test_the_aggregation_names_no_money_field_at_all(self, handler):
        """Asserted on the serialised body, because that is where the rule can be broken silently:
        a re-added `sum` over `attributes.money_spent` reaches a screen as a plausible number, and
        only the absence of the token itself proves the second source is gone."""
        agg_body = handler._build_top_agents_usage_aggregation({"bool": {"filter": []}}, fetch_size=100)

        assert "money_spent" not in json.dumps(agg_body)
        # `2-bucket` held nothing but the money sum, so it is gone rather than emptied.
        sub_aggs = agg_body["aggs"]["paginated_results"]["aggs"]
        assert "2-bucket" not in sub_aggs
        assert set(sub_aggs) == {"1-bucket", "3-bucket", "4-bucket", "display_name"}

    def test_the_aggregation_carries_the_fallback_label(self, handler):
        """The name is fetched as a label of last resort, sorted by time - never as the grouping key."""
        sub_aggs = handler._build_top_agents_usage_aggregation({"bool": {"filter": []}}, fetch_size=100)["aggs"][
            "paginated_results"
        ]["aggs"]

        assert sub_aggs["display_name"]["top_metrics"]["metrics"] == {"field": "attributes.assistant_name.keyword"}
        assert sub_aggs["display_name"]["top_metrics"]["sort"] == {"@timestamp": "desc"}


class TestTopAgentsUsageRowsKeyOnTheId:
    """The defect keying on the id fixes, and the one it introduces, are both about which rows exist."""

    @staticmethod
    def _result(buckets: list[dict]) -> dict:
        return {"aggregations": {"paginated_results": {"buckets": buckets}}}

    def test_a_row_keys_on_the_id_and_shows_the_assistants_current_name(self, handler):
        with _named_assistants(**{"a-1": "Release Notes Bot"}):
            rows = handler._parse_top_agents_usage_result(self._result([_top_agents_bucket("a-1")]))

        assert rows[0]["assistant_id"] == "a-1"
        assert rows[0]["assistant_name"] == "Release Notes Bot"
        assert rows[0]["invocations"] == 7
        assert "money_spent" not in rows[0]

    def test_a_renamed_assistant_stays_one_row_under_its_current_name(self, handler):
        """A rename cannot be reproduced against a real index here, so it is pinned where it is
        expressible: one bucket, whose metric documents still carry the **old** name, renders one row
        labelled with the name the assistant has now. Under name grouping that history is two
        buckets - which is what AD-29 calls no bucket assignment at all."""
        bucket = _top_agents_bucket("a-1", name_in_metrics="Name Before The Rename")

        with _named_assistants(**{"a-1": "Name After The Rename"}):
            rows = handler._parse_top_agents_usage_result(self._result([bucket]))

        assert [row["assistant_name"] for row in rows] == ["Name After The Rename"]

    def test_two_assistants_sharing_one_name_stay_two_rows(self, handler):
        """The other half of the defect: name grouping merges these two into a single bucket."""
        buckets = [
            _top_agents_bucket("a-1", name_in_metrics="Helper"),
            _top_agents_bucket("a-2", name_in_metrics="Helper"),
        ]

        with _named_assistants(**{"a-1": "Helper", "a-2": "Helper"}):
            rows = handler._parse_top_agents_usage_result(self._result(buckets))

        assert [row["assistant_id"] for row in rows] == ["a-1", "a-2"]
        assert [row["assistant_name"] for row in rows] == ["Helper", "Helper"]

    def test_a_deleted_assistant_renders_its_id_and_not_the_stale_metric_name(self, handler):
        with patch(ASSISTANT_LOOKUP, return_value=[]):
            rows = handler._parse_top_agents_usage_result(
                self._result([_top_agents_bucket("a-gone", name_in_metrics="Deleted Assistant")])
            )

        assert rows[0]["assistant_name"] == "a-gone"

    def test_a_failed_lookup_degrades_to_the_metric_name_rather_than_a_screen_of_ids(self, handler):
        with patch(ASSISTANT_LOOKUP, side_effect=RuntimeError("database is down")):
            rows = handler._parse_top_agents_usage_result(
                self._result([_top_agents_bucket("a-1", name_in_metrics="Last Known Name")])
            )

        assert rows[0]["assistant_name"] == "Last Known Name"

    def test_a_bucket_with_no_label_at_all_still_renders_its_id(self, handler):
        """The sub-aggregation matching no document is not an error: the id is always a valid label."""
        bucket = _top_agents_bucket("a-1")
        bucket["display_name"] = {"top": []}

        with patch(ASSISTANT_LOOKUP, side_effect=RuntimeError("database is down")):
            rows = handler._parse_top_agents_usage_result(self._result([bucket]))

        assert rows[0]["assistant_name"] == "a-1"


class TestTopAgentsUsageMoneyComesFromTheSpendPort:
    """AD-1/AD-14: the figure comes from the ledger, and every absence stays absent."""

    @staticmethod
    def _candidate(assistant_id: str) -> dict:
        return {"assistant_id": assistant_id, "assistant_name": f"name-of-{assistant_id}", "invocations": 7}

    @pytest.mark.asyncio
    async def test_the_port_is_keyed_on_the_grouped_ids_not_the_display_names(self, handler):
        rows = [self._candidate("a-42"), self._candidate("a-7")]

        with _patched_assistants_port({}) as port:
            enriched = await handler._enrich_top_agents_usage_spending(rows, PERIOD_START, PERIOD_END)

        assert port.await_args.args[2] == ["a-42", "a-7"]
        assert "name-of-a-42" not in port.await_args.args[2]
        # The id travelled parser to enricher only, so the wire shape is the one it always was.
        assert "assistant_id" not in enriched.rows[0]

    @pytest.mark.asyncio
    async def test_an_exact_figure_fills_the_cell_the_footer_and_nothing_else(self, handler):
        rows = [self._candidate("a-42")]

        with _patched_assistants_port({"a-42": SpendResult(figure=Decimal("4.5"))}):
            enriched = await handler._enrich_top_agents_usage_spending(rows, PERIOD_START, PERIOD_END)

        assert enriched.rows[0]["money_spent"] == 4.5
        assert enriched.rows[0]["spend_unavailable"] is False
        assert enriched.rows[0]["spend_partial"] is False
        assert enriched.columns == ("money_spent",)
        assert enriched.totals["money_spent"] == 4.5

    @pytest.mark.asyncio
    async def test_an_absent_figure_renders_no_number_anywhere_and_says_so(self, handler):
        """AD-23: no figure exists, so nothing numeric is emitted for it."""
        rows = [self._candidate("a-42")]

        with _patched_assistants_port({"a-42": SpendResult.no_figure(PERIOD)}):
            enriched = await handler._enrich_top_agents_usage_spending(rows, PERIOD_START, PERIOD_END)

        row = enriched.rows[0]
        assert row["money_spent"] is None
        assert row["spend_unavailable"] is True
        assert row["spend_missing_amount"] is None
        assert "money_spent" not in enriched.totals
        assert enriched.totals["money_spent_spend_unavailable"] is True

    @pytest.mark.asyncio
    async def test_a_partial_figure_renders_its_number_with_the_qualification(self, handler):
        rows = [self._candidate("a-42")]
        with_hole = SpendResult(
            figure=Decimal("8"),
            partial={SpendDimension.IDENTITY_BRIDGE: Portion(spend=None, rows=None)},
        )

        with _patched_assistants_port({"a-42": with_hole}):
            enriched = await handler._enrich_top_agents_usage_spending(rows, PERIOD_START, PERIOD_END)

        assert enriched.rows[0]["money_spent"] == 8.0
        assert enriched.rows[0]["spend_partial"] is True

    @pytest.mark.asyncio
    async def test_a_measured_zero_renders_as_a_zero_and_the_row_survives(self, handler):
        """A spending table drops a zero row; this one must not - it is an activity record."""
        rows = [self._candidate("a-42")]

        with _patched_assistants_port({"a-42": SpendResult(figure=Decimal("0"))}):
            enriched = await handler._enrich_top_agents_usage_spending(rows, PERIOD_START, PERIOD_END)

        assert len(enriched.rows) == 1
        assert enriched.rows[0]["money_spent"] == 0.0

    @pytest.mark.asyncio
    async def test_the_money_order_puts_an_absent_figure_last_rather_than_at_zero(self, handler):
        """A **measured zero** stands between the figures and the absence, which is what makes this
        assertion falsifiable: ranking an absent figure as `0.0` ties it with the measured zero, and
        only their relative order tells the two apart."""
        rows = [self._candidate("absent"), self._candidate("zero"), self._candidate("small"), self._candidate("large")]
        answers = {
            "absent": SpendResult.no_figure(PERIOD),
            "zero": SpendResult(figure=Decimal("0")),
            "small": SpendResult(figure=Decimal("1")),
            "large": SpendResult(figure=Decimal("90")),
        }

        with _patched_assistants_port(answers):
            enriched = await handler._enrich_top_agents_usage_spending(rows, PERIOD_START, PERIOD_END)

        assert [row["assistant_name"] for row in enriched.rows] == [
            "name-of-large",
            "name-of-small",
            "name-of-zero",
            "name-of-absent",
        ]
        # The footer is short by the row nobody has a figure for, and says so rather than looking whole.
        assert enriched.totals["money_spent"] == 91.0
        assert enriched.totals["money_spent_spend_partial"] is True

    @pytest.mark.asyncio
    async def test_a_window_the_port_cannot_read_asks_nothing_and_fabricates_nothing(self, handler):
        rows = [self._candidate("a-42")]

        with _patched_assistants_port({"a-42": SpendResult(figure=Decimal("7"))}) as port:
            enriched = await handler._enrich_top_agents_usage_spending(rows, PERIOD_START, PERIOD_START)

        port.assert_not_awaited()
        assert enriched.rows[0]["money_spent"] is None
        assert enriched.rows[0]["spend_unavailable"] is True

    def test_the_advertised_money_column_is_unchanged(self, handler):
        """The column id and label the UI reads are the ones this surface always had."""
        money = [column for column in handler._get_top_agents_usage_columns() if column["id"] == "money_spent"]

        assert money == [{"id": "money_spent", "label": "Money Spent ($)", "type": "number", "format": "currency"}]


class TestAClientSourceFilterReadsNoMoney:
    """No ledger row records which client a call came from, so a client-narrowed table's figure would be
    the unnarrowed one under the narrowed label. The port is not asked; the counts Elasticsearch narrowed
    stay, and every money cell is absent."""

    @pytest.mark.asyncio
    async def test_the_assistants_chats_table_asks_the_port_nothing(self, handler, mock_repository):
        values_by_column = {
            "attributes.assistant_id.keyword": "assistant-42",
            "assistant_name": "Helper",
            "total_conversations": 10,
            "total_messages": 100,
            "unique_users": 5,
            "min_messages_per_chat": 3,
            "median_messages_rounded": 8.5,
            "avg_messages_rounded": 10.2,
            "max_messages_per_chat": 25,
            "successful_operations": 95,
            "total_errors": 5,
            "error_rate_percent": 50.0,
            "total_tokens": 15000,
            "avg_execution_rounded": 1.23,
        }
        mock_repository.execute_esql_query.return_value = {
            "columns": [{"name": name} for name in values_by_column],
            "values": [list(values_by_column.values())],
        }

        with (
            _named_assistants(**{"assistant-42": "Helper"}),
            _patched_assistants_port({"assistant-42": SpendResult(figure=Decimal("12.34"))}) as port,
        ):
            result = await handler.get_assistants_chats(
                start_date=PERIOD_START, end_date=PERIOD_END, client_source="teams"
            )

        port.assert_not_awaited()
        row = result["data"]["rows"][0]
        assert row["total_cost_usd"] is None
        assert row["cost_per_op_usd"] is None
        assert row["spend_unavailable"] is True
        assert (row["total_messages"], row["total_tokens"]) == (100, 15000)
        assert "total_cost_usd" not in result["data"]["totals"]
        assert result["data"]["totals"]["total_cost_usd_spend_unavailable"] is True

    @pytest.mark.asyncio
    async def test_the_top_agents_table_asks_the_port_nothing(self, handler, mock_repository):
        mock_repository.execute_aggregation_query.return_value = {
            "aggregations": {"paginated_results": {"buckets": [_top_agents_bucket("a-42")]}}
        }

        with (
            _named_assistants(**{"a-42": "Helper"}),
            _patched_assistants_port({"a-42": SpendResult(figure=Decimal("4.5"))}) as port,
        ):
            result = await handler.get_top_agents_usage(
                start_date=PERIOD_START, end_date=PERIOD_END, client_source="teams"
            )

        port.assert_not_awaited()
        row = result["data"]["rows"][0]
        assert row["money_spent"] is None
        assert row["spend_unavailable"] is True
        assert (row["invocations"], row["unique_users"]) == (7, 3)
        assert "money_spent" not in result["data"]["totals"]
        assert result["data"]["totals"]["money_spent_spend_unavailable"] is True


class TestTheDashboardFilterReachesEveryMoneyRead:
    """RD-D1: each assistant table's money is narrowed by the request's own users and projects filter.

    Elasticsearch narrows the rows by it, but an assistant's ids select its calls in every project and
    for every person: a money read without the filter foots the filtered rows with the assistant's
    spend everywhere and sizes the "unattributed" residue on the whole window.
    """

    ASKED = ReportFilter(projects=("p1",))
    FILTER_FOR = "codemie.service.analytics.handlers.assistant_handler.report_filter_for"

    @pytest.mark.asyncio
    async def test_assistants_chats_builds_the_filter_from_the_request(self, handler):
        with (
            patch(self.FILTER_FOR, new=AsyncMock(return_value=self.ASKED)) as build,
            patch.object(handler._pipeline, "execute_esql_query", new=AsyncMock(return_value={})) as execute,
        ):
            await handler.get_assistants_chats(time_period="last_30_days", users=["u1"], projects=["p1"])

        build.assert_awaited_once_with(["u1"], ["p1"])
        assert execute.await_args.kwargs["row_enricher"].keywords["report_filter"] is self.ASKED

    @pytest.mark.asyncio
    async def test_top_agents_usage_builds_the_filter_from_the_request(self, handler):
        with (
            patch(self.FILTER_FOR, new=AsyncMock(return_value=self.ASKED)) as build,
            patch.object(
                handler._pipeline, "execute_enriched_tabular_query", new=AsyncMock(return_value={})
            ) as execute,
        ):
            await handler.get_top_agents_usage(time_period="last_30_days", users=["u1"], projects=["p1"])

        build.assert_awaited_once_with(["u1"], ["p1"])
        assert execute.await_args.kwargs["row_enricher"].keywords["report_filter"] is self.ASKED

    @pytest.mark.asyncio
    async def test_both_id_keyed_enrichers_hand_it_to_the_port(self, handler):
        with _patched_assistants_port({}) as port:
            await handler._enrich_assistants_spending(
                [_candidate("assistant-42")], PERIOD_START, PERIOD_END, report_filter=self.ASKED
            )
            await handler._enrich_top_agents_usage_spending(
                [{"assistant_id": "a-7", "assistant_name": "n", "invocations": 1}],
                PERIOD_START,
                PERIOD_END,
                report_filter=self.ASKED,
            )

        assert [call.kwargs["report_filter"] for call in port.await_args_list] == [self.ASKED, self.ASKED]

    @pytest.mark.asyncio
    async def test_agents_usage_hands_it_to_the_at_or_above_floor_leg(self, handler):
        """The name-keyed table reaches the port through its renderer, so the filter is closed over at
        the request and must arrive at the leg the port answers."""
        captured: dict[str, object] = {}

        async def _execute(**kwargs):
            captured["enricher"] = kwargs["row_enricher"]
            return {"data": {"rows": []}}

        with (
            _cutover_at(FLOOR),
            patch(self.FILTER_FOR, new=AsyncMock(return_value=self.ASKED)) as build,
            patch.object(handler._pipeline, "execute_enriched_tabular_query", new=AsyncMock(side_effect=_execute)),
        ):
            await handler.get_agents_usage(time_period="last_30_days", users=["u1"], projects=["p1"])

        build.assert_awaited_once_with(["u1"], ["p1"])
        rows = [_agents_usage_candidate("Helper", pre_floor=_measured(10.0))]
        with _directory(Helper="asst-42"), _patched_port({}, expect_period=AT_OR_ABOVE_FLOOR_LEG) as port:
            await captured["enricher"](rows, PERIOD_START, PERIOD_END)
        assert port.await_args.kwargs["report_filter"] is self.ASKED
