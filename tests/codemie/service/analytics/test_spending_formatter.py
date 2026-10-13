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

"""Characterization tests for the spending formatters: metric builders, time until reset and tabular pagination."""

import logging
from datetime import datetime, timedelta, timezone
from typing import get_args, get_type_hints
from unittest.mock import patch

import pytest

from codemie.service.analytics import spending_formatter
from codemie.service.analytics.response_formatter import ResponseFormatter

# 16 hours: with a 4-hour fixture ``% 86400`` -> ``% 43200`` would be an equivalent mutation. The 30 seconds of slack
# keep the minute term stable while the test runs; the clock is not patched.
RESET_DELTA = timedelta(days=3, hours=16, minutes=30, seconds=30)
RESET_TEXT = "3 days 16 hours 30 mins"
EXPIRED_TEXT = "0 days 0 hours 0 mins"

METRIC_TEXT = {
    "current_spending": ("Current Spending ($)", "Total amount spent in current budget period"),
    "budget_limit": ("Budget Limit ($)", "Soft budget limit (warning threshold)"),
    "budget_reset_at": ("Budget Reset Date", "Timestamp when budget will reset"),
    "time_until_reset": ("Time Until Reset", "Time remaining until budget resets"),
}


def _metric(metric_id: str, value, value_type: str, format_type: str | None = None) -> dict:
    label, description = METRIC_TEXT[metric_id]
    metric = {"id": metric_id, "label": label, "type": value_type, "value": value, "description": description}
    if format_type:
        metric["format"] = format_type
    return metric


def _future_reset(delta: timedelta = RESET_DELTA) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat()


@pytest.fixture
def logged(caplog, monkeypatch):
    """The module logger's records as (level name, message) pairs.

    The ``codemie`` logger does not propagate, so the root-level caplog handler can miss its records: the handler is
    attached to the module logger itself, which stops propagating for the test so no record is handled twice.
    """
    logger = spending_formatter.logger
    monkeypatch.setattr(logger, "propagate", False)
    logger.addHandler(caplog.handler)
    caplog.set_level(logging.DEBUG, logger=logger.name)
    yield lambda: [(record.levelname, record.getMessage()) for record in caplog.records if record.name == logger.name]
    logger.removeHandler(caplog.handler)


class TestBuildSpendingMetric:
    @pytest.mark.parametrize(
        ("metric_id", "label", "description"),
        [
            pytest.param("current_spending", "Current Spending ($)", "Total amount spent in current budget period"),
            pytest.param("budget_limit", "Budget Limit ($)", "Soft budget limit (warning threshold)"),
            pytest.param("budget_reset_at", "Budget Reset Date", "Timestamp when budget will reset"),
            pytest.param("time_until_reset", "Time Until Reset", "Time remaining until budget resets"),
        ],
    )
    def test_known_ids_carry_their_label_and_description(self, metric_id, label, description):
        metric = spending_formatter.build_spending_metric(metric_id, 7, "number", "currency")

        assert metric == {
            "id": metric_id,
            "label": label,
            "type": "number",
            "value": 7,
            "description": description,
            "format": "currency",
        }

    @pytest.mark.parametrize("metric_id", ["cli_current_spending", "premium_current_spending"])
    def test_unknown_id_falls_back_to_the_id_as_label_pinned_quirk(self, metric_id):
        metric = spending_formatter.build_spending_metric(metric_id, 1.5, "number", "currency")

        assert metric == {
            "id": metric_id,
            "label": metric_id,
            "type": "number",
            "value": 1.5,
            "description": "",
            "format": "currency",
        }

    @pytest.mark.parametrize("format_type", [None, ""])
    def test_format_key_is_absent_unless_the_format_is_truthy(self, format_type):
        metric = spending_formatter.build_spending_metric("budget_limit", 100.0, "number", format_type)

        assert "format" not in metric

    def test_format_key_is_present_for_a_truthy_format(self):
        metric = spending_formatter.build_spending_metric("budget_reset_at", "x", "date", "timestamp")

        assert metric["format"] == "timestamp"

    def test_type_defaults_to_string(self):
        assert spending_formatter.build_spending_metric("time_until_reset", "N/A")["type"] == "string"

    def test_the_given_type_is_kept(self):
        assert spending_formatter.build_spending_metric("current_spending", 1, "number")["type"] == "number"


class TestBuildSpendingMetrics:
    UNAVAILABLE = [
        _metric("current_spending", 0.0, "number", "currency"),
        _metric("budget_limit", "N/A", "string"),
        _metric("budget_reset_at", "N/A", "string"),
        _metric("time_until_reset", "N/A", "string"),
    ]

    def test_none_data_is_unavailable(self, logged):
        metrics = spending_formatter.build_spending_metrics(None, "u1")

        assert metrics == self.UNAVAILABLE
        assert logged() == [("INFO", "Budget tracking not available for user u1")]

    def test_empty_dict_is_unavailable_pinned_quirk(self, logged):
        metrics = spending_formatter.build_spending_metrics({}, "u1")

        assert metrics == self.UNAVAILABLE
        assert logged() == [("INFO", "Budget tracking not available for user u1")]

    def test_data_with_a_future_reset_fills_all_four_metrics(self, logged):
        reset = _future_reset()

        metrics = spending_formatter.build_spending_metrics(
            {"total_spend": 15.5, "max_budget": 100.0, "budget_reset_at": reset}, "u1"
        )

        assert metrics == [
            _metric("current_spending", 15.5, "number", "currency"),
            _metric("budget_limit", 100.0, "number", "currency"),
            _metric("budget_reset_at", reset, "date", "timestamp"),
            _metric("time_until_reset", RESET_TEXT, "string"),
        ]
        assert logged() == [
            (
                "INFO",
                f"Spending analytics retrieved for user u1: spend=$15.50, budget=100.0, time_until_reset={RESET_TEXT}",
            )
        ]

    def test_missing_budget_limit_stays_a_number_metric_holding_none(self):
        metrics = spending_formatter.build_spending_metrics(
            {"total_spend": 1.0, "max_budget": None, "budget_reset_at": _future_reset()}, "u1"
        )

        assert metrics[1] == _metric("budget_limit", None, "number", "currency")

    def test_absent_budget_limit_key_also_holds_none(self):
        metrics = spending_formatter.build_spending_metrics({"total_spend": 1.0}, "u1")

        assert metrics[1] == _metric("budget_limit", None, "number", "currency")

    def test_missing_total_spend_counts_as_zero(self):
        metrics = spending_formatter.build_spending_metrics({"max_budget": 100.0}, "u1")

        assert metrics[0] == _metric("current_spending", 0.0, "number", "currency")

    @pytest.mark.parametrize("reset", [None, ""])
    def test_no_reset_date_gives_string_metrics_without_a_format(self, reset, logged):
        metrics = spending_formatter.build_spending_metrics(
            {"total_spend": 15.5, "max_budget": 100.0, "budget_reset_at": reset}, "u1"
        )

        assert metrics == [
            _metric("current_spending", 15.5, "number", "currency"),
            _metric("budget_limit", 100.0, "number", "currency"),
            _metric("budget_reset_at", "N/A", "string"),
            _metric("time_until_reset", "N/A", "string"),
        ]
        assert "format" not in metrics[2]
        assert logged() == [
            ("INFO", "Spending analytics retrieved for user u1: spend=$15.50, budget=100.0, time_until_reset=N/A")
        ]

    def test_an_unparsable_reset_date_keeps_the_date_metric_and_has_no_time_until_reset(self, logged):
        metrics = spending_formatter.build_spending_metrics(
            {"total_spend": 1.0, "max_budget": 100.0, "budget_reset_at": "not-a-date"}, "u1"
        )

        assert metrics[2] == _metric("budget_reset_at", "not-a-date", "date", "timestamp")
        assert metrics[3] == _metric("time_until_reset", None, "string")
        assert [level for level, _ in logged()] == ["WARNING", "INFO"]


class TestCalculateTimeUntilReset:
    def test_time_until_reset_formats_days_hours_and_minutes(self):
        assert spending_formatter.calculate_time_until_reset(_future_reset()) == RESET_TEXT

    def test_z_and_explicit_utc_offset_spellings_are_equal(self):
        moment = datetime.now(timezone.utc) + RESET_DELTA
        with_offset = moment.isoformat()
        assert with_offset.endswith("+00:00")
        with_z = with_offset.replace("+00:00", "Z")

        assert spending_formatter.calculate_time_until_reset(with_offset) == RESET_TEXT
        assert spending_formatter.calculate_time_until_reset(with_z) == RESET_TEXT

    @pytest.mark.parametrize("age", [timedelta(hours=1), timedelta(days=400)])
    def test_time_until_reset_for_a_past_date_is_the_expired_text_pinned_quirk(self, age):
        past = (datetime.now(timezone.utc) - age).isoformat()

        assert spending_formatter.calculate_time_until_reset(past) == EXPIRED_TEXT

    @pytest.mark.parametrize("missing", [None, ""])
    def test_missing_reset_date_gives_none_without_a_warning(self, missing, logged):
        assert spending_formatter.calculate_time_until_reset(missing) is None
        assert logged() == []

    @pytest.mark.parametrize("invalid", ["not-a-date", 12345])
    def test_invalid_reset_date_gives_none_and_a_warning(self, invalid, logged):
        assert spending_formatter.calculate_time_until_reset(invalid) is None

        [(level, message)] = logged()
        assert level == "WARNING"
        assert message.startswith(f"Failed to parse budget_reset_at: {invalid}, error: ")

    def test_time_until_reset_naive_timestamp_raises_pinned_quirk(self):
        naive = (datetime.now() + RESET_DELTA).replace(tzinfo=None).isoformat()

        with pytest.raises(TypeError):
            spending_formatter.calculate_time_until_reset(naive)


class TestPaginateTabular:
    COLUMNS = [{"id": "project_name"}]
    ROWS = [{"project_name": f"p-{index}"} for index in range(5)]

    def _paginate(self, formatter, page, per_page=2, filters=None, start_time=None):
        filters = {"projects": "atlas-core"} if filters is None else filters
        start_time = datetime.now(timezone.utc) if start_time is None else start_time
        result = spending_formatter.paginate_tabular(
            columns=self.COLUMNS,
            rows=self.ROWS,
            filters_applied=filters,
            start_time=start_time,
            page=page,
            per_page=per_page,
        )
        return result, formatter.call_args.kwargs

    @pytest.mark.parametrize(
        ("page", "expected"),
        [
            pytest.param(0, [0, 1], id="first"),
            pytest.param(1, [2, 3], id="second"),
            pytest.param(2, [4], id="last-partial"),
            pytest.param(3, [], id="beyond-the-end"),
        ],
    )
    def test_slices_the_requested_page(self, page, expected):
        with patch.object(ResponseFormatter, "format_tabular_response") as formatter:
            _, kwargs = self._paginate(formatter, page)

        assert kwargs["rows"] == [self.ROWS[index] for index in expected]

    def test_passes_the_unsliced_total_and_the_request_through_to_the_formatter(self):
        filters = {"projects": "atlas-core"}
        with patch.object(ResponseFormatter, "format_tabular_response") as formatter:
            _, kwargs = self._paginate(formatter, 1, filters=filters)

        execution_time_ms = kwargs.pop("execution_time_ms")
        assert execution_time_ms >= 0
        assert kwargs == {
            "columns": self.COLUMNS,
            "rows": self.ROWS[2:4],
            "filters_applied": filters,
            "totals": None,
            "page": 1,
            "per_page": 2,
            "total_count": 5,
        }
        assert kwargs["filters_applied"] is filters
        assert kwargs["columns"] is self.COLUMNS

    def test_returns_the_formatter_result(self):
        with patch.object(ResponseFormatter, "format_tabular_response") as formatter:
            result, _ = self._paginate(formatter, 0)

        assert result is formatter.return_value

    def test_execution_time_is_reported_in_milliseconds(self):
        start_time = datetime.now(timezone.utc) - timedelta(seconds=2)
        with patch.object(ResponseFormatter, "format_tabular_response") as formatter:
            _, kwargs = self._paginate(formatter, 0, start_time=start_time)

        assert 2000 <= kwargs["execution_time_ms"] < 4000


def _contains_bare_dict(hint) -> bool:
    return hint is dict or any(_contains_bare_dict(argument) for argument in get_args(hint))


class TestPublicSignatures:
    @pytest.mark.parametrize(
        "function",
        [
            spending_formatter.build_spending_metric,
            spending_formatter.build_spending_metrics,
            spending_formatter.paginate_tabular,
        ],
    )
    def test_dict_annotations_are_parameterized(self, function):
        bare = [name for name, hint in get_type_hints(function).items() if _contains_bare_dict(hint)]

        assert bare == []
