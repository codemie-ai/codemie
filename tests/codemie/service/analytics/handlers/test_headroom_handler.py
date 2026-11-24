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

"""Unit tests for HeadroomHandler."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.headroom_handler import HeadroomHandler
from codemie.service.analytics.metric_names import MetricName


@pytest.fixture
def handler():
    user = MagicMock(spec=User)
    user.project_names = []
    user.admin_project_names = []
    user.is_global_user = False
    user.is_admin = False
    user.id = "test-user-id"
    return HeadroomHandler(user, MagicMock(spec=MetricsElasticRepository))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "pipeline_method"),
    [
        ("get_headroom_summary", "execute_summary_query"),
        ("get_savings_by_strategy", "execute_tabular_query"),
        ("get_savings_timeseries", "execute_composite_query"),
    ],
)
async def test_queries_only_headroom_compaction_events(handler, method, pipeline_method):
    setattr(handler._pipeline, pipeline_method, AsyncMock(return_value={"data": {}, "metadata": {}}))

    await getattr(handler, method)()

    filters = getattr(handler._pipeline, pipeline_method).call_args.kwargs["filters"]
    assert filters.metric_filters == [MetricName.HEADROOM_COMPACTION_USAGE.value]


def test_summary_aggregation_sums_the_three_token_fields(handler):
    agg = handler._build_summary_agg({"bool": {}})

    assert set(agg["aggs"]) == {
        "session_count",
        "request_count",
        "modes",
        "tokens_original",
        "tokens_compacted",
        "tokens_saved",
    }
    assert agg["aggs"]["tokens_saved"] == {"sum": {"field": "attributes.tokens_saved"}}


def test_summary_aggregation_collects_distinct_modes_with_a_small_terms_aggregation(handler):
    terms = handler._build_summary_agg({"bool": {}})["aggs"]["modes"]["terms"]

    assert terms["field"] == "attributes.mode.keyword"
    assert terms["size"] <= 5
    assert terms["missing"] == "mixed"


def _summary_result(original=2000.0, compacted=1500.0, saved=500.0, modes=("sidecar",)):
    return {
        "aggregations": {
            "tokens_original": {"value": original},
            "tokens_compacted": {"value": compacted},
            "tokens_saved": {"value": saved},
            "request_count": {"value": 4},
            "session_count": {"value": 2},
            "modes": {"buckets": [{"key": mode, "doc_count": 1} for mode in modes]},
        }
    }


def _metrics(handler, result):
    return {m["id"]: m["value"] for m in handler._parse_summary(result)}


def test_summary_parse_computes_totals_share_rate_and_mode(handler):
    assert _metrics(handler, _summary_result()) == {
        "tokens_original": 2000,
        "tokens_compacted": 1500,
        "tokens_saved": 500,
        "saved_percent": 25.0,
        "request_count": 4,
        "session_count": 2,
        "mode": "sidecar",
    }


def test_summary_saved_percent_is_a_percentage_rounded_to_two_decimals(handler):
    metrics = handler._parse_summary(_summary_result(original=3.0, compacted=2.0, saved=1.0))
    rate = next(m for m in metrics if m["id"] == "saved_percent")

    assert rate["value"] == 33.33
    assert [m["id"] for m in metrics].count("saved_percent") == 1
    assert "savings_rate_percent" not in {m["id"] for m in metrics}
    assert rate["type"] == "number"
    assert rate["format"] == "percentage"


def test_summary_saved_percent_is_zero_when_nothing_was_original(handler):
    result = _summary_result(original=0.0, compacted=0.0, saved=0.0, modes=())

    assert _metrics(handler, result)["saved_percent"] == 0.0


@pytest.mark.parametrize("mode", ["sdk", "sidecar"])
def test_summary_mode_is_the_single_mode_seen_in_the_period(handler, mode):
    assert _metrics(handler, _summary_result(modes=(mode,)))["mode"] == mode


def test_summary_mode_is_mixed_when_several_modes_appear(handler):
    assert _metrics(handler, _summary_result(modes=("sdk", "sidecar")))["mode"] == "mixed"


def test_summary_mode_keeps_the_mixed_label_of_merged_events(handler):
    assert _metrics(handler, _summary_result(modes=("mixed",)))["mode"] == "mixed"


def test_summary_mode_is_omitted_without_events(handler):
    assert "mode" not in _metrics(handler, _summary_result(modes=()))


def test_summary_parse_of_empty_result_is_all_zero_without_mode(handler):
    metrics = handler._parse_summary({})

    assert {m["value"] for m in metrics} == {0, 0.0}
    assert "mode" not in {m["id"] for m in metrics}


@pytest.mark.asyncio
async def test_strategy_savings_group_by_the_strategy_keyword(handler):
    handler._pipeline.execute_tabular_query = AsyncMock(return_value={})

    await handler.get_savings_by_strategy()

    kwargs = handler._pipeline.execute_tabular_query.call_args.kwargs
    assert kwargs["group_by_field"] == "attributes.strategy.keyword"
    assert kwargs["agg_builder"]({"q": 1}, 20)["aggs"]["paginated_results"]["terms"]["field"] == (
        "attributes.strategy.keyword"
    )


@pytest.mark.asyncio
async def test_strategy_savings_columns_include_the_saved_percent_after_tokens_saved(handler):
    handler._pipeline.execute_tabular_query = AsyncMock(return_value={})

    await handler.get_savings_by_strategy()

    columns = handler._pipeline.execute_tabular_query.call_args.kwargs["columns"]
    assert [c["id"] for c in columns] == [
        "strategy",
        "request_count",
        "tokens_original",
        "tokens_compacted",
        "tokens_saved",
        "saved_percent",
    ]
    rate = columns[-1]
    assert rate["type"] == "number"
    assert rate["format"] == "percentage"


def _bucket(key, doc_count, original, compacted, saved):
    return {
        "key": key,
        "doc_count": doc_count,
        "tokens_original": {"value": original},
        "tokens_compacted": {"value": compacted},
        "tokens_saved": {"value": saved},
    }


def test_terms_parse_returns_one_row_per_bucket_with_its_saved_percent(handler):
    result = {"aggregations": {"paginated_results": {"buckets": [_bucket("aggressive", 3, 900.0, 300.0, 600.0)]}}}

    assert handler._parse_terms_result(result) == [
        {
            "strategy": "aggressive",
            "request_count": 3,
            "tokens_original": 900,
            "tokens_compacted": 300,
            "tokens_saved": 600,
            "saved_percent": 66.67,
        }
    ]


def test_terms_parse_keeps_the_mixed_bucket_and_zero_original_ratio(handler):
    result = {
        "aggregations": {
            "paginated_results": {
                "buckets": [_bucket("mixed", 2, 400.0, 300.0, 100.0), _bucket("lossless", 1, 0.0, 0.0, 0.0)]
            }
        }
    }

    rows = handler._parse_terms_result(result)

    assert [(r["strategy"], r["saved_percent"]) for r in rows] == [("mixed", 25.0), ("lossless", 0.0)]


def test_by_mode_handler_method_is_gone(handler):
    assert not hasattr(handler, "get_savings_by_mode")


def test_timeseries_parse_uses_bucket_time_and_sums(handler):
    result = {
        "aggregations": {
            "time_buckets": {
                "buckets": [
                    {
                        "key_as_string": "2026-10-15T00:00:00.000Z",
                        "doc_count": 2,
                        "tokens_original": {"value": 100.0},
                        "tokens_compacted": {"value": 60.0},
                        "tokens_saved": {"value": 40.0},
                    }
                ]
            }
        }
    }

    assert handler._parse_timeseries(result) == [
        {
            "time": "2026-10-15T00:00:00.000Z",
            "request_count": 2,
            "tokens_original": 100,
            "tokens_compacted": 60,
            "tokens_saved": 40,
        }
    ]


def test_terms_aggregation_keeps_events_without_a_label_under_an_explicit_mixed_bucket(handler):
    terms = handler._build_terms_agg({"q": 1}, 20)["aggs"]["paginated_results"]["terms"]

    assert terms["missing"] == "mixed"


@pytest.mark.asyncio
async def test_strategy_totals_sum_the_token_counts_but_never_the_percentage_column(handler):
    from codemie.service.analytics.totals_calculator import TotalsCalculator

    handler._pipeline.execute_tabular_query = AsyncMock(return_value={})
    await handler.get_savings_by_strategy()
    columns = handler._pipeline.execute_tabular_query.call_args.kwargs["columns"]
    percent_column = next(c for c in columns if c.get("format") == "percentage")
    rows = [
        {"strategy": "aggressive", "request_count": 2, "tokens_original": 3844, "tokens_saved": 1497},
        {"strategy": "balanced", "request_count": 2, "tokens_original": 3844, "tokens_saved": 1435},
    ]
    for row, share in zip(rows, (38.94, 37.33)):
        row[percent_column["id"]] = share

    totals = TotalsCalculator.calculate_totals(columns=columns, rows=rows)

    assert totals["request_count"] == 4
    assert totals["tokens_saved"] == 2932
    assert percent_column["id"] not in totals
