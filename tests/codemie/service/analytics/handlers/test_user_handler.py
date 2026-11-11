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

"""Unit tests for UserHandler."""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.access_filter import AccessFilter
from codemie.service.analytics.handlers.user_handler import (
    MONEY_SPENT_FIELD,
    UserHandler,
)
from codemie.service.analytics.metric_names import MetricName
from codemie.service.spend.coverage import Portion, SpendDimension, SpendResult, TimeRange
from codemie.service.spend.ledger import AccessScope, PersonTotalHolder, ReportFilter, UserTerm
from codemie.service.spend.spend_group import SpendGroup

#: The boundary the `mock_user` fixture below is entitled to: a plain member of no project at all.
#: **Restricted with an empty project set, which matches nothing rather than everything** - the rule
#: `AccessScope` holds and `SecureQueryBuilder`'s `minimum_should_match=0` already applied to the very
#: candidate buckets this table's figures foot.
_EXPECTED_SCOPE = AccessScope(unrestricted=False, user_id="test-user-id")


@pytest.fixture(autouse=True)
def _fresh_access_context():
    """Every test derives its own access context from its own `mock_user`.

    `AccessFilter` caches contexts on the class, keyed by user id, and every test here shares one id:
    without this, a test that made its user an admin leaks an unrestricted scope into whichever test
    the same worker runs next, and a boundary assertion fails or passes by test order.
    """
    AccessFilter._context_cache.clear()
    yield
    AccessFilter._context_cache.clear()


@pytest.fixture
def mock_user():
    """Create mock user."""
    user = MagicMock(spec=User)
    user.project_names = []
    user.admin_project_names = []
    user.is_global_user = False
    user.is_admin_or_maintainer = False
    user.is_admin = False
    user.is_maintainer = False
    user.id = "test-user-id"
    return user


@pytest.fixture
def mock_repository():
    """Create mock repository."""
    return MagicMock(spec=MetricsElasticRepository)


@pytest.fixture
def handler(mock_user, mock_repository):
    """Create handler with mocked dependencies."""
    return UserHandler(mock_user, mock_repository)


class TestGetUsersSpending:
    """Tests for get_users_spending method."""

    def test_get_users_spending_aggregates_by_user_email(self, handler):
        """Users are grouped by user_id, placeholders filtered out, and no money is aggregated."""
        # Arrange
        query = {"bool": {"filter": []}}

        # Act
        agg_body = handler._build_users_spending_aggregation(query, fetch_size=20)

        # Assert — query is wrapped with placeholder filter
        assert agg_body["query"]["bool"]["must"] == [query]
        assert agg_body["query"]["bool"]["must_not"] == [
            {"terms": {"attributes.user_id.keyword": ["unknown", "00000000-0000-0000-0000-000000000000"]}}
        ]
        assert agg_body["size"] == 0

        # Verify paginated_results structure
        users_agg = agg_body["aggs"]["paginated_results"]
        assert users_agg["terms"]["field"] == "attributes.user_id.keyword"
        assert users_agg["terms"]["size"] == 20
        # Candidates are selected by activity: the money field they used to be ordered by is gone
        assert users_agg["terms"]["order"] == {"_count": "desc"}
        # No sub-aggregation survives — every one of them was a money sum
        assert "aggs" not in users_agg
        assert MONEY_SPENT_FIELD not in json.dumps(agg_body)

    def test_parse_users_spending_result_leaves_money_absent(self, handler):
        """The money cell is left absent for the spend port; Elasticsearch supplies identities only."""
        # Arrange
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": "user1@example.com",
                            "total_cost": {"value": 123.456789},
                        },
                        {
                            "key": "user2@example.com",
                            "total_cost": {"value": 45.999},
                        },
                    ]
                },
                "total_buckets": {"value": 2},
            }
        }

        # Act
        rows = handler._parse_users_spending_result(result)

        # Assert
        assert len(rows) == 2
        assert rows[0]["user_email"] == "user1@example.com"
        assert rows[0]["total_cost_usd"] is None
        assert rows[1]["user_email"] == "user2@example.com"
        assert rows[1]["total_cost_usd"] is None

    def test_parse_users_spending_result_never_substitutes_zero_for_money(self, handler):
        """An unfilled money cell stays None: a zero here would claim the user spent nothing."""
        # Arrange
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": "user1@example.com",
                            "total_cost": {"value": None},
                        }
                    ]
                },
                "total_buckets": {"value": 1},
            }
        }

        # Act
        rows = handler._parse_users_spending_result(result)

        # Assert
        assert len(rows) == 1
        assert rows[0]["total_cost_usd"] is None

    def test_parse_users_spending_result_skips_empty_keys(self, handler):
        """Verify empty user keys are skipped (not mapped to 'unknown')."""
        # Arrange
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": "user1@example.com",
                            "total_cost": {"value": 10.50},
                        },
                        {
                            "key": "",
                            "total_cost": {"value": 0.01},
                        },
                        {
                            "key": "user2@example.com",
                            "total_cost": {"value": 5.25},
                        },
                    ]
                },
                "total_buckets": {"value": 3},
            }
        }

        # Act
        rows = handler._parse_users_spending_result(result)

        # Assert — empty key bucket is skipped entirely
        assert len(rows) == 2
        assert rows[0]["user_email"] == "user1@example.com"
        assert rows[1]["user_email"] == "user2@example.com"

    def test_parse_users_spending_rows_raw_returns_identity_and_no_money(self, handler):
        """Parser returns the identity and an absent money cell — no bucket money is read."""
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": "user1@example.com",
                            "total_cost": {"value": 500.0},
                            "cli_cost": {"sum": {"value": 100.0}},
                        },
                        {
                            "key": "user2@example.com",
                            "total_cost": {"value": 200.0},
                            "cli_cost": {"sum": {"value": 0.0}},
                        },
                    ]
                }
            }
        }

        rows = handler._parse_users_spending_rows_raw(result)

        assert len(rows) == 2
        assert rows[0] == {"user_email": "user1@example.com", "total_cost_usd": None}
        assert rows[1] == {"user_email": "user2@example.com", "total_cost_usd": None}

    def test_parse_users_spending_rows_raw_skips_empty_keys(self, handler):
        """Empty user keys are skipped (same as existing parser)."""
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": "user1@example.com",
                            "total_cost": {"value": 10.0},
                            "cli_cost": {"sum": {"value": 0.0}},
                        },
                        {"key": "", "total_cost": {"value": 5.0}, "cli_cost": {"sum": {"value": 0.0}}},
                    ]
                }
            }
        }
        rows = handler._parse_users_spending_rows_raw(result)
        assert len(rows) == 1
        assert rows[0]["user_email"] == "user1@example.com"

    @pytest.mark.asyncio
    async def test_get_users_spending_returns_raw_total_without_cli_adjustment(self, handler):
        """get_users_spending returns the pipeline total untouched — no CLI adjustment term."""
        raw_rows = [
            {"user_email": "user1", "total_cost_usd": 500.0},
        ]
        mock_pipeline_result = {
            "data": {"rows": raw_rows, "columns": []},
            "totals": {},
            "pagination": {"page": 0, "per_page": 20, "total_count": 1, "has_more": False},
        }

        with patch.object(
            handler._pipeline, "execute_enriched_tabular_query", new=AsyncMock(return_value=mock_pipeline_result)
        ):
            result = await handler.get_users_spending(time_period="last_30_days")

        rows = result["data"]["rows"]
        assert rows == [{"user_email": "user1", "total_cost_usd": 500.0}]


class TestGetUsersActivity:
    """Tests for get_users_activity method."""

    def test_get_users_activity_includes_unique_projects(self, handler):
        """Verify unique projects count is extracted via cardinality."""
        # Arrange
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": "user1@example.com",
                            "2": {"value": 3},  # cardinality of unique projects
                            "1-bucket": {"1-metric": {"value": 100.0}},
                            "assistant_cost_bucket": {"assistant_cost": {"value": 30.0}},
                            "workflow_cost_bucket": {"workflow_cost": {"value": 40.0}},
                            "datasource_cost_bucket": {"datasource_cost": {"value": 30.0}},
                            "assistant_tokens_bucket": {"input": {"value": 1000}, "output": {"value": 500}},
                            "workflow_tokens_bucket": {"input": {"value": 2000}, "output": {"value": 1000}},
                            "datasource_tokens_bucket": {"input": {"value": 500}, "output": {"value": 250}},
                            "3-bucket": {"3-metric": {"value": 3500}},
                            "4-bucket": {"4-metric": {"value": 1750}},
                            "5-bucket": {"5-metric": {"value": 100}},
                            "6-bucket": {"6-metric": {"value": 50}},
                        }
                    ]
                },
                "total_buckets": {"value": 1},
            }
        }

        # Act
        rows = handler._parse_users_activity_result(result)

        # Assert
        assert len(rows) == 1
        row = rows[0]
        assert row["unique_projects"] == 3

    def test_get_users_activity_defaults_to_zero_when_no_projects(self, handler):
        """Verify unique_projects defaults to 0 when no project data."""
        # Arrange
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": "user1@example.com",
                            "2": {"value": 0},  # no unique projects
                            "1-bucket": {"1-metric": {"value": 100.0}},
                            "assistant_cost_bucket": {"assistant_cost": {"value": 100.0}},
                            "workflow_cost_bucket": {"workflow_cost": {"value": 0}},
                            "datasource_cost_bucket": {"datasource_cost": {"value": 0}},
                            "assistant_tokens_bucket": {"input": {"value": 5000}, "output": {"value": 3000}},
                            "workflow_tokens_bucket": {"input": {"value": 0}, "output": {"value": 0}},
                            "datasource_tokens_bucket": {"input": {"value": 0}, "output": {"value": 0}},
                            "3-bucket": {"3-metric": {"value": 5000}},
                            "4-bucket": {"4-metric": {"value": 3000}},
                            "5-bucket": {"5-metric": {"value": 0}},
                            "6-bucket": {"6-metric": {"value": 0}},
                        }
                    ]
                },
                "total_buckets": {"value": 1},
            }
        }

        # Act
        rows = handler._parse_users_activity_result(result)

        # Assert
        assert len(rows) == 1
        assert rows[0]["unique_projects"] == 0

    def test_get_users_activity_calculates_llm_tokens(self, handler):
        """Verify LLM tokens (input + output from web + CLI) are calculated correctly."""
        # Arrange
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": "user1@example.com",
                            "2": {"value": 1},
                            "1-bucket": {"1-metric": {"value": 100.0}},
                            "assistant_cost_bucket": {"assistant_cost": {"value": 100.0}},
                            "workflow_cost_bucket": {"workflow_cost": {"value": 0}},
                            "datasource_cost_bucket": {"datasource_cost": {"value": 0}},
                            "assistant_tokens_bucket": {"input": {"value": 80}, "output": {"value": 40}},
                            "workflow_tokens_bucket": {"input": {"value": 0}, "output": {"value": 0}},
                            "datasource_tokens_bucket": {"input": {"value": 0}, "output": {"value": 0}},
                            "3-bucket": {"3-metric": {"value": 100}},  # web input_tokens
                            "4-bucket": {"4-metric": {"value": 50}},  # web output_tokens
                            "5-bucket": {"5-metric": {"value": 20}},  # CLI input_tokens
                            "6-bucket": {"6-metric": {"value": 10}},  # CLI output_tokens
                        }
                    ]
                },
                "total_buckets": {"value": 1},
            }
        }

        # Act
        rows = handler._parse_users_activity_result(result)

        # Assert
        assert len(rows) == 1
        assert rows[0]["llm_tokens"] == 180  # 100 + 50 + 20 + 10

    def test_get_users_activity_aggregation_structure(self, handler):
        """Verify aggregation structure for users activity."""
        # Arrange
        query = {"bool": {"filter": []}}

        # Act
        agg_body = handler._build_users_activity_aggregation(query, fetch_size=20)

        # Assert — query is wrapped with placeholder filter
        assert agg_body["query"]["bool"]["must"] == [query]
        assert agg_body["query"]["bool"]["must_not"] == [
            {"terms": {"attributes.user_id.keyword": ["unknown", "00000000-0000-0000-0000-000000000000"]}}
        ]
        assert agg_body["size"] == 0

        # Verify paginated_results structure
        users_agg = agg_body["aggs"]["paginated_results"]
        assert users_agg["terms"]["field"] == "attributes.user_id.keyword"
        assert users_agg["terms"]["size"] == 20
        assert users_agg["terms"]["order"] == {"1-bucket>1-metric": "desc"}

        # Verify sub-aggregations exist
        assert "2" in users_agg["aggs"]  # unique_projects (cardinality)
        assert "1-bucket" in users_agg["aggs"]  # total money_spent
        assert "assistant_cost_bucket" in users_agg["aggs"]  # assistant costs
        assert "workflow_cost_bucket" in users_agg["aggs"]  # workflow costs
        assert "datasource_cost_bucket" in users_agg["aggs"]  # datasource costs
        assert "assistant_tokens_bucket" in users_agg["aggs"]  # assistant tokens
        assert "workflow_tokens_bucket" in users_agg["aggs"]  # workflow tokens
        assert "datasource_tokens_bucket" in users_agg["aggs"]  # datasource tokens
        assert "3-bucket" in users_agg["aggs"]  # web input_tokens
        assert "4-bucket" in users_agg["aggs"]  # web output_tokens
        assert "5-bucket" in users_agg["aggs"]  # CLI input_tokens
        assert "6-bucket" in users_agg["aggs"]  # CLI output_tokens
        assert "filter_zero_tokens" in users_agg["aggs"]  # bucket_selector filter

        # Verify "2" is cardinality aggregation for unique projects
        assert "cardinality" in users_agg["aggs"]["2"]
        assert users_agg["aggs"]["2"]["cardinality"]["field"] == "attributes.project.keyword"

        # Verify 1-bucket filters by all metric types using "should" (not "filter")
        should_filters = users_agg["aggs"]["1-bucket"]["filter"]["bool"]["should"]
        # Extract metric names from should filters
        metric_names = set()
        for filter_item in should_filters:
            if "term" in filter_item:
                metric_names.add(filter_item["term"]["metric_name.keyword"])
            elif "bool" in filter_item and "filter" in filter_item["bool"]:
                # Handle nested bool filter for CLI (with cli_request=true)
                for nested_filter in filter_item["bool"]["filter"]:
                    if "term" in nested_filter and "metric_name.keyword" in nested_filter["term"]:
                        metric_names.add(nested_filter["term"]["metric_name.keyword"])

        assert metric_names == {
            MetricName.CONVERSATION_ASSISTANT_USAGE.value,
            MetricName.CLI_LLM_USAGE_TOTAL.value,  # Changed from CLI_COMMAND_EXECUTION_TOTAL
            MetricName.WORKFLOW_EXECUTION_TOTAL.value,
            MetricName.DATASOURCE_TOKENS_USAGE.value,
        }

        # Verify 3-bucket and 4-bucket filter by LLM metrics only (excludes embeddings)
        assert set(users_agg["aggs"]["3-bucket"]["filter"]["bool"]["filter"][0]["terms"]["metric_name.keyword"]) == {
            MetricName.CONVERSATION_ASSISTANT_USAGE.value,
            MetricName.WORKFLOW_EXECUTION_TOTAL.value,
        }
        assert set(users_agg["aggs"]["4-bucket"]["filter"]["bool"]["filter"][0]["terms"]["metric_name.keyword"]) == {
            MetricName.CONVERSATION_ASSISTANT_USAGE.value,
            MetricName.WORKFLOW_EXECUTION_TOTAL.value,
        }

        # Verify bucket_selector filter exists
        assert "bucket_selector" in users_agg["aggs"]["filter_zero_tokens"]
        bucket_selector = users_agg["aggs"]["filter_zero_tokens"]["bucket_selector"]
        assert "buckets_path" in bucket_selector
        assert "script" in bucket_selector
        assert (
            bucket_selector["script"] == "(params.webInput + params.webOutput + params.cliInput + params.cliOutput) > 0"
        )


def _make_es_page(user_ids: list[str], after_key: dict | None = None) -> dict:
    """Build a fake ES composite aggregation response page."""
    buckets = [{"key": {"user_id": uid, "user_name": f"User {uid}"}, "doc_count": 1} for uid in user_ids]
    agg: dict = {"buckets": buckets}
    if after_key is not None:
        agg["after_key"] = after_key
    return {"aggregations": {"unique_users": agg}}


class TestGetUsersList:
    """Tests for get_users_list / _fetch_all_users_with_after_key."""

    @pytest.fixture(autouse=True)
    def _enable_user_management(self):
        from codemie.service.analytics.access_filter import AccessFilter

        AccessFilter._context_cache.clear()
        with patch("codemie.service.analytics.handlers.user_handler.config") as mock_cfg:
            mock_cfg.ENABLE_USER_MANAGEMENT = True
            yield mock_cfg

    @pytest.mark.asyncio
    async def test_single_page_returns_all_users(self, handler, mock_repository):
        """When ES returns fewer than page_size buckets with no after_key, stops after 1 call."""
        mock_repository.execute_aggregation_query = AsyncMock(return_value=_make_es_page(["u1", "u2", "u3"]))

        query = {"bool": {"filter": []}}
        buckets = await handler._fetch_all_users_with_after_key(query)

        assert len(buckets) == 3
        assert buckets[0]["key"]["user_id"] == "u1"
        mock_repository.execute_aggregation_query.assert_called_once()

    @pytest.mark.asyncio
    async def test_multi_page_collects_all_users(self, handler, mock_repository):
        """When first page is full (10000 buckets) ES returns after_key; loop continues until partial page."""
        page1_ids = [f"u{i}" for i in range(10000)]
        page2_ids = ["u10000", "u10001", "u10002"]

        mock_repository.execute_aggregation_query = AsyncMock(
            side_effect=[
                _make_es_page(page1_ids, after_key={"user_id": "u9999", "user_name": "User u9999"}),
                _make_es_page(page2_ids),
            ]
        )

        query = {"bool": {"filter": []}}
        buckets = await handler._fetch_all_users_with_after_key(query)

        assert len(buckets) == 10003
        assert mock_repository.execute_aggregation_query.call_count == 2

    @pytest.mark.asyncio
    async def test_second_call_includes_after_key(self, handler, mock_repository):
        """Verifies the after_key from page 1 is passed as 'after' in the page 2 request."""
        page1_ids = [f"u{i}" for i in range(10000)]
        after = {"user_id": "u9999", "user_name": "User u9999"}

        mock_repository.execute_aggregation_query = AsyncMock(
            side_effect=[
                _make_es_page(page1_ids, after_key=after),
                _make_es_page(["u10000"]),
            ]
        )

        query = {"bool": {"filter": []}}
        await handler._fetch_all_users_with_after_key(query)

        _, second_call_kwargs = mock_repository.execute_aggregation_query.call_args_list[1]
        second_body = mock_repository.execute_aggregation_query.call_args_list[1][0][0]
        assert second_body["aggs"]["unique_users"]["composite"]["after"] == after

    @pytest.mark.asyncio
    async def test_three_pages_stops_at_partial_last_page(self, handler, mock_repository):
        """Loops correctly across three pages."""
        full_page = [f"u{i}" for i in range(10000)]
        after1 = {"user_id": "u9999", "user_name": "User u9999"}
        after2 = {"user_id": "u19999", "user_name": "User u19999"}

        mock_repository.execute_aggregation_query = AsyncMock(
            side_effect=[
                _make_es_page(full_page, after_key=after1),
                _make_es_page(full_page, after_key=after2),
                _make_es_page(["u20000", "u20001"]),
            ]
        )

        query = {"bool": {"filter": []}}
        buckets = await handler._fetch_all_users_with_after_key(query)

        assert len(buckets) == 20002
        assert mock_repository.execute_aggregation_query.call_count == 3

    @pytest.mark.asyncio
    async def test_partial_page_with_after_key_continues_pagination(self, handler, mock_repository):
        """Partial page (<10000 buckets) with after_key must NOT stop — ES multi-shard scenario."""
        after = {"user_id": "u6999", "user_name": "User u6999"}

        mock_repository.execute_aggregation_query = AsyncMock(
            side_effect=[
                _make_es_page([f"u{i}" for i in range(7000)], after_key=after),
                _make_es_page([f"u{i}" for i in range(7000, 10000)]),
            ]
        )

        query = {"bool": {"filter": []}}
        buckets = await handler._fetch_all_users_with_after_key(query)

        assert len(buckets) == 10000
        assert mock_repository.execute_aggregation_query.call_count == 2

    @pytest.mark.asyncio
    async def test_empty_result_returns_empty_list(self, handler, mock_repository):
        """ES returning zero buckets produces an empty list with one call."""
        mock_repository.execute_aggregation_query = AsyncMock(return_value=_make_es_page([]))

        query = {"bool": {"filter": []}}
        buckets = await handler._fetch_all_users_with_after_key(query)

        assert buckets == []
        mock_repository.execute_aggregation_query.assert_called_once()

    # ── Super-user (PG) path ──────────────────────────────────────────────────

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.user_handler.get_async_session")
    @patch("codemie.service.analytics.handlers.user_handler.user_repository")
    async def test_get_users_list_superadmin_uses_pg_not_es(
        self, mock_pg_repo, mock_session_ctx, handler, mock_repository, mock_user
    ):
        """Super-admin path queries PostgreSQL and does NOT call Elasticsearch."""
        mock_user.is_admin = True
        mock_user.is_admin_or_maintainer = True

        pg_user = MagicMock()
        pg_user.id = "uuid-1"
        pg_user.name = "Alice"
        pg_user.username = "alice"
        pg_user.email = None

        mock_pg_repo.aquery_active_users = AsyncMock(return_value=[pg_user])
        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        response = await handler.get_users_list(time_period="last_30_days")

        data = response["data"]
        assert data["users"] == [{"id": "uuid-1", "name": "Alice", "email": None}]
        assert data["total_count"] == 1
        assert "metadata" in response
        mock_repository.execute_aggregation_query.assert_not_called()

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.user_handler.get_async_session")
    @patch("codemie.service.analytics.handlers.user_handler.user_repository")
    async def test_get_users_list_superadmin_username_fallback(
        self, mock_pg_repo, mock_session_ctx, handler, mock_user
    ):
        """When UserDB.name is None, username is used as the display name."""
        mock_user.is_admin = True
        mock_user.is_admin_or_maintainer = True

        pg_user = MagicMock()
        pg_user.id = "uuid-2"
        pg_user.name = None
        pg_user.username = "bob"
        pg_user.email = None

        mock_pg_repo.aquery_active_users = AsyncMock(return_value=[pg_user])
        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        response = await handler.get_users_list(time_period="last_30_days")

        assert response["data"]["users"] == [{"id": "uuid-2", "name": "bob", "email": None}]

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.user_handler.get_async_session")
    @patch("codemie.service.analytics.handlers.user_handler.user_repository")
    async def test_get_users_list_superadmin_passes_search_to_pg(
        self, mock_pg_repo, mock_session_ctx, handler, mock_user
    ):
        """search parameter is forwarded to aquery_active_users."""
        mock_user.is_admin = True
        mock_user.is_admin_or_maintainer = True

        mock_pg_repo.aquery_active_users = AsyncMock(return_value=[])
        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        await handler.get_users_list(time_period="last_30_days", search="alice")

        mock_pg_repo.aquery_active_users.assert_called_once()
        call_kwargs = mock_pg_repo.aquery_active_users.call_args
        assert call_kwargs.kwargs.get("search") == "alice" or (
            len(call_kwargs.args) > 1 and call_kwargs.args[1] == "alice"
        )

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.user_handler.get_async_session")
    @patch("codemie.service.analytics.handlers.user_handler.user_repository")
    async def test_get_users_list_superadmin_passes_projects_to_pg(
        self, mock_pg_repo, mock_session_ctx, handler, mock_user
    ):
        """projects parameter is forwarded to aquery_active_users."""
        mock_user.is_admin = True
        mock_user.is_admin_or_maintainer = True

        mock_pg_repo.aquery_active_users = AsyncMock(return_value=[])
        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        await handler.get_users_list(time_period="last_30_days", projects=["project-a", "project-b"])

        mock_pg_repo.aquery_active_users.assert_called_once()
        call_kwargs = mock_pg_repo.aquery_active_users.call_args
        assert call_kwargs.kwargs.get("projects") == ["project-a", "project-b"]

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.user_handler.get_async_session")
    @patch("codemie.service.analytics.handlers.user_handler.user_repository")
    async def test_get_users_list_superadmin_empty_result(self, mock_pg_repo, mock_session_ctx, handler, mock_user):
        """Empty PG result produces users=[] and total_count=0."""
        mock_user.is_admin = True
        mock_user.is_admin_or_maintainer = True

        mock_pg_repo.aquery_active_users = AsyncMock(return_value=[])
        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        response = await handler.get_users_list(time_period="last_30_days")

        assert response["data"]["users"] == []
        assert response["data"]["total_count"] == 0

    # ── Super-admin with ENABLE_USER_MANAGEMENT=False falls back to ES ───────

    @pytest.mark.asyncio
    @patch(
        "codemie.service.analytics.handlers.user_identity_resolver.UserIdentityResolver.resolve_and_merge",
        new_callable=AsyncMock,
    )
    @patch("codemie.service.analytics.handlers.user_handler.get_async_session")
    @patch("codemie.service.analytics.handlers.user_handler.user_repository")
    async def test_get_users_list_superadmin_falls_back_to_es_when_flag_disabled(
        self, mock_pg_repo, mock_session_ctx, mock_resolve_and_merge, handler, mock_repository, mock_user
    ):
        """When ENABLE_USER_MANAGEMENT=False, admin uses ES path and PG is not called."""
        mock_user.is_admin = True
        mock_resolve_and_merge.side_effect = lambda users: users
        mock_repository.execute_aggregation_query = AsyncMock(return_value=_make_es_page([]))

        with patch("codemie.service.analytics.handlers.user_handler.config") as mock_cfg:
            mock_cfg.ENABLE_USER_MANAGEMENT = False
            response = await handler.get_users_list(time_period="last_30_days")

        mock_pg_repo.aquery_active_users.assert_not_called()
        assert response["data"]["users"] == []

    # ── Email inclusion (PG super-admin path) ─────────────────────────────────

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.user_handler.get_async_session")
    @patch("codemie.service.analytics.handlers.user_handler.user_repository")
    async def test_get_users_list_superadmin_includes_email(self, mock_pg_repo, mock_session_ctx, handler, mock_user):
        """UserListItem must include email when returned from the PG super-admin path."""
        mock_user.is_admin = True
        mock_user.is_admin_or_maintainer = True

        pg_user = MagicMock()
        pg_user.id = "uuid-1"
        pg_user.name = "Alice"
        pg_user.username = "alice"
        pg_user.email = "alice@example.com"

        mock_pg_repo.aquery_active_users = AsyncMock(return_value=[pg_user])
        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        response = await handler.get_users_list(time_period="last_30_days")

        users = response["data"]["users"]
        assert len(users) == 1
        assert users[0]["email"] == "alice@example.com"

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.user_handler.get_async_session")
    @patch("codemie.service.analytics.handlers.user_handler.user_repository")
    async def test_get_users_list_superadmin_email_none_when_db_email_is_none(
        self, mock_pg_repo, mock_session_ctx, handler, mock_user
    ):
        """email key must still be present but None when UserDB.email is None."""
        mock_user.is_admin = True
        mock_user.is_admin_or_maintainer = True

        pg_user = MagicMock()
        pg_user.id = "uuid-2"
        pg_user.name = "Bob"
        pg_user.username = "bob"
        pg_user.email = None

        mock_pg_repo.aquery_active_users = AsyncMock(return_value=[pg_user])
        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        response = await handler.get_users_list(time_period="last_30_days")

        users = response["data"]["users"]
        assert users[0]["email"] is None

    # ── Non-admin (ES) path — unchanged ──────────────────────────────────────

    @pytest.mark.asyncio
    @patch(
        "codemie.service.analytics.handlers.user_identity_resolver.UserIdentityResolver.resolve_and_merge",
        new_callable=AsyncMock,
    )
    async def test_get_users_list_non_admin_returns_correct_response_shape(
        self, mock_resolve_and_merge, handler, mock_repository, mock_user
    ):
        """Non-admin get_users_list uses single-page query and returns correct response shape."""
        mock_resolve_and_merge.side_effect = lambda users: users
        mock_user.is_admin = False
        mock_user.is_admin_or_maintainer = False
        mock_repository.execute_aggregation_query = AsyncMock(return_value=_make_es_page(["alice", "bob"]))

        response = await handler.get_users_list(time_period="last_24_hours")

        data = response["data"]
        assert data["total_count"] == 2
        assert {"id": "alice", "name": "User alice"} in data["users"]
        assert {"id": "bob", "name": "User bob"} in data["users"]
        assert "metadata" in response
        mock_repository.execute_aggregation_query.assert_called_once()

    @pytest.mark.asyncio
    @patch(
        "codemie.service.analytics.handlers.user_identity_resolver.UserIdentityResolver.resolve_and_merge",
        new_callable=AsyncMock,
    )
    async def test_get_users_list_non_admin_uses_single_query(
        self, mock_resolve_and_merge, handler, mock_repository, mock_user
    ):
        """Non-admin get_users_list executes exactly one ES query (no pagination loop)."""
        mock_resolve_and_merge.side_effect = lambda users: users
        mock_user.is_admin = False
        mock_user.is_admin_or_maintainer = False
        mock_repository.execute_aggregation_query = AsyncMock(
            return_value=_make_es_page([f"u{i}" for i in range(10000)])
        )

        await handler.get_users_list(time_period="last_24_hours")

        mock_repository.execute_aggregation_query.assert_called_once()


class TestGetUsersUniqueDaily:
    """Tests for get_users_unique_daily method."""

    def test_build_users_unique_daily_aggregation_structure(self, handler):
        """Verify date_histogram aggregation structure with cardinality."""
        # Arrange
        query = {
            "bool": {
                "filter": [
                    {"range": {"@timestamp": {"gte": "2025-12-01T00:00:00.000Z", "lte": "2025-12-31T23:59:59.999Z"}}}
                ]
            }
        }

        # Act
        agg_body = handler._build_users_unique_daily_aggregation(query)

        # Assert
        assert agg_body["query"] == query
        assert agg_body["size"] == 0

        # Verify paginated_results structure uses date_histogram
        date_hist_agg = agg_body["aggs"]["paginated_results"]
        assert "date_histogram" in date_hist_agg
        assert date_hist_agg["date_histogram"]["field"] == "time"
        assert date_hist_agg["date_histogram"]["calendar_interval"] == "1d"
        assert date_hist_agg["date_histogram"]["time_zone"] == "UTC"
        assert date_hist_agg["date_histogram"]["order"] == {"_key": "asc"}

        # Verify extended_bounds is present
        assert "extended_bounds" in date_hist_agg["date_histogram"]
        extended_bounds = date_hist_agg["date_histogram"]["extended_bounds"]
        assert "min" in extended_bounds
        assert "max" in extended_bounds

        # Verify nested filtered_users aggregation with cardinality on user_id
        assert "aggs" in date_hist_agg
        assert "filtered_users" in date_hist_agg["aggs"]
        filtered_users_agg = date_hist_agg["aggs"]["filtered_users"]
        # Verify filter excludes null/empty user_ids
        assert "filter" in filtered_users_agg
        assert "bool" in filtered_users_agg["filter"]
        # Verify nested cardinality aggregation
        assert "aggs" in filtered_users_agg
        assert "unique_users" in filtered_users_agg["aggs"]
        assert "cardinality" in filtered_users_agg["aggs"]["unique_users"]
        assert filtered_users_agg["aggs"]["unique_users"]["cardinality"]["field"] == "attributes.user_id.keyword"

    def test_parse_users_unique_daily_result_formats_dates(self, handler):
        """Verify date formatting and cardinality extraction."""
        # Arrange - Mock ES result with date buckets (nested filtered_users structure)
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": 1766448000000,  # epoch millis
                            "key_as_string": "2025-12-22T00:00:00.000Z",
                            "filtered_users": {"unique_users": {"value": 42}},
                        },
                        {
                            "key": 1766534400000,
                            "key_as_string": "2025-12-23T00:00:00.000Z",
                            "filtered_users": {"unique_users": {"value": 58}},
                        },
                        {
                            "key": 1766620800000,
                            "key_as_string": "2025-12-24T00:00:00.000Z",
                            "filtered_users": {"unique_users": {"value": 35}},
                        },
                    ]
                }
            }
        }

        # Act
        rows = handler._parse_users_unique_daily_result(result)

        # Assert
        assert len(rows) == 3
        # Verify first row
        assert rows[0]["date"] == "2025-12-22"
        assert rows[0]["unique_users"] == 42
        # Verify second row
        assert rows[1]["date"] == "2025-12-23"
        assert rows[1]["unique_users"] == 58
        # Verify third row
        assert rows[2]["date"] == "2025-12-24"
        assert rows[2]["unique_users"] == 35

    def test_parse_users_unique_daily_result_handles_empty_buckets(self, handler):
        """Verify empty buckets handling."""
        # Arrange
        result = {"aggregations": {"paginated_results": {"buckets": []}}}

        # Act
        rows = handler._parse_users_unique_daily_result(result)

        # Assert
        assert rows == []

    def test_get_users_unique_daily_columns(self, handler):
        """Verify column definitions."""
        # Act
        columns = handler._get_users_unique_daily_columns()

        # Assert
        assert len(columns) == 2
        # Verify date column
        assert columns[0]["id"] == "date"
        assert columns[0]["label"] == "Date"
        assert columns[0]["type"] == "date"
        # Verify unique_users column
        assert columns[1]["id"] == "unique_users"
        assert columns[1]["label"] == "Unique Users"
        assert columns[1]["type"] == "number"


class TestGetUsersPlatformSpendingMetrics:
    """Verify get_users_platform_spending uses PLATFORM_METRICS (9 items, no CLI)."""

    @pytest.mark.asyncio
    async def test_platform_spending_passes_platform_metrics_to_pipeline(self, handler):
        mock_result = {
            "data": {"rows": [], "columns": []},
            "totals": {},
            "pagination": {"page": 0, "per_page": 20, "total_count": 0, "has_more": False},
        }
        with (
            patch.object(
                handler._pipeline, "execute_enriched_tabular_query", new=AsyncMock(return_value=mock_result)
            ) as mock_exec,
            patch(
                "codemie.service.analytics.handlers.user_handler.UserIdentityResolver.resolve_rows",
                new=AsyncMock(),
            ),
        ):
            await handler.get_users_platform_spending()

        _, kwargs = mock_exec.call_args
        metric_filters = kwargs["filters"].metric_filters
        expected = set(MetricName.to_list_from_group(MetricName.PLATFORM_METRICS))
        assert set(metric_filters) == expected

    @pytest.mark.asyncio
    async def test_platform_spending_excludes_cli_metric(self, handler):
        mock_result = {
            "data": {"rows": [], "columns": []},
            "totals": {},
            "pagination": {"page": 0, "per_page": 20, "total_count": 0, "has_more": False},
        }
        with (
            patch.object(
                handler._pipeline, "execute_enriched_tabular_query", new=AsyncMock(return_value=mock_result)
            ) as mock_exec,
            patch(
                "codemie.service.analytics.handlers.user_handler.UserIdentityResolver.resolve_rows",
                new=AsyncMock(),
            ),
        ):
            await handler.get_users_platform_spending()

        _, kwargs = mock_exec.call_args
        metric_filters = kwargs["filters"].metric_filters or []
        assert MetricName.CLI_LLM_USAGE_TOTAL.value not in metric_filters


PERIOD_START = datetime(2026, 8, 1, tzinfo=timezone.utc)
PERIOD_END = datetime(2026, 8, 31, tzinfo=timezone.utc)
PERIOD = TimeRange(PERIOD_START, PERIOD_END)


def _user_record(user_id: str, username: str, email: str):
    return SimpleNamespace(id=user_id, username=username, email=email, name=username)


@contextlib.asynccontextmanager
async def _fake_session():
    yield MagicMock()


def _ledger_total(total: SpendResult) -> AsyncMock:
    """An aggregate_by_spend_group stub whose TOTAL is `total`, held to the caller's own boundary.

    The filter is asserted empty because these tests filter on nothing: a handler that dropped the
    dashboard's filter, or invented one, fails here rather than footing a filtered table unfiltered.
    """

    async def _call(period, *, access, report_filter):
        assert period == PERIOD
        assert access == _EXPECTED_SCOPE, "the ledger total must be narrowed by the caller's own boundary"
        assert report_filter == ReportFilter()
        return {group: SpendResult.no_figure(PERIOD) for group in SpendGroup} | {SpendGroup.TOTAL: total}

    return AsyncMock(side_effect=_call)


def _patched_users_port(records: dict, consumed: AsyncMock, ledger_total: AsyncMock | None = None):
    """Patch the identity read that names the holders, and the spend port itself.

    The scope's ledger total defaults to no figure, which leaves every footer exactly as its rows made it.
    """
    stack = contextlib.ExitStack()
    stack.enter_context(
        patch("codemie.service.analytics.handlers.user_handler.UserIdentityResolver.resolve_rows", new=AsyncMock())
    )
    stack.enter_context(patch("codemie.service.analytics.handlers.user_handler.get_async_session", _fake_session))
    stack.enter_context(
        patch(
            "codemie.service.analytics.handlers.user_handler.user_repository.afind_users_by_identifiers",
            new=AsyncMock(return_value=records),
        )
    )
    # Every project these tests name is shared: none is a personal project read through its owner.
    stack.enter_context(
        patch(
            "codemie.repository.application_repository.application_repository.aget_personal_project_owners",
            new=AsyncMock(return_value=set()),
        )
    )
    stack.enter_context(
        patch("codemie.service.analytics.handlers.user_handler.spend_service.consumed_for_holders", new=consumed)
    )
    stack.enter_context(
        patch(
            "codemie.service.analytics.handlers.user_handler.spend_service.aggregate_by_spend_group",
            new=ledger_total or _ledger_total(SpendResult.no_figure(PERIOD)),
        )
    )
    return stack


#: The filter of a dashboard with nothing selected - what every test not about filtering asks with.
NO_FILTER = ReportFilter()


def _answers(expected_filter: ReportFilter = NO_FILTER, /, **by_user_id: SpendResult):
    """A consumed_for_holders stub answering per user id.

    `access` and `report_filter` are taken as **required keywords**, so a handler that stopped threading
    either fails here: `PersonTotalHolder` is a person's total across everything, while the rows it foots
    are already narrowed to what the caller may see and to what the dashboard asked about.
    """

    async def _call(holders, period, *, access, report_filter):
        assert period == PERIOD
        assert access == _EXPECTED_SCOPE, "the figure must be narrowed by the caller's own boundary"
        assert report_filter == expected_filter, "the figure must be narrowed by the dashboard's own filter"
        return {holder: by_user_id[holder.user_id] for holder in holders if holder.user_id in by_user_id}

    return AsyncMock(side_effect=_call)


class TestUsersSpendingFromLedger:
    """The users table reads money from the spend port, one call for every candidate."""

    ADA = _user_record("u-ada", "ada", "ada@example.com")
    BOB = _user_record("u-bob", "bob", "bob@example.com")

    @pytest.mark.asyncio
    async def test_person_total_holder_carries_both_aliases(self, handler):
        rows = [{"user_email": "ada@example.com"}]

        with _patched_users_port({"ada@example.com": self.ADA}, AsyncMock()):
            holders = await handler._person_total_holders(rows)

        assert holders == {
            "ada@example.com": PersonTotalHolder(user_id="u-ada", aliases=frozenset({"ada", "ada@example.com"}))
        }

    @pytest.mark.asyncio
    async def test_exact_figure_renders_with_clean_annotations(self, handler):
        rows = [{"user_email": "ada@example.com", "total_cost_usd": None}]

        with _patched_users_port(
            {"ada@example.com": self.ADA}, _answers(**{"u-ada": SpendResult(figure=Decimal("42.5"))})
        ):
            enriched = await handler._enrich_users_spending(rows, PERIOD_START, PERIOD_END)

        assert enriched.rows[0]["total_cost_usd"] == 42.5
        assert enriched.rows[0]["spend_unavailable"] is False
        assert enriched.rows[0]["spend_partial"] is False
        assert enriched.rows[0]["spend_missing_amount"] is None
        assert enriched.totals["total_cost_usd"] == 42.5

    @pytest.mark.asyncio
    async def test_no_figure_renders_absent_and_unavailable(self, handler):
        rows = [{"user_email": "ada@example.com", "total_cost_usd": None}]

        with _patched_users_port({"ada@example.com": self.ADA}, _answers(**{"u-ada": SpendResult.no_figure(PERIOD)})):
            enriched = await handler._enrich_users_spending(rows, PERIOD_START, PERIOD_END)

        assert enriched.rows[0]["total_cost_usd"] is None
        assert enriched.rows[0]["spend_unavailable"] is True

    @pytest.mark.asyncio
    async def test_measured_zero_is_a_zero_and_leaves_the_table(self, handler):
        rows = [{"user_email": "ada@example.com", "total_cost_usd": None}]

        with _patched_users_port(
            {"ada@example.com": self.ADA}, _answers(**{"u-ada": SpendResult(figure=Decimal("0"))})
        ):
            enriched = await handler._enrich_users_spending(rows, PERIOD_START, PERIOD_END)

        # The cell is a measured zero, never an absence — and a zero-spend user is not listed,
        # exactly as the Elasticsearch zero-cost selector used to leave them out.
        assert rows[0]["total_cost_usd"] == 0.0
        assert enriched.rows == []

    @pytest.mark.asyncio
    async def test_truncated_figure_keeps_its_number_and_its_label(self, handler):
        rows = [{"user_email": "ada@example.com", "total_cost_usd": None}]
        truncated = SpendResult(figure=Decimal("11.75"), unavailable=PERIOD)

        with _patched_users_port({"ada@example.com": self.ADA}, _answers(**{"u-ada": truncated})):
            enriched = await handler._enrich_users_spending(rows, PERIOD_START, PERIOD_END)

        assert enriched.rows[0]["total_cost_usd"] == 11.75
        assert enriched.rows[0]["spend_unavailable"] is True

    @pytest.mark.asyncio
    async def test_rows_are_ordered_by_the_ledger_figure_not_by_doc_count(self, handler):
        rows = [
            {"user_email": "ada@example.com", "total_cost_usd": None},
            {"user_email": "bob@example.com", "total_cost_usd": None},
        ]
        records = {"ada@example.com": self.ADA, "bob@example.com": self.BOB}
        consumed = _answers(**{"u-ada": SpendResult(figure=Decimal("1")), "u-bob": SpendResult(figure=Decimal("50"))})

        with _patched_users_port(records, consumed):
            enriched = await handler._enrich_users_spending(rows, PERIOD_START, PERIOD_END)

        assert [row["user_email"] for row in enriched.rows] == ["bob@example.com", "ada@example.com"]
        assert enriched.totals["total_cost_usd"] == 51.0

    @pytest.mark.asyncio
    async def test_absent_figure_sorts_last_and_is_never_sorted_as_zero(self, handler):
        rows = [
            {"user_email": "ada@example.com", "total_cost_usd": None},
            {"user_email": "bob@example.com", "total_cost_usd": None},
        ]
        records = {"ada@example.com": self.ADA, "bob@example.com": self.BOB}
        consumed = _answers(**{"u-ada": SpendResult.no_figure(PERIOD), "u-bob": SpendResult(figure=Decimal("0.01"))})

        with _patched_users_port(records, consumed):
            enriched = await handler._enrich_users_spending(rows, PERIOD_START, PERIOD_END)

        assert [row["user_email"] for row in enriched.rows] == ["bob@example.com", "ada@example.com"]
        # No figure was invented to make the row sortable.
        assert enriched.rows[1]["total_cost_usd"] is None
        # ...and the footer says the total it shows is short.
        assert enriched.totals["total_cost_usd"] == 0.01
        assert enriched.totals["total_cost_usd_spend_partial"] is True

    @pytest.mark.asyncio
    async def test_unresolvable_identity_has_no_figure(self, handler):
        rows = [{"user_email": "ghost@example.com", "total_cost_usd": None}]
        consumed = AsyncMock()

        with _patched_users_port({}, consumed):
            enriched = await handler._enrich_users_spending(rows, PERIOD_START, PERIOD_END)

        consumed.assert_not_awaited()
        assert enriched.rows[0]["total_cost_usd"] is None
        assert enriched.rows[0]["spend_unavailable"] is True

    @pytest.mark.asyncio
    async def test_unknown_hole_keeps_the_missing_amount_unknown(self, handler):
        rows = [{"user_email": "ada@example.com", "total_cost_usd": None}]
        with_hole = SpendResult(
            figure=Decimal("8"),
            partial={SpendDimension.IDENTITY_BRIDGE: Portion(spend=None, rows=None)},
        )

        with _patched_users_port({"ada@example.com": self.ADA}, _answers(**{"u-ada": with_hole})):
            enriched = await handler._enrich_users_spending(rows, PERIOD_START, PERIOD_END)

        assert enriched.rows[0]["total_cost_usd"] == 8.0
        assert enriched.rows[0]["spend_partial"] is True
        assert enriched.rows[0]["spend_missing_amount"] is None

    @pytest.mark.asyncio
    async def test_all_figures_absent_omits_the_footer_money_key(self, handler):
        rows = [{"user_email": "ada@example.com", "total_cost_usd": None}]

        with _patched_users_port({"ada@example.com": self.ADA}, _answers(**{"u-ada": SpendResult.no_figure(PERIOD)})):
            enriched = await handler._enrich_users_spending(rows, PERIOD_START, PERIOD_END)

        assert "total_cost_usd" not in enriched.totals
        assert enriched.totals["total_cost_usd_spend_unavailable"] is True

    @pytest.mark.asyncio
    async def test_annotation_keys_stay_off_the_advertised_columns(self, handler):
        assert [column["id"] for column in handler._get_users_spending_columns()] == ["user_email", "total_cost_usd"]

    @pytest.mark.asyncio
    async def test_a_client_source_filter_asks_the_port_nothing_and_keeps_the_row_absent(
        self, handler, mock_repository
    ):
        """No ledger row records a client, so the figure would be the person's whole spend under one
        client's label. The candidate stays listed: dropping it would treat an absence as a zero."""
        mock_repository.execute_aggregation_query.return_value = {
            "aggregations": {"paginated_results": {"buckets": [{"key": "ada@example.com", "doc_count": 3}]}}
        }
        consumed = _answers(**{"u-ada": SpendResult(figure=Decimal("42.5"))})

        with _patched_users_port({"ada@example.com": self.ADA}, consumed):
            result = await handler.get_users_spending(
                start_date=PERIOD_START, end_date=PERIOD_END, client_source="teams"
            )

        consumed.assert_not_awaited()
        assert result["data"]["rows"] == [
            {
                "user_email": "ada@example.com",
                "total_cost_usd": None,
                "spend_unavailable": True,
                "spend_partial": False,
                "spend_missing_amount": None,
            }
        ]
        assert "total_cost_usd" not in result["data"]["totals"]
        assert result["data"]["totals"]["total_cost_usd_spend_unavailable"] is True


class TestUsersSpendingFooterAgainstTheLedger:
    """The users table's footer is checked against the ledger's figure for the same question.

    Elasticsearch enumerates the rows and the ledger fills them, so a user with ledger spend and no
    metric document in the window has money in the scope's total and no row to carry it.
    """

    ADA = _user_record("u-ada", "ada", "ada@example.com")

    async def _footer(
        self,
        handler,
        ledger_total: AsyncMock,
        row_figure: str = "24.12",
        expected_filter: ReportFilter = NO_FILTER,
        **enrich_kwargs,
    ) -> dict:
        rows = [{"user_email": "ada@example.com", "total_cost_usd": None}]
        consumed = _answers(expected_filter, **{"u-ada": SpendResult(figure=Decimal(row_figure))})
        with _patched_users_port({"ada@example.com": self.ADA}, consumed, ledger_total):
            enriched = await handler._enrich_users_spending(rows, PERIOD_START, PERIOD_END, **enrich_kwargs)
        return enriched.totals

    @pytest.mark.asyncio
    async def test_a_footer_short_of_the_ledger_total_is_partial_by_the_difference(self, handler):
        totals = await self._footer(handler, _ledger_total(SpendResult(figure=Decimal("25.92"))))

        assert totals["total_cost_usd"] == 24.12
        assert totals["total_cost_usd_spend_partial"] is True
        assert totals["total_cost_usd_spend_missing_amount"] == 1.8

    @pytest.mark.asyncio
    async def test_a_footer_equal_to_the_ledger_total_stays_exact(self, handler):
        totals = await self._footer(handler, _ledger_total(SpendResult(figure=Decimal("24.12"))))

        assert totals["total_cost_usd_spend_partial"] is False
        assert totals["total_cost_usd_spend_missing_amount"] is None

    @pytest.mark.asyncio
    async def test_a_half_cent_footer_equal_to_the_ledger_total_is_not_short_by_a_rounding_cent(self, handler):
        half_cent = "0.125"

        totals = await self._footer(
            handler, _ledger_total(SpendResult(figure=Decimal(half_cent))), row_figure=half_cent
        )

        assert totals["total_cost_usd_spend_partial"] is False
        assert totals["total_cost_usd_spend_missing_amount"] is None

    @pytest.mark.asyncio
    async def test_a_short_ledger_total_makes_the_footer_partial_by_an_unknown_amount(self, handler):
        short = SpendResult(
            figure=Decimal("25.92"),
            partial={SpendDimension.IDENTITY_BRIDGE: Portion(spend=None, rows=None)},
        )

        totals = await self._footer(handler, _ledger_total(short))

        assert totals["total_cost_usd_spend_partial"] is True
        assert totals["total_cost_usd_spend_missing_amount"] is None

    @pytest.mark.asyncio
    async def test_an_unavailable_ledger_total_leaves_the_footer_untouched(self, handler):
        totals = await self._footer(handler, _ledger_total(SpendResult.no_figure(PERIOD)))

        assert totals["total_cost_usd"] == 24.12
        assert totals["total_cost_usd_spend_partial"] is False

    @pytest.mark.asyncio
    async def test_a_report_without_a_period_reads_no_ledger_total(self, handler):
        ledger_total = _ledger_total(SpendResult(figure=Decimal("25.92")))

        totals = await self._footer(handler, ledger_total, client_source="teams")

        ledger_total.assert_not_awaited()
        assert "total_cost_usd" not in totals

    @pytest.mark.asyncio
    async def test_the_ledger_total_is_read_with_the_dashboards_own_filter(self, handler):
        ledger_total = AsyncMock(return_value={SpendGroup.TOTAL: SpendResult(figure=Decimal("24.12"))})

        await self._footer(handler, ledger_total, expected_filter=ReportFilter(projects=("alpha",)), projects=["alpha"])

        _, kwargs = ledger_total.call_args
        assert kwargs["access"] == _EXPECTED_SCOPE
        assert kwargs["report_filter"] == ReportFilter(projects=("alpha",))

    @pytest.mark.asyncio
    async def test_the_endpoint_hands_its_users_and_projects_filters_to_the_ledger_total(
        self, handler, mock_repository
    ):
        """The filters reach the ledger read through the endpoint, the users one resolved to a person."""
        _answer_buckets(mock_repository, [{"key": "ada@example.com", "doc_count": 3}])
        asked = ReportFilter(
            users=(UserTerm(user_id="u-ada", aliases=frozenset({"ada", "ada@example.com"})),),
            projects=("alpha",),
        )
        consumed = _answers(asked, **{"u-ada": SpendResult(figure=Decimal("24.12"))})
        ledger_total = AsyncMock(return_value={SpendGroup.TOTAL: SpendResult(figure=Decimal("24.12"))})

        with (
            _patched_users_port({"ada@example.com": self.ADA}, consumed, ledger_total),
            patch("codemie.service.analytics.handlers.report_filter.get_async_session", _fake_session),
        ):
            await handler.get_users_spending(
                start_date=PERIOD_START, end_date=PERIOD_END, users=["ada@example.com"], projects=["alpha"]
            )

        _, kwargs = ledger_total.call_args
        assert kwargs["report_filter"] == asked
        consumed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_person_filtered_to_one_project_is_read_for_that_project_alone(self, handler):
        """The defect this pins: an admin filtering the dashboard to one project was shown a person's
        spend across every project, because the holder read carried the boundary and not the filter."""
        consumed = AsyncMock(return_value={})
        ledger_total = AsyncMock(return_value={SpendGroup.TOTAL: SpendResult.no_figure(PERIOD)})
        rows = [{"user_email": "ada@example.com", "total_cost_usd": None}]

        with _patched_users_port({"ada@example.com": self.ADA}, consumed, ledger_total):
            await handler._enrich_users_spending(rows, PERIOD_START, PERIOD_END, projects=["spend-e2e"])

        _, kwargs = consumed.call_args
        assert kwargs["access"] == _EXPECTED_SCOPE
        assert kwargs["report_filter"] == ReportFilter(projects=("spend-e2e",))

    @pytest.mark.asyncio
    async def test_the_filter_is_built_once_and_both_reads_are_handed_that_one(self, handler):
        """Building it costs an identity lookup, and two builds a request apart are two chances to foot
        one table's rows and its total on different people."""
        asked = ReportFilter(projects=("alpha",))
        build = AsyncMock(return_value=asked)
        consumed = AsyncMock(return_value={})
        ledger_total = AsyncMock(return_value={SpendGroup.TOTAL: SpendResult.no_figure(PERIOD)})
        rows = [{"user_email": "ada@example.com", "total_cost_usd": None}]

        with (
            _patched_users_port({"ada@example.com": self.ADA}, consumed, ledger_total),
            patch("codemie.service.analytics.handlers.user_handler.report_filter_for", new=build),
        ):
            await handler._enrich_users_spending(rows, PERIOD_START, PERIOD_END, projects=["alpha"])

        build.assert_awaited_once_with(None, ["alpha"])
        assert consumed.call_args.kwargs["report_filter"] is asked
        assert ledger_total.call_args.kwargs["report_filter"] is asked

    @pytest.mark.asyncio
    async def test_an_empty_table_pays_no_identity_lookup(self, handler):
        """With no row neither the per-person read nor the footer total runs, so building the filter
        would spend a lookup on nothing."""
        build = AsyncMock()
        consumed = AsyncMock()
        ledger_total = AsyncMock()

        with (
            _patched_users_port({}, consumed, ledger_total),
            patch("codemie.service.analytics.handlers.user_handler.report_filter_for", new=build),
        ):
            await handler._enrich_users_spending([], PERIOD_START, PERIOD_END, users=["ada@example.com"])

        build.assert_not_awaited()
        consumed.assert_not_awaited()
        ledger_total.assert_not_awaited()


def _answer_buckets(mock_repository, buckets: list[dict]) -> None:
    mock_repository.execute_aggregation_query.return_value = {
        "aggregations": {"paginated_results": {"buckets": buckets}}
    }


def _sent_terms_aggregation(mock_repository) -> dict:
    (agg_body,), _ = mock_repository.execute_aggregation_query.call_args
    return agg_body["aggs"]["paginated_results"]


@pytest.fixture
def unresolved_identities():
    with patch("codemie.service.analytics.handlers.user_handler.UserIdentityResolver.resolve_rows", new=AsyncMock()):
        yield


ADA = _user_record("u-ada", "ada", "ada@example.com")
BOB = _user_record("u-bob", "bob", "bob@example.com")
RECORDS = {"ada": ADA, "bob": BOB}

#: A figure that is short in a dimension nobody can size - the state the port labels `partial`.
_SHORT = SpendResult(
    figure=Decimal("1.25"),
    partial={SpendDimension.IDENTITY_BRIDGE: Portion(spend=None, rows=None)},
)


def _exact(amount: str) -> SpendResult:
    return SpendResult(figure=Decimal(amount))


def _fake_report_filter_for(records: dict):
    """`report_filter_for` without the identity lookup: a known identifier resolves to its record's user."""

    async def _build(users, projects):
        terms = tuple(
            UserTerm(user_id=records[identifier].id, aliases=frozenset({records[identifier].username}))
            if identifier in records
            else UserTerm(user_id=identifier, aliases=None)
            for identifier in dict.fromkeys(user for user in users or () if user)
        )
        return ReportFilter(users=terms, projects=tuple(projects or ()))

    return _build


@dataclass(frozen=True)
class _GroupPort:
    """The two reads behind a per-user group table: every row in one batch, and the footer's whole scope."""

    rows: AsyncMock
    scope: AsyncMock


def _group_port(
    by_user_id: dict[str, dict[SpendGroup, SpendResult]],
    dashboard: dict[SpendGroup, SpendResult] | None = None,
) -> _GroupPort:
    """Stubs for `aggregate_by_spend_group_per_filter` (each user's groups) and `aggregate_by_spend_group` (the scope's).

    The caller's own boundary is asserted on every read, because a per-person figure read without it is
    the whole platform's spend on that person beside rows already narrowed to what the caller may see.
    A group nobody answered is `no_figure`, never a zero.
    """

    def _groups(period, answers):
        return {group: SpendResult.no_figure(period) for group in SpendGroup} | (answers or {})

    async def _rows(period, *, access, common_filter, report_filters):
        assert access == _EXPECTED_SCOPE, "every read must be narrowed by the caller's own boundary"
        return {
            report_filter: _groups(period, by_user_id.get(report_filter.users[0].user_id))
            for report_filter in report_filters
        }

    async def _scope(period, *, access, report_filter):
        assert access == _EXPECTED_SCOPE, "every read must be narrowed by the caller's own boundary"
        return _groups(period, dashboard)

    return _GroupPort(rows=AsyncMock(side_effect=_rows), scope=AsyncMock(side_effect=_scope))


def _filters_asked(port: _GroupPort) -> dict[str, object]:
    """What one table asked: the batch's common and per-row filters, and the footer's whole-scope filter."""
    batch = port.rows.await_args.kwargs
    return {
        "common": batch["common_filter"],
        "rows": batch["report_filters"],
        "footer": port.scope.await_args.kwargs["report_filter"],
    }


@contextlib.contextmanager
def _patched_group_port(port: _GroupPort):
    with (
        patch(
            "codemie.service.analytics.handlers.user_handler.report_filter_for", new=_fake_report_filter_for(RECORDS)
        ),
        patch(
            "codemie.service.analytics.handlers.ledger_group_spend.report_filter_for",
            new=_fake_report_filter_for(RECORDS),
        ),
        patch(
            "codemie.service.analytics.handlers.ledger_group_spend.spend_service.aggregate_by_spend_group_per_filter",
            new=port.rows,
        ),
        patch("codemie.service.analytics.handlers.user_handler.spend_service.aggregate_by_spend_group", new=port.scope),
    ):
        yield


@pytest.mark.usefixtures("unresolved_identities")
class TestPlatformSpendingByUsersFromLedger:
    """Platform Spent by User lists the active users from Elasticsearch and reads each one's money from the ledger."""

    @staticmethod
    def _bucket(user: str) -> dict:
        return {"key": user, "doc_count": 5}

    @pytest.mark.asyncio
    async def test_a_users_figure_is_the_ledgers_platform_group(self, handler, mock_repository):
        _answer_buckets(mock_repository, [self._bucket("ada")])
        port = _group_port({"u-ada": {SpendGroup.PLATFORM: _exact("1.25"), SpendGroup.CLI: _exact("99")}})

        with _patched_group_port(port):
            result = await handler.get_users_platform_spending()

        assert result["data"]["rows"] == [
            {
                "user_email": "ada",
                "total_cost_usd": 1.25,
                "spend_unavailable": False,
                "spend_partial": False,
                "spend_missing_amount": None,
            }
        ]

    @pytest.mark.asyncio
    async def test_elasticsearch_only_lists_the_candidates_and_sums_no_money(self, handler, mock_repository):
        _answer_buckets(mock_repository, [])

        with _patched_group_port(_group_port({})):
            await handler.get_users_platform_spending()

        terms = _sent_terms_aggregation(mock_repository)
        assert terms["terms"]["order"] == {"_count": "desc"}
        assert terms["aggs"] == {}

    @pytest.mark.asyncio
    async def test_a_user_the_ledger_has_no_figure_for_reads_unavailable_never_zero(self, handler, mock_repository):
        _answer_buckets(mock_repository, [self._bucket("ada")])

        with _patched_group_port(_group_port({})):
            result = await handler.get_users_platform_spending()

        assert result["data"]["rows"] == [
            {
                "user_email": "ada",
                "total_cost_usd": None,
                "spend_unavailable": True,
                "spend_partial": False,
                "spend_missing_amount": None,
            }
        ]
        assert "total_cost_usd" not in result["data"]["totals"]
        assert result["data"]["totals"]["total_cost_usd_spend_unavailable"] is True

    @pytest.mark.asyncio
    async def test_a_short_figure_reads_partial_and_so_does_the_footer(self, handler, mock_repository):
        _answer_buckets(mock_repository, [self._bucket("ada")])

        with _patched_group_port(_group_port({"u-ada": {SpendGroup.PLATFORM: _SHORT}})):
            result = await handler.get_users_platform_spending()

        row = result["data"]["rows"][0]
        assert row["total_cost_usd"] == 1.25
        assert row["spend_partial"] is True
        assert row["spend_unavailable"] is False
        assert result["data"]["totals"]["total_cost_usd_spend_partial"] is True

    @pytest.mark.asyncio
    async def test_a_footer_beside_a_user_without_a_figure_is_partial_and_that_user_ranks_last(
        self, handler, mock_repository
    ):
        _answer_buckets(mock_repository, [self._bucket("ada"), self._bucket("bob")])

        with _patched_group_port(_group_port({"u-bob": {SpendGroup.PLATFORM: _exact("2.0")}})):
            result = await handler.get_users_platform_spending()

        assert [row["user_email"] for row in result["data"]["rows"]] == ["bob", "ada"]
        assert result["data"]["totals"]["total_cost_usd"] == 2.0
        assert result["data"]["totals"]["total_cost_usd_spend_partial"] is True
        assert result["pagination"]["total_count"] == 2

    @pytest.mark.asyncio
    async def test_a_measured_zero_user_stays_listed_as_this_table_always_listed_them(self, handler, mock_repository):
        _answer_buckets(mock_repository, [self._bucket("ada")])

        with _patched_group_port(_group_port({"u-ada": {SpendGroup.PLATFORM: _exact("0")}})):
            result = await handler.get_users_platform_spending()

        assert result["data"]["rows"][0]["total_cost_usd"] == 0.0
        assert result["data"]["rows"][0]["spend_unavailable"] is False

    @pytest.mark.asyncio
    async def test_a_fully_resolved_table_has_an_exact_footer(self, handler, mock_repository):
        _answer_buckets(mock_repository, [self._bucket("ada")])
        port = _group_port(
            {"u-ada": {SpendGroup.PLATFORM: _exact("1.25")}}, dashboard={SpendGroup.PLATFORM: _exact("1.25")}
        )

        with _patched_group_port(port):
            result = await handler.get_users_platform_spending()

        assert result["data"]["totals"] == {
            "total_cost_usd": 1.25,
            "total_cost_usd_spend_unavailable": False,
            "total_cost_usd_spend_partial": False,
            "total_cost_usd_spend_missing_amount": None,
        }

    @pytest.mark.asyncio
    async def test_the_footer_is_partial_by_what_the_ledger_holds_beyond_the_listed_users(
        self, handler, mock_repository
    ):
        _answer_buckets(mock_repository, [self._bucket("ada")])
        port = _group_port(
            {"u-ada": {SpendGroup.PLATFORM: _exact("1.25")}}, dashboard={SpendGroup.PLATFORM: _exact("5.0")}
        )

        with _patched_group_port(port):
            result = await handler.get_users_platform_spending()

        totals = result["data"]["totals"]
        assert totals["total_cost_usd"] == 1.25
        assert totals["total_cost_usd_spend_partial"] is True
        assert totals["total_cost_usd_spend_missing_amount"] == 3.75

    @pytest.mark.asyncio
    async def test_each_read_is_that_person_inside_the_dashboards_projects_filter(self, handler, mock_repository):
        _answer_buckets(mock_repository, [self._bucket("ada")])
        port = _group_port({"u-ada": {SpendGroup.PLATFORM: _exact("1.25")}})

        with _patched_group_port(port):
            await handler.get_users_platform_spending(projects=["alpha"])

        assert _filters_asked(port) == {
            "common": ReportFilter(projects=("alpha",)),
            "rows": [ReportFilter(users=(UserTerm(user_id="u-ada", aliases=frozenset({"ada"})),), projects=("alpha",))],
            "footer": ReportFilter(projects=("alpha",)),
        }

    @pytest.mark.asyncio
    async def test_a_client_source_report_asks_the_port_nothing_and_publishes_no_figure(self, handler, mock_repository):
        _answer_buckets(mock_repository, [self._bucket("ada")])
        port = _group_port({"u-ada": {SpendGroup.PLATFORM: _exact("1.25")}})

        with _patched_group_port(port):
            result = await handler.get_users_platform_spending(client_source="vscode")

        port.rows.assert_not_awaited()
        port.scope.assert_not_awaited()
        assert result["data"]["rows"][0]["total_cost_usd"] is None
        assert result["data"]["rows"][0]["spend_unavailable"] is True


@pytest.mark.usefixtures("unresolved_identities")
class TestCliSpendingByUsersFromLedger:
    """CLI Spent by User lists the users with a CLI request from Elasticsearch and reads their CLI money from the ledger."""

    @staticmethod
    def _bucket(user: str) -> dict:
        return {"key": user, "doc_count": 5, "cli_request_filter": {"doc_count": 5}}

    @pytest.mark.asyncio
    async def test_a_users_figure_is_the_ledgers_cli_group(self, handler, mock_repository):
        _answer_buckets(mock_repository, [self._bucket("ada")])
        port = _group_port({"u-ada": {SpendGroup.CLI: _exact("1.5"), SpendGroup.PLATFORM: _exact("99")}})

        with _patched_group_port(port):
            result = await handler.get_users_cli_spending()

        assert result["data"]["rows"] == [
            {
                "user_name": "ada",
                "total_cost_usd": 1.5,
                "spend_unavailable": False,
                "spend_partial": False,
                "spend_missing_amount": None,
            }
        ]

    @pytest.mark.asyncio
    async def test_a_user_the_ledger_has_no_figure_for_is_kept_and_reads_unavailable(self, handler, mock_repository):
        _answer_buckets(mock_repository, [self._bucket("ada")])

        with _patched_group_port(_group_port({})):
            result = await handler.get_users_cli_spending()

        assert result["data"]["rows"] == [
            {
                "user_name": "ada",
                "total_cost_usd": None,
                "spend_unavailable": True,
                "spend_partial": False,
                "spend_missing_amount": None,
            }
        ]
        assert result["data"]["totals"]["total_cost_usd_spend_unavailable"] is True

    @pytest.mark.asyncio
    async def test_the_selector_keeps_users_by_cli_requests_and_no_money_is_summed_in_elasticsearch(
        self, handler, mock_repository
    ):
        _answer_buckets(mock_repository, [])

        with _patched_group_port(_group_port({})):
            await handler.get_users_cli_spending()

        terms = _sent_terms_aggregation(mock_repository)
        selector = terms["aggs"]["has_cli_request"]["bucket_selector"]
        assert selector["buckets_path"] == {"requests": "cli_request_filter>_count"}
        assert selector["script"] == "params.requests > 0"
        assert terms["aggs"]["cli_request_filter"] == {"filter": {"term": {"attributes.cli_request": True}}}
        assert terms["terms"]["order"] == {"_count": "desc"}

    @pytest.mark.asyncio
    async def test_a_partly_resolved_user_reads_partial_and_so_does_the_footer(self, handler, mock_repository):
        _answer_buckets(mock_repository, [self._bucket("ada"), self._bucket("bob")])
        short = SpendResult(figure=Decimal("0.37"), partial=_SHORT.partial)
        port = _group_port({"u-ada": {SpendGroup.CLI: short}, "u-bob": {SpendGroup.CLI: _exact("1.0")}})

        with _patched_group_port(port):
            result = await handler.get_users_cli_spending()

        rows = {row["user_name"]: row for row in result["data"]["rows"]}
        assert rows["ada"]["total_cost_usd"] == 0.37
        assert rows["ada"]["spend_partial"] is True
        assert rows["bob"]["spend_partial"] is False
        assert result["data"]["totals"]["total_cost_usd"] == 1.37
        assert result["data"]["totals"]["total_cost_usd_spend_partial"] is True

    @pytest.mark.asyncio
    async def test_a_user_who_measurably_spent_nothing_leaves_the_table(self, handler, mock_repository):
        _answer_buckets(mock_repository, [self._bucket("ada")])

        with _patched_group_port(_group_port({"u-ada": {SpendGroup.CLI: _exact("0")}})):
            result = await handler.get_users_cli_spending()

        assert result["data"]["rows"] == []
        assert result["pagination"]["total_count"] == 0

    @pytest.mark.asyncio
    async def test_a_sub_cent_spend_is_kept_rather_than_rounded_into_a_zero(self, handler, mock_repository):
        _answer_buckets(mock_repository, [self._bucket("ada")])

        with _patched_group_port(_group_port({"u-ada": {SpendGroup.CLI: _exact("0.004")}})):
            result = await handler.get_users_cli_spending()

        assert result["data"]["rows"][0]["total_cost_usd"] == 0.004

    @pytest.mark.asyncio
    async def test_the_footer_is_partial_by_what_the_ledger_holds_beyond_the_listed_users(
        self, handler, mock_repository
    ):
        _answer_buckets(mock_repository, [self._bucket("ada")])
        port = _group_port({"u-ada": {SpendGroup.CLI: _exact("1.0")}}, dashboard={SpendGroup.CLI: _exact("4.0")})

        with _patched_group_port(port):
            result = await handler.get_users_cli_spending()

        totals = result["data"]["totals"]
        assert totals["total_cost_usd"] == 1.0
        assert totals["total_cost_usd_spend_partial"] is True
        assert totals["total_cost_usd_spend_missing_amount"] == 3.0

    @pytest.mark.asyncio
    async def test_each_read_is_that_person_inside_the_dashboards_projects_filter(self, handler, mock_repository):
        _answer_buckets(mock_repository, [self._bucket("ada")])
        port = _group_port({"u-ada": {SpendGroup.CLI: _exact("1.0")}})

        with _patched_group_port(port):
            await handler.get_users_cli_spending(projects=["alpha"])

        assert _filters_asked(port) == {
            "common": ReportFilter(projects=("alpha",)),
            "rows": [ReportFilter(users=(UserTerm(user_id="u-ada", aliases=frozenset({"ada"})),), projects=("alpha",))],
            "footer": ReportFilter(projects=("alpha",)),
        }
