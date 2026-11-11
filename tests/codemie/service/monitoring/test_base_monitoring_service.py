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

from unittest.mock import patch, MagicMock

import pytest

from codemie.configs.logger import logging_user_id
from codemie.service.monitoring.base_monitoring_service import (
    DURATION_BUCKET_BOUNDARIES_SECONDS,
    BaseMonitoringService,
    emit_llm_token_metric,
    send_log_metric,
)
from codemie.service.monitoring.metrics_constants import MetricsAttributes


@pytest.fixture
def mock_meter():
    with patch("codemie.service.monitoring.base_monitoring_service.metrics.get_meter", autospec=True) as mock:
        yield mock


@pytest.fixture
def mock_counter():
    counter_mock = MagicMock()
    counter_mock.add = MagicMock()
    return counter_mock


def test_send_count_metric(mock_meter, mock_counter):
    # Arrange
    meter_instance_mock = mock_meter.return_value
    meter_instance_mock.create_counter.return_value = mock_counter

    name = "test_metric"
    description = "Test Description"
    unit = "items"
    attributes = {"key": "value"}
    count = 5

    # Act
    BaseMonitoringService.send_count_metric(name, description, unit, attributes, count)

    # Assert
    mock_meter.assert_called_once_with(BaseMonitoringService.METER_NAME)
    meter_instance_mock.create_counter.assert_called_once_with(name=name, description=description, unit=unit)
    mock_counter.add.assert_called_once_with(count, attributes)


@patch("codemie.service.monitoring.base_monitoring_service.logger.info")
def test_send_log_metric_casts_cached_tokens_money_spent_to_float(mock_logger_info):
    """Test that send_log_metric explicitly casts cached_tokens_money_spent to float for Elasticsearch."""
    # Arrange - simulate cached_tokens_money_spent as integer 0 (which Elasticsearch would map as 'long')
    name = "conversation_assistant_usage"
    attributes = {
        "user_id": "test-user",
        "input_tokens": 1000,
        "output_tokens": 500,
        "cached_tokens": 800,
        "money_spent": 0.015,
        "cached_tokens_money_spent": 0,  # Integer 0, not float!
    }

    # Act
    send_log_metric(name, attributes)

    # Assert - verify cached_tokens_money_spent was cast to float
    mock_logger_info.assert_called_once()
    logged_message = mock_logger_info.call_args[0][0]
    import json

    logged_data = json.loads(logged_message)

    # Verify cached_tokens_money_spent is now a float type in JSON
    assert "cached_tokens_money_spent" in logged_data["attributes"]
    assert isinstance(logged_data["attributes"]["cached_tokens_money_spent"], float)
    assert logged_data["attributes"]["cached_tokens_money_spent"] == 0.0  # Not integer 0


@patch("codemie.service.monitoring.base_monitoring_service.logger.info")
def test_send_log_metric_preserves_non_zero_cached_tokens_money_spent(mock_logger_info):
    """Test that send_log_metric preserves non-zero cached_tokens_money_spent values correctly."""
    name = "conversation_assistant_usage"
    attributes = {
        "user_id": "test-user",
        "cached_tokens_money_spent": 0.00144,
    }

    send_log_metric(name, attributes)

    mock_logger_info.assert_called_once()
    logged_message = mock_logger_info.call_args[0][0]
    import json

    logged_data = json.loads(logged_message)

    assert logged_data["attributes"]["cached_tokens_money_spent"] == 0.00144
    assert isinstance(logged_data["attributes"]["cached_tokens_money_spent"], float)


@patch("codemie.service.monitoring.base_monitoring_service.logger.info")
def test_send_log_metric_handles_none_cached_tokens_money_spent(mock_logger_info):
    """Test that send_log_metric handles None cached_tokens_money_spent without errors."""
    name = "conversation_assistant_usage"
    attributes = {
        "user_id": "test-user",
        "cached_tokens_money_spent": None,  # None value
    }

    send_log_metric(name, attributes)

    # Assert - should not raise an error
    mock_logger_info.assert_called_once()
    logged_message = mock_logger_info.call_args[0][0]
    import json

    logged_data = json.loads(logged_message)

    # None should remain None (not cast to float)
    assert logged_data["attributes"]["cached_tokens_money_spent"] is None


@patch("codemie.service.monitoring.base_monitoring_service.logger.info")
def test_send_log_metric_handles_missing_cached_tokens_money_spent(mock_logger_info):
    """Test that send_log_metric handles missing cached_tokens_money_spent field."""
    name = "conversation_assistant_usage"
    attributes = {
        "user_id": "test-user",
        "money_spent": 0.015,
        # No cached_tokens_money_spent field
    }

    # Act - should not raise an error
    send_log_metric(name, attributes)

    # Assert
    mock_logger_info.assert_called_once()
    logged_message = mock_logger_info.call_args[0][0]
    import json

    logged_data = json.loads(logged_message)

    # Field should not exist
    assert "cached_tokens_money_spent" not in logged_data["attributes"]


class TestEmitLlmTokenMetricMoneyGate:
    """AD-28: `emit_llm_token_metric` must not freeze an unsettled cost into metric history.

    This helper is the shared emitter behind fifteen call sites across the generator, tool-execution
    and conversation-analysis services, so one ungated emission is one defect on fifteen paths. It
    matters beyond the dashboard: `DefaultSpendLedger` sums this very attribute as money, so an
    ungated subtotal is read back **through the spend port** as though it were the whole cost.
    """

    @staticmethod
    def _tokens(**overrides):
        from codemie.core.models import TokensUsage

        defaults = {
            "input_tokens": 100,
            "output_tokens": 25,
            "cached_tokens": 5,
            "money_spent": 0.5,
        }
        return TokensUsage(**{**defaults, **overrides})

    def _emit(self, tokens):
        summary = MagicMock()
        summary.tokens_usage = tokens
        with patch(
            "codemie.service.request_summary_manager.request_summary_manager.get_summary",
            return_value=summary,
        ):
            with patch("codemie.service.monitoring.base_monitoring_service.send_log_metric") as emitted:
                emit_llm_token_metric(name="some_metric", request_id="req-1", base_attributes={"a": 1})
        return emitted.call_args.kwargs["attributes"]

    def test_settled_cost_is_emitted(self):
        """The mirror case first: a fix that drops money from every document is the same defect."""
        attributes = self._emit(self._tokens())

        assert attributes[MetricsAttributes.MONEY_SPENT] == 0.5
        assert attributes[MetricsAttributes.INPUT_TOKENS] == 100

    def test_pending_cost_emits_no_money_attribute(self):
        attributes = self._emit(self._tokens(money_spent=0.5, money_spent_pending=True))

        assert MetricsAttributes.MONEY_SPENT not in attributes
        # Tokens are final at this point and must survive the gate.
        assert attributes[MetricsAttributes.INPUT_TOKENS] == 100
        assert attributes[MetricsAttributes.OUTPUT_TOKENS] == 25

    def test_unavailable_cost_emits_no_money_attribute(self):
        attributes = self._emit(self._tokens(money_spent=0.0, money_spent_unavailable=True))

        # The sharpest form: nothing settled, so the subtotal is 0.0 and would have gone out as a
        # claim that the work was free.
        assert MetricsAttributes.MONEY_SPENT not in attributes

    def test_the_cache_money_fields_stay_unconditional(self):
        """`inventory.md` keeps these emitting so historical Elasticsearch mapping holds."""
        attributes = self._emit(self._tokens(money_spent_pending=True))

        assert MetricsAttributes.CACHED_TOKENS_MONEY_SPENT in attributes
        assert MetricsAttributes.CACHE_CREATION_TOKENS_MONEY_SPENT in attributes


class TestRecordDurationMetric:
    """Durations are recorded in seconds, and per user only where the caller asks (EPMCDME-15537)."""

    DURATION_METRIC = "a_duration"
    DURATION_SECONDS = 0.2
    CALLING_USER = "user-1"

    @pytest.fixture
    def histogram(self, mock_meter):
        with patch.dict(BaseMonitoringService._histograms, clear=True):
            yield mock_meter.return_value.create_histogram.return_value

    def _record(self, **kwargs):
        token = logging_user_id.set(self.CALLING_USER)
        try:
            BaseMonitoringService.record_duration_metric(self.DURATION_METRIC, self.DURATION_SECONDS, **kwargs)
        finally:
            logging_user_id.reset(token)

    def test_the_histogram_gets_seconds_scale_buckets(self, mock_meter, histogram):
        self._record()

        mock_meter.return_value.create_histogram.assert_called_once_with(
            name=self.DURATION_METRIC,
            description="",
            unit="s",
            explicit_bucket_boundaries_advisory=DURATION_BUCKET_BOUNDARIES_SECONDS,
        )
        histogram.record.assert_called_once()

    def test_a_sample_names_the_calling_user_by_default(self, histogram):
        self._record()

        assert histogram.record.call_args.args[1][MetricsAttributes.USER_ID] == self.CALLING_USER

    def test_an_infrastructure_sample_names_no_user(self, histogram):
        self._record(per_user=False)

        duration, attributes = histogram.record.call_args.args
        assert duration == self.DURATION_SECONDS
        assert MetricsAttributes.USER_ID not in attributes


def test_emit_llm_token_metric_sets_user_email_attribute_to_bound_username():
    with (
        patch("codemie.service.monitoring.base_monitoring_service.current_username") as mock_username,
        patch("codemie.service.monitoring.base_monitoring_service.send_log_metric") as mock_send,
    ):
        mock_username.get.return_value = "jdoe"

        emit_llm_token_metric("some_metric", None, {"k": "v"})

    attributes = mock_send.call_args.kwargs["attributes"]
    assert attributes[MetricsAttributes.USER_EMAIL] == "jdoe"
    assert attributes["k"] == "v"
