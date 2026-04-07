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

"""Unit tests for leaderboard collector helpers and ES aggregation logic."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.service.leaderboard.collector import (
    ATTR_ASSISTANT_ID_KEYWORD,
    ATTR_CLI_REQUEST,
    ATTR_MCP_NAME_KEYWORD,
    ATTR_MONEY_SPENT,
    ATTR_USER_ID_KEYWORD,
    CLI_ASSISTANT_ID,
    METRIC_NAME_KEYWORD,
    LeaderboardCollector,
    RawUserMetrics,
    _creator_email_sql,
    _creator_name_sql,
    _creator_user_id_sql,
    _validate_column,
)

MODULE = "codemie.service.leaderboard.collector"


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

    def test_build_raw_metrics_merges_pg_es_and_cost_payloads(self, collector):
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
                "cli": {"cli_total_spend": 10.5},
            }
        }
        cost_metrics = {"u1": {"total_spend": 11.5}}

        result = collector._build_raw_metrics(users, pg_metrics, es_metrics, cost_metrics)

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
                cli={"cli_sessions": 5, "cli_total_spend": 10.5},
                impact={"kata_completed": 6},
                litellm_spend={"total_spend": 11.5},
            )
        ]


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

    def test_build_cli_query_uses_money_and_cli_constants(self, collector):
        query = collector._build_cli_query({"range": "value"})

        cli_spend = query["aggs"]["by_user"]["aggs"]["cli_spend"]
        assert {"term": {ATTR_CLI_REQUEST: True}} in cli_spend["filter"]["bool"]["must"]
        assert cli_spend["aggs"]["total_spend"]["sum"]["field"] == ATTR_MONEY_SPENT


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
                            "cli_spend": {"total_spend": {"value": 12.5}},
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
            "cli_total_spend": 12.5,
        }


class TestCostMetrics:
    @pytest.mark.asyncio
    async def test_collect_cost_metrics_builds_result_from_es_buckets(self, collector, mock_es_repository):
        mock_es_repository.execute_aggregation_query.return_value = {
            "aggregations": {
                "by_user": {
                    "buckets": [
                        {
                            "key": "u1",
                            "total_spend": {"value": 12.5},
                            "cli_spend": {"amount": {"value": 7.5}},
                            "platform_spend": {"amount": {"value": 5.0}},
                        }
                    ]
                }
            }
        }

        result = await collector._collect_cost_metrics("2026-03-01", "2026-03-31")

        assert result == {
            "u1": {
                "total_spend": 12.5,
                "cli_spend": 7.5,
                "platform_spend": 5.0,
            }
        }
        body = mock_es_repository.execute_aggregation_query.await_args.args[0]
        assert body["aggs"]["by_user"]["aggs"]["total_spend"]["sum"]["field"] == ATTR_MONEY_SPENT
        assert body["aggs"]["by_user"]["aggs"]["cli_spend"]["filter"] == {"term": {ATTR_CLI_REQUEST: True}}

    @pytest.mark.asyncio
    @patch(f"{MODULE}.logger")
    async def test_collect_cost_metrics_returns_empty_dict_on_exception(
        self, mock_logger, collector, mock_es_repository
    ):
        mock_es_repository.execute_aggregation_query.side_effect = RuntimeError("ES down")

        result = await collector._collect_cost_metrics("2026-03-01", "2026-03-31")

        assert result == {}
        mock_logger.warning.assert_called_once()
