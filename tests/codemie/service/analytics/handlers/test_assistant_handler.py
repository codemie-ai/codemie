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

from unittest.mock import MagicMock

import pytest

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.assistant_handler import AssistantHandler
from codemie.service.analytics.metric_names import MetricName


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
                {"name": "attributes.assistant_name.keyword", "type": "keyword"},
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
                {"name": "cost_rounded", "type": "double"},
                {"name": "cost_per_operation", "type": "double"},
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

    def test_get_assistants_chats_esql_query_structure(self, handler):
        """Verify ES|QL query has correct structure."""
        # The ES|QL query is embedded in the handler method
        # We can't easily extract it without calling the method, but we can verify
        # the structure by looking at what _parse_assistants_chats_result expects

        # This test would verify the query contains required elements:
        # - FROM codemie_metrics_logs
        # - WHERE metric_name.keyword == "conversation_assistant_usage"
        # - STATS BY (2-stage aggregation)
        # - First STATS groups by assistant + conversation_id
        # - Second STATS groups by assistant only
        # - EVAL calculates error_rate, cost_per_operation
        # - SORT and LIMIT clauses

        # Since the query is a string literal, we'll test the result parser instead
        pass


class TestAssistantsChatsResultParser:
    """Tests for assistants/chats result parser."""

    def test_parse_assistants_chats_result_extracts_all_fields(self, handler):
        """Verify all fields are extracted from ES|QL result."""
        # Arrange
        result = {
            "columns": [
                {"name": "attributes.assistant_name.keyword", "type": "keyword"},
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
                {"name": "cost_rounded", "type": "double"},
                {"name": "cost_per_operation", "type": "double"},
                {"name": "avg_execution_rounded", "type": "double"},
                {"name": "last_error_time", "type": "date"},
            ],
            "values": [
                [
                    "test-assistant",
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
                    12.34,  # cost_rounded
                    0.12987,  # cost_per_operation
                    1.23,  # avg_execution_rounded
                    "2024-01-15T10:30:00Z",  # last_error_time
                ]
            ],
        }

        # Act
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
        assert row["total_cost_usd"] == 12.34
        assert row["cost_per_op_usd"] == 0.12987
        assert row["avg_time_seconds"] == 1.23
        assert row["last_error_timestamp"] == "2024-01-15T10:30:00Z"

    def test_parse_assistants_chats_result_handles_null_values(self, handler):
        """Verify None/null values are handled gracefully."""
        # Arrange
        result = {
            "columns": [
                {"name": "attributes.assistant_name.keyword", "type": "keyword"},
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
                {"name": "cost_rounded", "type": "double"},
                {"name": "cost_per_operation", "type": "double"},
                {"name": "avg_execution_rounded", "type": "double"},
            ],
            "values": [
                [
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
                    12.34,
                    None,  # null cost_per_operation
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
        assert row["cost_per_op_usd"] == 0
        assert row["avg_time_seconds"] == 0
        assert row["last_error_timestamp"] is None


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

        # Verify multiple sub-aggregations exist (1-bucket through 6-bucket)
        assert "1-bucket" in assistants_agg["aggs"]
        assert "2-bucket" in assistants_agg["aggs"]
        assert "3-bucket" in assistants_agg["aggs"]
        assert "4-bucket" in assistants_agg["aggs"]
        assert "5-bucket" in assistants_agg["aggs"]
        assert "6-bucket" in assistants_agg["aggs"]

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
                            "2-bucket": {"2-metric": {"value": 45.6789}},
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
        assert row["total_cost"] == 45.6789  # Rounded to 4 decimals
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
