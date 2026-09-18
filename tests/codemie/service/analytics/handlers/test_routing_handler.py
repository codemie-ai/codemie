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

"""Unit tests for RoutingHandler."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.routing_handler import RoutingHandler
from codemie.service.analytics.metric_names import MetricName


@pytest.fixture
def mock_user():
    user = MagicMock(spec=User)
    user.project_names = []
    user.admin_project_names = []
    user.is_global_user = False
    user.is_admin = False
    user.id = "test-user-id"
    return user


@pytest.fixture
def mock_repository():
    return MagicMock(spec=MetricsElasticRepository)


@pytest.fixture
def handler(mock_user, mock_repository):
    return RoutingHandler(mock_user, mock_repository)


class TestGetRoutingSummary:
    @pytest.mark.asyncio
    async def test_returns_summary_response(self, handler):
        handler._pipeline.execute_summary_query = AsyncMock(return_value={"data": {"metrics": []}, "metadata": {}})
        result = await handler.get_routing_summary()
        assert "data" in result
        handler._pipeline.execute_summary_query.assert_called_once()

    @pytest.mark.asyncio
    async def test_uses_routing_call_usage_metric(self, handler):
        handler._pipeline.execute_summary_query = AsyncMock(return_value={"data": {"metrics": []}, "metadata": {}})
        await handler.get_routing_summary()
        call_kwargs = handler._pipeline.execute_summary_query.call_args.kwargs
        assert MetricName.ROUTING_CALL_USAGE.value in call_kwargs.get("metric_filters", [])

    @pytest.mark.asyncio
    async def test_empty_result_returns_zero_metrics(self, handler):
        handler._pipeline.execute_summary_query = AsyncMock(return_value={"data": {"metrics": []}, "metadata": {}})
        result = await handler.get_routing_summary()
        assert result is not None  # no exception on empty result


class TestGetModelSwitches:
    @pytest.mark.asyncio
    async def test_returns_tabular_response(self, handler):
        handler._pipeline.execute_tabular_query = AsyncMock(
            return_value={"data": {"rows": [], "columns": [], "total_count": 0}, "metadata": {}}
        )
        result = await handler.get_model_switches()
        assert "data" in result

    @pytest.mark.asyncio
    async def test_uses_routing_call_usage_metric(self, handler):
        handler._pipeline.execute_tabular_query = AsyncMock(
            return_value={"data": {"rows": [], "columns": [], "total_count": 0}, "metadata": {}}
        )
        await handler.get_model_switches()
        call_kwargs = handler._pipeline.execute_tabular_query.call_args.kwargs
        assert MetricName.ROUTING_CALL_USAGE.value in call_kwargs.get("metric_filters", [])

    @pytest.mark.asyncio
    async def test_accepts_session_id(self, handler):
        handler._pipeline.execute_tabular_query = AsyncMock(
            return_value={"data": {"rows": [], "columns": [], "total_count": 0}, "metadata": {}}
        )
        result = await handler.get_model_switches(session_id="sess-1")
        assert result is not None


class TestGetTierDistribution:
    @pytest.mark.asyncio
    async def test_returns_tabular_response(self, handler):
        handler._pipeline.execute_tabular_query = AsyncMock(
            return_value={"data": {"rows": [], "columns": [], "total_count": 0}, "metadata": {}}
        )
        result = await handler.get_tier_distribution()
        assert "data" in result

    @pytest.mark.asyncio
    async def test_uses_routing_call_usage_metric(self, handler):
        handler._pipeline.execute_tabular_query = AsyncMock(
            return_value={"data": {"rows": [], "columns": [], "total_count": 0}, "metadata": {}}
        )
        await handler.get_tier_distribution()
        call_kwargs = handler._pipeline.execute_tabular_query.call_args.kwargs
        assert MetricName.ROUTING_CALL_USAGE.value in call_kwargs.get("metric_filters", [])


class TestGetModelDistributions:
    @pytest.mark.asyncio
    async def test_returns_routed_model_distribution(self, handler):
        handler._pipeline.execute_tabular_query = AsyncMock(
            return_value={"data": {"rows": [], "columns": [], "total_count": 0}, "metadata": {}}
        )

        result = await handler.get_routed_model_distribution()

        assert "data" in result
        call_kwargs = handler._pipeline.execute_tabular_query.call_args.kwargs
        assert MetricName.ROUTING_CALL_USAGE.value in call_kwargs.get("metric_filters", [])


class TestRoutingComposition:
    @pytest.mark.asyncio
    async def test_returns_routing_activity_for_stacked_chart(self, handler):
        async def execute_query(**kwargs):
            return kwargs["result_parser"]({}, {})

        handler._pipeline.execute_composite_query = AsyncMock(side_effect=execute_query)

        result = await handler.get_routing_activity()

        assert "data" in result
        call_kwargs = handler._pipeline.execute_composite_query.call_args.kwargs
        assert MetricName.ROUTING_CALL_USAGE.value in call_kwargs.get("metric_filters", [])
        assert [column["id"] for column in result["data"]["columns"]] == [
            "time",
            "simple_requests",
            "medium_requests",
            "complex_requests",
            "reasoning_requests",
        ]

    @pytest.mark.asyncio
    async def test_returns_grouped_routing_paths(self, handler):
        handler._pipeline.execute_tabular_query_with_flattened_rows = AsyncMock(
            return_value={"data": {"rows": [], "columns": [], "total_count": 0}, "metadata": {}}
        )

        result = await handler.get_routing_paths()

        assert "data" in result
        call_kwargs = handler._pipeline.execute_tabular_query_with_flattened_rows.call_args.kwargs
        assert MetricName.ROUTING_CALL_USAGE.value in call_kwargs.get("metric_filters", [])

    def test_builds_routing_activity_as_time_histogram_with_tier_buckets(self, handler):
        aggregation = handler._build_routing_activity_agg({"match_all": {}})
        time_bucket = aggregation["aggs"]["time_buckets"]

        assert "date_histogram" in time_bucket
        assert time_bucket["date_histogram"]["fixed_interval"] == "3h"
        assert "tiers" in time_bucket["aggs"]

    def test_parses_routing_activity_into_stacked_series(self, handler):
        rows = handler._parse_routing_activity(
            {
                "aggregations": {
                    "time_buckets": {
                        "buckets": [
                            {
                                "key_as_string": "2026-09-15T12:00:00.000Z",
                                "tiers": {
                                    "buckets": [
                                        {"key": "simple", "doc_count": 8},
                                        {"key": "simple", "doc_count": 3},
                                        {"key": "reasoning", "doc_count": 1},
                                    ]
                                },
                            }
                        ]
                    }
                }
            }
        )

        assert rows == [
            {
                "time": "2026-09-15T12:00:00.000Z",
                "simple_requests": 11,
                "medium_requests": 0,
                "complex_requests": 0,
                "reasoning_requests": 1,
            }
        ]

    def test_preserves_tier_values_and_normalizes_sources_in_timeline(self, handler):
        result = handler._parse_decision_timeline(
            {
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "@timestamp": "2026-09-15T12:00:00Z",
                                "attributes": {
                                    "tier": "capable",
                                    "decision_source": "llm_classifier",
                                },
                            }
                        }
                    ]
                }
            }
        )

        assert result[0]["tier"] == "capable"
        assert result[0]["decision_source"] == "llm-classifier"

    def test_parses_canonical_tier_buckets(self, handler):
        rows = handler._parse_terms_result(
            {
                "aggregations": {
                    "paginated_results": {
                        "buckets": [
                            {"key": "simple", "doc_count": 2},
                            {"key": "medium", "doc_count": 3},
                            {"key": "complex", "doc_count": 4},
                        ]
                    }
                }
            },
            "tier",
        )

        assert rows == [
            {"tier": "simple", "request_count": 2},
            {"tier": "medium", "request_count": 3},
            {"tier": "complex", "request_count": 4},
        ]

    def test_parses_grouped_router_paths(self, handler):
        rows = handler._parse_routing_paths(
            {
                "aggregations": {
                    "routers": {
                        "buckets": [
                            {
                                "key": "gpt-smart-router",
                                "models": {
                                    "buckets": [
                                        {
                                            "key": "claude-sonnet-5",
                                            "tiers": {
                                                "buckets": [
                                                    {
                                                        "key": "medium",
                                                        "doc_count": 4,
                                                        "actual_cost": {"value": 0.05},
                                                        "estimated_max_cost": {"value": 0.08},
                                                        "potential_savings": {"value": 0.02},
                                                    }
                                                ]
                                            },
                                        }
                                    ]
                                },
                            }
                        ]
                    }
                }
            }
        )

        assert rows == [
            {
                "router": "gpt-smart-router",
                "routed_model": "claude-sonnet-5",
                "tier": "medium",
                "request_count": 4,
                "actual_cost_usd": 0.05,
                "estimated_max_cost_usd": 0.08,
                "potential_savings_usd": 0.02,
            }
        ]

    def test_normalizes_negative_zero_costs(self, handler):
        rows = handler._parse_routing_paths(
            {
                "aggregations": {
                    "routers": {
                        "buckets": [
                            {
                                "key": "router",
                                "models": {
                                    "buckets": [
                                        {
                                            "key": "model",
                                            "tiers": {
                                                "buckets": [
                                                    {
                                                        "key": "simple",
                                                        "doc_count": 1,
                                                        "actual_cost": {"value": -0.001},
                                                        "estimated_max_cost": {"value": 0.0},
                                                        "potential_savings": {"value": -0.001},
                                                    }
                                                ]
                                            },
                                        }
                                    ]
                                },
                            }
                        ]
                    }
                }
            }
        )

        assert rows[0]["actual_cost_usd"] == 0.0
        assert rows[0]["potential_savings_usd"] == 0.0

    @pytest.mark.asyncio
    async def test_returns_requested_model_distribution(self, handler):
        handler._pipeline.execute_tabular_query = AsyncMock(
            return_value={"data": {"rows": [], "columns": [], "total_count": 0}, "metadata": {}}
        )

        result = await handler.get_requested_model_distribution()

        assert "data" in result
        call_kwargs = handler._pipeline.execute_tabular_query.call_args.kwargs
        assert MetricName.ROUTING_CALL_USAGE.value in call_kwargs.get("metric_filters", [])


class TestGetDecisionSourceDistribution:
    @pytest.mark.asyncio
    async def test_returns_tabular_response(self, handler):
        handler._pipeline.execute_tabular_query = AsyncMock(
            return_value={"data": {"rows": [], "columns": [], "total_count": 0}, "metadata": {}}
        )
        result = await handler.get_decision_source_distribution()
        assert "data" in result

    @pytest.mark.asyncio
    async def test_uses_routing_call_usage_metric(self, handler):
        handler._pipeline.execute_tabular_query = AsyncMock(
            return_value={"data": {"rows": [], "columns": [], "total_count": 0}, "metadata": {}}
        )
        await handler.get_decision_source_distribution()
        call_kwargs = handler._pipeline.execute_tabular_query.call_args.kwargs
        assert MetricName.ROUTING_CALL_USAGE.value in call_kwargs.get("metric_filters", [])


class TestGetDecisionTimeline:
    @pytest.mark.asyncio
    async def test_returns_decision_rows(self, handler):
        handler._pipeline.execute_search_rows = AsyncMock(
            return_value={"data": {"rows": [], "columns": [], "total_count": 0}, "metadata": {}}
        )

        result = await handler.get_decision_timeline()

        assert "data" in result
        handler._pipeline.execute_search_rows.assert_called_once()

    def test_parses_routing_decision_fields(self, handler):
        result = handler._parse_decision_timeline(
            {
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "@timestamp": "2026-09-15T12:00:00Z",
                                "attributes": {
                                    "routed_model": "claude-sonnet-5",
                                    "tier": "medium",
                                    "decision_source": "llm-classifier",
                                    "confidence": 0.82,
                                },
                            }
                        }
                    ]
                }
            }
        )

        assert result == [
            {
                "timestamp": "2026-09-15T12:00:00Z",
                "routed_model": "claude-sonnet-5",
                "tier": "medium",
                "decision_source": "llm-classifier",
                "confidence": 0.82,
            }
        ]


class TestGetClassifierOverhead:
    @pytest.mark.asyncio
    async def test_returns_summary_response(self, handler):
        handler._pipeline.execute_summary_query = AsyncMock(return_value={"data": {"metrics": []}, "metadata": {}})
        result = await handler.get_classifier_overhead()
        assert "data" in result

    @pytest.mark.asyncio
    async def test_uses_routing_call_usage_metric(self, handler):
        handler._pipeline.execute_summary_query = AsyncMock(return_value={"data": {"metrics": []}, "metadata": {}})
        await handler.get_classifier_overhead()
        call_kwargs = handler._pipeline.execute_summary_query.call_args.kwargs
        assert MetricName.ROUTING_CALL_USAGE.value in call_kwargs.get("metric_filters", [])


class TestRoutingHandlerAggBuilders:
    """Unit tests for internal aggregation builders and parsers."""

    def test_routing_summary_uses_value_count_not_hits_total(self, handler):
        """_build_routing_summary_agg must use a value_count agg for request_count, not hits.total."""
        dummy_query: dict = {"match_all": {}}
        agg_body = handler._build_routing_summary_agg(dummy_query)
        aggs = agg_body.get("aggs", {})
        assert "request_count" in aggs, "request_count aggregation is missing from summary agg"
        assert "value_count" in aggs["request_count"], "request_count must use value_count aggregation"

    def test_routing_summary_includes_decision_source_counts(self, handler):
        agg_body = handler._build_routing_summary_agg({"match_all": {}})

        assert "decision_source_counts" in agg_body["aggs"]

    def test_parse_routing_summary_reads_request_count_from_agg(self, handler):
        """_parse_routing_summary must read request_count from aggregations, not hits.total."""
        fake_result = {
            "hits": {"total": {"value": 0}},  # track_total_hits is False — always 0
            "aggregations": {
                "session_count": {"value": 3},
                "request_count": {"value": 42},
                "total_classifier_cost": {"value": 0.005},
            },
        }
        metrics = handler._parse_routing_summary(fake_result)
        rc = next((m for m in metrics if m["id"] == "request_count"), None)
        assert rc is not None
        assert rc["value"] == 42, f"Expected 42 from agg, got {rc['value']}"
        assert next(m for m in metrics if m["id"] == "total_classifier_cost")["format"] == "currency"

    def test_parse_routing_summary_returns_requested_order_and_decision_counts(self, handler):
        metrics = handler._parse_routing_summary(
            {
                "aggregations": {
                    "request_count": {"value": 42},
                    "session_count": {"value": 7},
                    "total_original_cost": {"value": 0.4},
                    "total_classifier_cost": {"value": 0.0},
                    "total_estimated_max_cost": {"value": 0.8},
                    "total_potential_savings": {"value": 0.4},
                    "decision_source_counts": {
                        "buckets": [
                            {"key": "llm_classifier", "doc_count": 12},
                            {"key": "heuristic_scorer", "doc_count": 30},
                        ]
                    },
                }
            }
        )

        assert [metric["id"] for metric in metrics] == [
            "total_original_cost",
            "total_classifier_cost",
            "total_estimated_max_cost",
            "total_potential_savings",
            "request_count",
            "session_count",
            "llm_classifier_count",
            "heuristic_count",
        ]
        assert metrics[1]["value"] == 0.0
        assert metrics[-2]["value"] == 12
        assert metrics[-1]["value"] == 30

    def test_model_switches_agg_includes_date_histogram(self, handler):
        """_build_model_switches_agg must include a date_histogram for ordered timeline."""
        dummy_query: dict = {"match_all": {}}
        agg_body = handler._build_model_switches_agg(dummy_query, 20, None)
        aggs = agg_body.get("aggs", {})
        # paginated_results should be a date_histogram (not bare terms)
        paginated = aggs.get("paginated_results", {})
        assert "date_histogram" in paginated, (
            "paginated_results must use date_histogram for ordered timeline, got: " + str(list(paginated.keys()))
        )

    def test_model_switches_agg_session_id_filter_with_empty_string(self, handler):
        """Empty-string session_id must apply a term filter, not skip it."""
        dummy_query: dict = {"match_all": {}}
        agg_body = handler._build_model_switches_agg(dummy_query, 20, "")
        # The query should be wrapped in a bool/must with a term filter
        q = agg_body.get("query", {})
        assert "bool" in q, "empty-string session_id must trigger the term filter"
        must_clauses = q["bool"].get("must", [])
        term_clauses = [c for c in must_clauses if "term" in c]
        assert len(term_clauses) > 0, "a term filter for conversation_id must be present"

    def test_model_switches_agg_none_session_id_skips_filter(self, handler):
        """session_id=None must NOT apply a term filter."""
        dummy_query: dict = {"match_all": {}}
        agg_body = handler._build_model_switches_agg(dummy_query, 20, None)
        q = agg_body.get("query", {})
        # query should be the plain dummy_query, not a bool wrapper
        assert "bool" not in q, "None session_id should not wrap query in bool/must"

    def test_classifier_overhead_agg_includes_cached_tokens(self, handler):
        """_build_classifier_overhead_agg must sum classifier_cached_tokens."""
        dummy_query: dict = {"match_all": {}}
        agg_body = handler._build_classifier_overhead_agg(dummy_query)
        aggs = agg_body.get("aggs", {})
        assert "total_cached_tokens" in aggs, "classifier_cached_tokens aggregation is missing"
        assert "sum" in aggs["total_cached_tokens"], "total_cached_tokens must use sum aggregation"

    def test_parse_classifier_overhead_includes_cached_tokens(self, handler):
        """_parse_classifier_overhead must surface total_cached_tokens in the result."""
        fake_result = {
            "aggregations": {
                "total_classifier_cost": {"value": 0.003},
                "total_input_tokens": {"value": 300},
                "total_output_tokens": {"value": 50},
                "total_cached_tokens": {"value": 10},
            }
        }
        metrics = handler._parse_classifier_overhead(fake_result)
        ids = [m["id"] for m in metrics]
        assert "total_cached_tokens" in ids, "cached tokens metric must be in overhead response"
        cached_metric = next(m for m in metrics if m["id"] == "total_cached_tokens")
        assert cached_metric["value"] == 10
