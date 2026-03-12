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

from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService, send_log_metric


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
