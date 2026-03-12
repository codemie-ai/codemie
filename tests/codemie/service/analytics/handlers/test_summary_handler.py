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

"""Unit tests for SummaryHandler."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.summary_handler import SummaryHandler


@pytest.fixture
def mock_user():
    """Create mock user."""
    user = MagicMock(spec=User)
    user.project_names = []
    user.admin_project_names = []
    user.is_global_user = False
    user.id = "test-user-id"
    return user


@pytest.fixture
def mock_repository():
    """Create mock repository."""
    return MagicMock(spec=MetricsElasticRepository)


@pytest.fixture
def handler(mock_user, mock_repository):
    """Create handler with mocked dependencies."""
    return SummaryHandler(mock_user, mock_repository)


class TestGetSummaries:
    """Tests for get_summaries method."""

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.cli_cost_processor.config.CLI_METRICS_CUTOFF_DATE", "2024-01-01")
    async def test_get_summaries_delegates_to_pipeline(self, handler, mock_repository):
        """Verify handler delegates to query pipeline correctly."""
        # Arrange - Mock three separate queries (unique_users + main + CLI costs)
        # CLI cutoff date is mocked to 2024-01-01, so all queries execute
        mock_repository.execute_aggregation_query.side_effect = [
            # First call: unique_users query
            {"aggregations": {"unique_users": {"value": 12}}},
            # Second call: main query with nested structure
            {
                "aggregations": {
                    "web_llm_tokens": {
                        "input_tokens": {"value": 80000},
                        "cached_input_tokens": {"value": 15000},
                        "output_tokens": {"value": 40000},
                    },
                    "cli_tokens": {
                        "cli_input_tokens": {"value": 20000},
                        "cli_cached_input_tokens": {"value": 5000},
                        "cli_output_tokens": {"value": 10000},
                    },
                    "total_money_spent": {"sum": {"value": 123.456}},
                    "unique_assistants": {"count": {"value": 100}},
                    "unique_workflows": {"count": {"value": 50}},
                    "embedding_metrics": {
                        "input_tokens": {"value": 5000},
                        "money_spent": {"value": 0.5},
                    },
                    "llm_cost": {
                        "money_spent": {"value": 122.956},
                    },
                    "cli_cost": {
                        "money_spent": {"value": 25.50},
                    },
                }
            },
            # Third call: CLI costs query (separate query with adjusted dates)
            {
                "aggregations": {
                    "total_cost": {"value": 25.50},
                }
            },
        ]

        # Act
        result = await handler.get_summaries(time_period="last_30_days", users=["user1"])

        # Assert
        assert result is not None
        assert "data" in result
        assert "metrics" in result["data"]
        assert "metadata" in result
        # Verify repository was called three times (unique_users + main + CLI costs)
        assert mock_repository.execute_aggregation_query.call_count == 3

    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.cli_cost_processor.config.CLI_METRICS_CUTOFF_DATE", "2024-01-01")
    async def test_get_summaries_uses_correct_metric_filters(self, handler, mock_repository):
        """Verify correct metrics are filtered (including CLI metrics)."""
        # Arrange - Mock three separate queries (unique_users + main + CLI costs)
        # CLI cutoff date is mocked to 2024-01-01, so all queries execute
        mock_repository.execute_aggregation_query.side_effect = [
            # First call: unique_users query
            {"aggregations": {"unique_users": {"value": 0}}},
            # Second call: main query with nested structure
            {
                "aggregations": {
                    "web_llm_tokens": {
                        "input_tokens": {"value": 0},
                        "cached_input_tokens": {"value": 0},
                        "output_tokens": {"value": 0},
                    },
                    "cli_tokens": {
                        "cli_input_tokens": {"value": 0},
                        "cli_cached_input_tokens": {"value": 0},
                        "cli_output_tokens": {"value": 0},
                    },
                    "total_money_spent": {"sum": {"value": 0.0}},
                    "unique_assistants": {"count": {"value": 0}},
                    "unique_workflows": {"count": {"value": 0}},
                    "embedding_metrics": {
                        "input_tokens": {"value": 0},
                        "money_spent": {"value": 0.0},
                    },
                    "llm_cost": {
                        "money_spent": {"value": 0.0},
                    },
                    "cli_cost": {
                        "money_spent": {"value": 0.0},
                    },
                }
            },
            # Third call: CLI costs query (separate query with adjusted dates)
            {
                "aggregations": {
                    "total_cost": {"value": 0.0},
                }
            },
        ]

        # Act
        result = await handler.get_summaries(time_period="last_7_days")

        # Assert
        # Verify that repository was called three times (unique_users + main + CLI costs)
        assert mock_repository.execute_aggregation_query.call_count == 3
        # The pipeline internally uses SUMMARY_METRICS filters (includes CLI_COMMAND_EXECUTION_TOTAL)
        assert result is not None


class TestAggregationBuilder:
    """Tests for aggregation builder methods."""

    def test_build_summaries_aggregation_structure(self, handler):
        """Verify aggregation structure for ES query (web LLM + CLI + embeddings)."""
        # Arrange
        test_query = {"bool": {"filter": []}}

        # Act
        agg_body = handler._build_summaries_aggregation(test_query)

        # Assert
        assert agg_body["query"] == test_query
        assert agg_body["size"] == 0  # Aggregation only, no docs
        assert "aggs" in agg_body

        # Verify 8 aggregations exist (web_llm_tokens, cli_tokens, total_money_spent, 2 unique counts, embeddings, llm_cost, cli_cost)
        assert len(agg_body["aggs"]) == 8
        assert "web_llm_tokens" in agg_body["aggs"]
        assert "cli_tokens" in agg_body["aggs"]
        assert "total_money_spent" in agg_body["aggs"]
        assert "unique_assistants" in agg_body["aggs"]
        assert "unique_workflows" in agg_body["aggs"]
        assert "embedding_metrics" in agg_body["aggs"]
        assert "llm_cost" in agg_body["aggs"]
        assert "cli_cost" in agg_body["aggs"]

        # Verify web_llm_tokens has nested structure with filter
        web_llm_aggs = agg_body["aggs"]["web_llm_tokens"]["aggs"]
        assert "input_tokens" in web_llm_aggs
        assert "output_tokens" in web_llm_aggs
        assert "cached_input_tokens" in web_llm_aggs
        assert web_llm_aggs["input_tokens"]["sum"]["field"] == "attributes.input_tokens"
        assert web_llm_aggs["cached_input_tokens"]["sum"]["field"] == "attributes.cache_read_input_tokens"
        assert web_llm_aggs["output_tokens"]["sum"]["field"] == "attributes.output_tokens"

        # Verify CLI tokens are nested in cli_tokens bucket with filter
        cli_tokens_aggs = agg_body["aggs"]["cli_tokens"]["aggs"]
        assert "cli_input_tokens" in cli_tokens_aggs
        assert "cli_output_tokens" in cli_tokens_aggs
        assert "cli_cached_input_tokens" in cli_tokens_aggs
        assert cli_tokens_aggs["cli_input_tokens"]["sum"]["field"] == "attributes.input_tokens"
        assert cli_tokens_aggs["cli_cached_input_tokens"]["sum"]["field"] == "attributes.cache_read_input_tokens"
        assert cli_tokens_aggs["cli_output_tokens"]["sum"]["field"] == "attributes.output_tokens"

        # Verify money spent has nested structure with filter (to exclude OLD CLI metric)
        assert "filter" in agg_body["aggs"]["total_money_spent"]
        assert "aggs" in agg_body["aggs"]["total_money_spent"]
        assert agg_body["aggs"]["total_money_spent"]["aggs"]["sum"]["sum"]["field"] == "attributes.money_spent"


class TestMetricsBuilder:
    """Tests for metrics builder methods."""

    def test_build_summaries_metrics_parses_es_result(self, handler):
        """Verify metrics are extracted from ES result correctly (web LLM + CLI + embeddings + unique counts)."""
        # Arrange
        result = {
            "aggregations": {
                "web_llm_tokens": {
                    "input_tokens": {"value": 80000},
                    "cached_input_tokens": {"value": 15000},
                    "output_tokens": {"value": 40000},
                },
                "cli_tokens": {
                    "cli_input_tokens": {"value": 20000},
                    "cli_cached_input_tokens": {"value": 5000},
                    "cli_output_tokens": {"value": 10000},
                },
                "total_money_spent": {"sum": {"value": 123.456}},
                "unique_assistants": {"count": {"value": 100}},
                "unique_workflows": {"count": {"value": 50}},
                "embedding_metrics": {
                    "input_tokens": {"value": 5000},
                    "money_spent": {"value": 0.5},
                },
                "llm_cost": {
                    "money_spent": {"value": 122.956},
                },
                "cli_cost": {
                    "money_spent": {"value": 25.50},
                },
            }
        }

        # Act
        metrics = handler._build_summaries_metrics(result, unique_users_count=12)

        # Assert
        assert len(metrics) == 11  # 3 LLM token metrics + 1 embedding token + 3 unique counts + 4 cost metrics

        # Verify input tokens metric (web + CLI, LLM only)
        input_metric = next(m for m in metrics if m["id"] == "total_input_tokens")
        assert input_metric["label"] == "LLM Input Tokens"
        assert input_metric["type"] == "number"
        assert input_metric["value"] == 100000  # 80000 + 20000
        assert input_metric["format"] == "number"

        # Verify cached input tokens metric (web + CLI, LLM only)
        cached_metric = next(m for m in metrics if m["id"] == "total_cached_input_tokens")
        assert cached_metric["value"] == 20000  # 15000 + 5000

        # Verify output tokens metric (web + CLI, LLM only)
        output_metric = next(m for m in metrics if m["id"] == "total_output_tokens")
        assert output_metric["value"] == 50000  # 40000 + 10000

        # Verify embedding metrics
        embedding_tokens_metric = next(m for m in metrics if m["id"] == "embedding_input_tokens")
        assert embedding_tokens_metric["value"] == 5000

        # Verify unique counts
        unique_users_metric = next(m for m in metrics if m["id"] == "unique_active_users")
        assert unique_users_metric["value"] == 12

        unique_assistants_metric = next(m for m in metrics if m["id"] == "unique_assistants_invoked")
        assert unique_assistants_metric["value"] == 100

        unique_workflows_metric = next(m for m in metrics if m["id"] == "unique_workflows_invoked")
        assert unique_workflows_metric["value"] == 50

    def test_build_summaries_metrics_handles_missing_aggregations(self, handler):
        """Verify graceful handling when ES returns no aggregations."""
        # Arrange
        result = {"aggregations": {}}

        # Act
        metrics = handler._build_summaries_metrics(result, unique_users_count=0)

        # Assert
        assert len(metrics) == 11
        # All values should default to 0 or 0.0
        for metric in metrics:
            assert metric["value"] == 0 or metric["value"] == 0.0

    def test_build_summaries_metrics_handles_none_values(self, handler):
        """Verify None values in aggregations are handled."""
        # Arrange
        # Note: If ES returns {"value": None}, the code would raise TypeError
        # This test verifies behavior when value key is missing entirely (uses default 0)
        result = {
            "aggregations": {
                "web_llm_tokens": {},  # Missing nested aggs
                "cli_tokens": {},
                "total_money_spent": {},
                "embedding_metrics": {},
                "llm_cost": {},
                "cli_cost": {},
            }
        }

        # Act
        metrics = handler._build_summaries_metrics(result, unique_users_count=0)

        # Assert
        assert len(metrics) == 11
        # When value key is missing, .get("value", 0) returns 0
        for metric in metrics:
            assert metric["value"] == 0 or metric["value"] == 0.0

    def test_build_summaries_metrics_combines_web_and_cli_tokens(self, handler):
        """Verify metrics correctly sum web LLM + CLI token values (excludes embeddings)."""
        # Arrange
        result = {
            "aggregations": {
                "web_llm_tokens": {
                    "input_tokens": {"value": 50000},
                    "cached_input_tokens": {"value": 10000},
                    "output_tokens": {"value": 25000},
                },
                "cli_tokens": {
                    "cli_input_tokens": {"value": 30000},
                    "cli_cached_input_tokens": {"value": 8000},
                    "cli_output_tokens": {"value": 15000},
                },
                "unique_assistants": {"count": {"value": 75}},
                "unique_workflows": {"count": {"value": 25}},
                "embedding_metrics": {
                    "input_tokens": {"value": 2000},
                    "money_spent": {"value": 0.25},
                },
                "llm_cost": {
                    "money_spent": {"value": 75.25},
                },
                "cli_cost": {
                    "money_spent": {"value": 30.00},
                },
            }
        }

        # Act
        metrics = handler._build_summaries_metrics(result, unique_users_count=5)

        # Assert
        assert len(metrics) == 11

        # Verify combined LLM values (excludes embeddings)
        input_metric = next(m for m in metrics if m["id"] == "total_input_tokens")
        assert input_metric["value"] == 80000  # 50000 (web LLM) + 30000 (CLI)

        cached_metric = next(m for m in metrics if m["id"] == "total_cached_input_tokens")
        assert cached_metric["value"] == 18000  # 10000 (web LLM) + 8000 (CLI)

        output_metric = next(m for m in metrics if m["id"] == "total_output_tokens")
        assert output_metric["value"] == 40000  # 25000 (web LLM) + 15000 (CLI)

        # Verify embedding metrics are separate
        embedding_tokens_metric = next(m for m in metrics if m["id"] == "embedding_input_tokens")
        assert embedding_tokens_metric["value"] == 2000

        # Verify unique counts
        unique_users_metric = next(m for m in metrics if m["id"] == "unique_active_users")
        assert unique_users_metric["value"] == 5

        unique_assistants_metric = next(m for m in metrics if m["id"] == "unique_assistants_invoked")
        assert unique_assistants_metric["value"] == 75

        unique_workflows_metric = next(m for m in metrics if m["id"] == "unique_workflows_invoked")
        assert unique_workflows_metric["value"] == 25

    def test_build_summaries_metrics_handles_missing_cli_fields(self, handler):
        """Verify graceful handling when only web LLM metrics exist (no CLI data)."""
        # Arrange - Web-only user scenario
        result = {
            "aggregations": {
                "web_llm_tokens": {
                    "input_tokens": {"value": 60000},
                    "cached_input_tokens": {"value": 12000},
                    "output_tokens": {"value": 30000},
                },
                "cli_tokens": {
                    "cli_input_tokens": {"value": 0},  # No CLI usage
                    "cli_cached_input_tokens": {"value": 0},  # No CLI usage
                    "cli_output_tokens": {"value": 0},  # No CLI usage
                },
                "total_money_spent": {"sum": {"value": 45.75}},
                "unique_assistants": {"count": {"value": 10}},
                "unique_workflows": {"count": {"value": 5}},
                "embedding_metrics": {
                    "input_tokens": {"value": 1000},
                    "money_spent": {"value": 0.10},
                },
                "llm_cost": {
                    "money_spent": {"value": 45.65},
                },
                "cli_cost": {
                    "money_spent": {"value": 0.0},  # No CLI usage
                },
            }
        }

        # Act
        metrics = handler._build_summaries_metrics(result, unique_users_count=3)

        # Assert
        assert len(metrics) == 11

        # Verify values match web-only LLM usage
        input_metric = next(m for m in metrics if m["id"] == "total_input_tokens")
        assert input_metric["value"] == 60000  # Only web LLM tokens

        cached_metric = next(m for m in metrics if m["id"] == "total_cached_input_tokens")
        assert cached_metric["value"] == 12000  # Only web LLM tokens

        output_metric = next(m for m in metrics if m["id"] == "total_output_tokens")
        assert output_metric["value"] == 30000  # Only web LLM tokens

        # Verify embedding tokens
        embedding_tokens_metric = next(m for m in metrics if m["id"] == "embedding_input_tokens")
        assert embedding_tokens_metric["value"] == 1000

        # Verify unique counts
        unique_users_metric = next(m for m in metrics if m["id"] == "unique_active_users")
        assert unique_users_metric["value"] == 3

        unique_assistants_metric = next(m for m in metrics if m["id"] == "unique_assistants_invoked")
        assert unique_assistants_metric["value"] == 10

        unique_workflows_metric = next(m for m in metrics if m["id"] == "unique_workflows_invoked")
        assert unique_workflows_metric["value"] == 5


class TestPlatformCostCalculation:
    """Tests for platform_cost metric calculation."""

    @pytest.mark.skip(reason="Cost metrics are hidden (EPMCDME-10598)")
    def test_platform_cost_calculation_basic(self, handler):
        """Verify platform_cost = llm_cost - cli_cost."""
        # Arrange
        result = {
            "aggregations": {
                "web_llm_tokens": {
                    "input_tokens": {"value": 50000},
                    "cached_input_tokens": {"value": 10000},
                    "output_tokens": {"value": 25000},
                },
                "cli_input_tokens": {"value": 10000},
                "cli_cached_input_tokens": {"value": 2000},
                "cli_output_tokens": {"value": 5000},
                "total_money_spent": {"value": 100.0},
                "unique_assistants": {"count": {"value": 10}},
                "unique_workflows": {"count": {"value": 5}},
                "embedding_metrics": {
                    "input_tokens": {"value": 1000},
                    "money_spent": {"value": 0.50},
                },
                "llm_cost": {
                    "money_spent": {"value": 99.50},
                },
                "cli_cost": {
                    "money_spent": {"value": 29.50},
                },
            }
        }

        # Act
        metrics = handler._build_summaries_metrics(result, unique_users_count=5)

        # Assert
        platform_cost_metric = next(m for m in metrics if m["id"] == "platform_cost")
        cli_cost_metric = next(m for m in metrics if m["id"] == "cli_cost")

        # platform_cost = llm_cost - cli_cost = 99.50 - 29.50 = 70.00
        assert platform_cost_metric["value"] == 70.0
        assert cli_cost_metric["value"] == 29.50

    @pytest.mark.skip(reason="Cost metrics are hidden (EPMCDME-10598)")
    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.cli_cost_processor.config.CLI_METRICS_CUTOFF_DATE", "2024-01-01")
    async def test_platform_cost_with_cli_adjustment(self, handler, mock_repository):
        """Verify CLI adjustment affects platform_cost correctly."""
        # Arrange - Simulate CLI cost reduction due to cutoff date
        mock_repository.execute_aggregation_query.side_effect = [
            # First call: unique_users query
            {"aggregations": {"unique_users": {"value": 5}}},
            # Second call: main query (original costs before adjustment)
            {
                "aggregations": {
                    "web_llm_tokens": {
                        "input_tokens": {"value": 50000},
                        "cached_input_tokens": {"value": 10000},
                        "output_tokens": {"value": 25000},
                    },
                    "cli_input_tokens": {"value": 10000},
                    "cli_cached_input_tokens": {"value": 2000},
                    "cli_output_tokens": {"value": 5000},
                    "total_money_spent": {"value": 100.0},
                    "unique_assistants": {"count": {"value": 10}},
                    "unique_workflows": {"count": {"value": 5}},
                    "embedding_metrics": {
                        "input_tokens": {"value": 1000},
                        "money_spent": {"value": 0.50},
                    },
                    "llm_cost": {
                        "money_spent": {"value": 99.50},  # Original: platform + CLI
                    },
                    "cli_cost": {
                        "money_spent": {"value": 30.0},  # Original CLI cost (inflated)
                    },
                }
            },
            # Third call: CLI costs query with adjusted dates (reduced cost)
            {
                "aggregations": {
                    "total_cost": {"value": 10.0},  # Adjusted CLI cost (only after cutoff)
                }
            },
        ]

        # Act
        result = await handler.get_summaries(time_period="last_30_days")

        # Assert
        metrics = result["data"]["metrics"]
        platform_cost_metric = next(m for m in metrics if m["id"] == "platform_cost")
        cli_cost_metric = next(m for m in metrics if m["id"] == "cli_cost")
        total_money_metric = next(m for m in metrics if m["id"] == "total_money_spent")
        embedding_cost_metric = next(m for m in metrics if m["id"] == "embedding_cost")

        # Original values from Query 1:
        # - total_money_spent = 100.0
        # - embedding_cost = 0.50
        # - llm_cost (calculated) = 100.0 - 0.50 = 99.50
        # - cli_cost (inflated) = 30.0
        # - platform_cost (calculated) = 99.50 - 30.0 = 69.50
        #
        # After CLI adjustment (Query 2):
        # - cli_cost_adjusted = 10.0
        # - cli_cost_adjustment = 10.0 - 30.0 = -20.0
        # - adjusted_llm_cost = 99.50 + (-20.0) = 79.50
        # - adjusted_platform_cost = 79.50 - 10.0 = 69.50 (stays the same!)
        # - adjusted_total_money_spent = 100.0 + (-20.0) = 80.0
        assert embedding_cost_metric["value"] == 0.50
        assert cli_cost_metric["value"] == 10.0
        assert platform_cost_metric["value"] == 69.50
        assert total_money_metric["value"] == 80.0

        # Validation: total = platform + cli + embedding
        calculated_total = platform_cost_metric["value"] + cli_cost_metric["value"] + embedding_cost_metric["value"]
        assert abs(calculated_total - total_money_metric["value"]) <= 0.01

    @pytest.mark.skip(reason="Cost metrics are hidden (EPMCDME-10598)")
    def test_platform_cost_zero_cli(self, handler):
        """Verify platform_cost = llm_cost when cli_cost = 0."""
        # Arrange - Web-only usage scenario
        result = {
            "aggregations": {
                "web_llm_tokens": {
                    "input_tokens": {"value": 50000},
                    "cached_input_tokens": {"value": 10000},
                    "output_tokens": {"value": 25000},
                },
                "cli_input_tokens": {"value": 0},
                "cli_cached_input_tokens": {"value": 0},
                "cli_output_tokens": {"value": 0},
                "total_money_spent": {"value": 50.0},
                "unique_assistants": {"count": {"value": 10}},
                "unique_workflows": {"count": {"value": 5}},
                "embedding_metrics": {
                    "input_tokens": {"value": 500},
                    "money_spent": {"value": 0.25},
                },
                "llm_cost": {
                    "money_spent": {"value": 49.75},
                },
                "cli_cost": {
                    "money_spent": {"value": 0.0},
                },
            }
        }

        # Act
        metrics = handler._build_summaries_metrics(result, unique_users_count=3)

        # Assert
        platform_cost_metric = next(m for m in metrics if m["id"] == "platform_cost")

        # When CLI cost is 0, platform_cost should equal total LLM cost
        assert platform_cost_metric["value"] == 49.75

    @pytest.mark.skip(reason="Cost metrics are hidden (EPMCDME-10598)")
    def test_spending_breakdown_calculation(self, handler):
        """Verify platform_cost + cli_cost + embedding_cost equals total_money_spent."""
        # Arrange
        result = {
            "aggregations": {
                "web_llm_tokens": {
                    "input_tokens": {"value": 50000},
                    "cached_input_tokens": {"value": 10000},
                    "output_tokens": {"value": 25000},
                },
                "cli_input_tokens": {"value": 10000},
                "cli_cached_input_tokens": {"value": 2000},
                "cli_output_tokens": {"value": 5000},
                "total_money_spent": {"value": 100.0},
                "unique_assistants": {"count": {"value": 10}},
                "unique_workflows": {"count": {"value": 5}},
                "embedding_metrics": {
                    "input_tokens": {"value": 1000},
                    "money_spent": {"value": 0.50},
                },
                "llm_cost": {
                    "money_spent": {"value": 99.50},
                },
                "cli_cost": {
                    "money_spent": {"value": 29.50},
                },
            }
        }

        # Act
        metrics = handler._build_summaries_metrics(result, unique_users_count=5)

        # Assert
        platform_cost = next(m for m in metrics if m["id"] == "platform_cost")["value"]
        cli_cost = next(m for m in metrics if m["id"] == "cli_cost")["value"]
        embedding_cost = next(m for m in metrics if m["id"] == "embedding_cost")["value"]
        total_money = next(m for m in metrics if m["id"] == "total_money_spent")["value"]

        # Verify spending breakdown: platform_cost = llm_cost - cli_cost = 99.50 - 29.50 = 70.00
        assert platform_cost == 70.0
        assert cli_cost == 29.50
        assert embedding_cost == 0.50
        assert total_money == 100.0

        # Validation: total = platform + cli + embedding
        calculated_total = platform_cost + cli_cost + embedding_cost
        assert abs(calculated_total - total_money) <= 0.01

    @pytest.mark.skip(reason="Cost metrics are hidden (EPMCDME-10598)")
    def test_metrics_count_is_11(self, handler):
        """Verify response contains exactly 11 metrics."""
        # Arrange
        result = {
            "aggregations": {
                "web_llm_tokens": {
                    "input_tokens": {"value": 1000},
                    "cached_input_tokens": {"value": 100},
                    "output_tokens": {"value": 500},
                },
                "cli_input_tokens": {"value": 200},
                "cli_cached_input_tokens": {"value": 50},
                "cli_output_tokens": {"value": 100},
                "total_money_spent": {"value": 10.0},
                "unique_assistants": {"count": {"value": 5}},
                "unique_workflows": {"count": {"value": 3}},
                "embedding_metrics": {
                    "input_tokens": {"value": 100},
                    "money_spent": {"value": 0.10},
                },
                "llm_cost": {
                    "money_spent": {"value": 9.90},
                },
                "cli_cost": {
                    "money_spent": {"value": 2.0},
                },
            }
        }

        # Act
        metrics = handler._build_summaries_metrics(result, unique_users_count=2)

        # Assert
        assert len(metrics) == 11
        metric_ids = [m["id"] for m in metrics]
        assert "platform_cost" in metric_ids
        assert "total_money_spent" in metric_ids

    @pytest.mark.skip(reason="Cost metrics are hidden (EPMCDME-10598)")
    def test_platform_cost_metric_structure(self, handler):
        """Verify platform_cost has correct metadata."""
        # Arrange
        result = {
            "aggregations": {
                "web_llm_tokens": {
                    "input_tokens": {"value": 1000},
                    "cached_input_tokens": {"value": 100},
                    "output_tokens": {"value": 500},
                },
                "cli_input_tokens": {"value": 0},
                "cli_cached_input_tokens": {"value": 0},
                "cli_output_tokens": {"value": 0},
                "total_money_spent": {"value": 10.0},
                "unique_assistants": {"count": {"value": 1}},
                "unique_workflows": {"count": {"value": 1}},
                "embedding_metrics": {
                    "input_tokens": {"value": 100},
                    "money_spent": {"value": 0.10},
                },
                "llm_cost": {
                    "money_spent": {"value": 9.90},
                },
                "cli_cost": {
                    "money_spent": {"value": 0.0},
                },
            }
        }

        # Act
        metrics = handler._build_summaries_metrics(result, unique_users_count=1)

        # Assert
        platform_cost_metric = next(m for m in metrics if m["id"] == "platform_cost")
        assert platform_cost_metric["id"] == "platform_cost"
        assert platform_cost_metric["label"] == "Platform LLM Cost"
        assert platform_cost_metric["type"] == "number"
        assert platform_cost_metric["format"] == "currency"
        assert "description" in platform_cost_metric
        assert "excluding CLI" in platform_cost_metric["description"]

    @pytest.mark.skip(reason="Cost metrics are hidden (EPMCDME-10598)")
    def test_platform_cost_not_negative_when_embedding_is_zero(self, handler):
        """Regression: platform_cost must not be negative when embedding_cost=0.0.

        Production case from screenshot: total=$0.41, cli=$0.37, embedding=$0.00.
        Old code computed `llm_cost_agg - cli_cost` which could go negative when
        the llm_cost ES aggregation returned 0 or less than cli_cost.
        """
        # Arrange — exact values observed in production
        result = {
            "aggregations": {
                "web_llm_tokens": {
                    "input_tokens": {"value": 0},
                    "cached_input_tokens": {"value": 0},
                    "output_tokens": {"value": 0},
                },
                "cli_input_tokens": {"value": 1000},
                "cli_cached_input_tokens": {"value": 0},
                "cli_output_tokens": {"value": 500},
                "total_money_spent": {"value": 0.41},
                "unique_assistants": {"count": {"value": 1}},
                "unique_workflows": {"count": {"value": 0}},
                "embedding_metrics": {
                    "input_tokens": {"value": 0},
                    "money_spent": {"value": 0.0},  # <-- falsy value that triggered the bug
                },
                "llm_cost": {
                    "money_spent": {"value": 0.0},  # ES agg returned 0
                },
                "cli_cost": {
                    "money_spent": {"value": 0.37},
                },
            }
        }

        # Act
        metrics = handler._build_summaries_metrics(result, unique_users_count=1)

        # Assert
        platform_cost_metric = next(m for m in metrics if m["id"] == "platform_cost")
        assert platform_cost_metric["value"] >= 0, "platform_cost must never be negative"
        # platform_cost = max(0, total(0.41) - cli(0.37) - embedding(0.0)) = 0.04
        assert platform_cost_metric["value"] == pytest.approx(0.04, abs=0.01)

    @pytest.mark.skip(reason="Cost metrics are hidden (EPMCDME-10598)")
    @pytest.mark.asyncio
    @patch("codemie.service.analytics.handlers.cli_cost_processor.config.CLI_METRICS_CUTOFF_DATE", "2024-01-01")
    async def test_platform_cost_adjustment_not_negative_when_embedding_is_zero(self, handler, mock_repository):
        """Regression: platform_cost must not be negative after CLI adjustment when embedding_cost=0.0.

        Old code: `if total_money_original and embedding_cost_value` treated 0.0 as falsy,
        collapsing original_llm_cost to 0, which made platform_cost = -cli_cost_original.
        """
        # Arrange — embedding=0.0 throughout; all spend is CLI
        mock_repository.execute_aggregation_query.side_effect = [
            # First call: unique_users query
            {"aggregations": {"unique_users": {"value": 1}}},
            # Second call: main query — all spending is CLI, no embeddings
            {
                "aggregations": {
                    "web_llm_tokens": {
                        "input_tokens": {"value": 0},
                        "cached_input_tokens": {"value": 0},
                        "output_tokens": {"value": 0},
                    },
                    "cli_input_tokens": {"value": 1000},
                    "cli_cached_input_tokens": {"value": 0},
                    "cli_output_tokens": {"value": 500},
                    "total_money_spent": {"value": 0.41},
                    "unique_assistants": {"count": {"value": 1}},
                    "unique_workflows": {"count": {"value": 0}},
                    "embedding_metrics": {
                        "input_tokens": {"value": 0},
                        "money_spent": {"value": 0.0},  # <-- falsy, triggers the bug
                    },
                    "llm_cost": {"money_spent": {"value": 0.41}},
                    "cli_cost": {"money_spent": {"value": 0.41}},  # inflated (pre-cutoff)
                }
            },
            # Third call: adjusted CLI cost (cutoff applied)
            {
                "aggregations": {
                    "total_cost": {"value": 0.37},  # after cutoff: $0.41 → $0.37
                }
            },
        ]

        # Act
        result = await handler.get_summaries(time_period="last_30_days")

        # Assert
        metrics = result["data"]["metrics"]
        platform_cost_metric = next(m for m in metrics if m["id"] == "platform_cost")
        cli_cost_metric = next(m for m in metrics if m["id"] == "cli_cost")
        total_money_metric = next(m for m in metrics if m["id"] == "total_money_spent")
        embedding_cost_metric = next(m for m in metrics if m["id"] == "embedding_cost")

        assert platform_cost_metric["value"] >= 0, "platform_cost must never be negative"
        assert cli_cost_metric["value"] == 0.37
        assert embedding_cost_metric["value"] == 0.0
        # total adjusted = 0.41 + (0.37 - 0.41) = 0.37
        assert total_money_metric["value"] == pytest.approx(0.37, abs=0.01)
        # platform = total_adjusted - cli_adjusted - embedding = 0.37 - 0.37 - 0.0 = 0.0
        assert platform_cost_metric["value"] == pytest.approx(0.0, abs=0.01)

        # Validation: breakdown sums to total
        calculated_total = platform_cost_metric["value"] + cli_cost_metric["value"] + embedding_cost_metric["value"]
        assert abs(calculated_total - total_money_metric["value"]) <= 0.01
