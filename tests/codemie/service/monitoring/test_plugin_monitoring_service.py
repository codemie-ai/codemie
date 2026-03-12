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

"""Tests for plugin monitoring service."""

from unittest import mock


from codemie.service.monitoring.plugin_monitoring_service import PluginMonitoringService
from codemie.service.monitoring.metrics_constants import (
    PLUGIN_KEYS_TOTAL_METRIC,
    PLUGIN_KEYS_ERRORS_METRIC,
    PLUGIN_KEYS_INIT_FAILURES_METRIC,
)


class TestPluginMonitoringService:
    """Test suite for PluginMonitoringService."""

    @mock.patch('codemie.service.monitoring.base_monitoring_service.BaseMonitoringService.send_count_metric')
    def test_send_plugin_key_metrics_success(self, mock_send_count):
        """Test sending plugin key metrics with success=True."""
        # Call the method
        PluginMonitoringService.send_plugin_key_metrics(
            plugin_key="test_key",
            subject="test_key.test_subject",
            success=True,
            execution_time_ms=100,
            additional_attributes={"test_attr": "test_value"},
        )

        # Check the call to the base class method
        mock_send_count.assert_called_once()
        call_args = mock_send_count.call_args[1]

        assert call_args["name"] == PLUGIN_KEYS_TOTAL_METRIC
        assert call_args["attributes"]["plugin_key"] == "test_key"
        assert call_args["attributes"]["plugin_subject"] == "test_key.test_subject"
        assert call_args["attributes"]["execution_time"] == 100
        assert call_args["attributes"]["test_attr"] == "test_value"

    @mock.patch('codemie.service.monitoring.base_monitoring_service.BaseMonitoringService.send_count_metric')
    def test_send_plugin_key_metrics_failure(self, mock_send_count):
        """Test sending plugin key metrics with success=False."""
        # Call the method
        PluginMonitoringService.send_plugin_key_metrics(
            plugin_key="test_key",
            subject="test_key.test_subject",
            success=False,
            execution_time_ms=100,
            additional_attributes={"test_attr": "test_value"},
        )

        # Check the call to the base class method
        mock_send_count.assert_called_once()
        call_args = mock_send_count.call_args[1]

        assert call_args["name"] == PLUGIN_KEYS_ERRORS_METRIC
        assert call_args["attributes"]["plugin_key"] == "test_key"
        assert call_args["attributes"]["plugin_subject"] == "test_key.test_subject"
        assert call_args["attributes"]["execution_time"] == 100
        assert call_args["attributes"]["test_attr"] == "test_value"

    @mock.patch('codemie.service.monitoring.base_monitoring_service.BaseMonitoringService.send_count_metric')
    def test_send_plugin_key_metrics_no_execution_time(self, mock_send_count):
        """Test sending plugin key metrics without execution time."""
        # Call the method
        PluginMonitoringService.send_plugin_key_metrics(
            plugin_key="test_key", subject="test_key.test_subject", success=True
        )

        # Check the call to the base class method
        mock_send_count.assert_called_once()
        call_args = mock_send_count.call_args[1]

        assert call_args["name"] == PLUGIN_KEYS_TOTAL_METRIC
        assert call_args["attributes"]["plugin_key"] == "test_key"
        assert call_args["attributes"]["plugin_subject"] == "test_key.test_subject"
        assert "execution_time" not in call_args["attributes"]

    @mock.patch('codemie.service.monitoring.base_monitoring_service.BaseMonitoringService.send_count_metric')
    def test_send_plugin_init_failure_metrics_request_not_initialized(self, mock_send_count):
        """Test sending plugin initialization failure metrics for request not initialized."""
        # Call the method
        PluginMonitoringService.send_plugin_init_failure_metrics(
            plugin_key="test_key",
            subject="test_key.test_subject",
            failure_type="request_not_initialized",
            execution_time_ms=100,
            additional_attributes={"test_attr": "test_value"},
        )

        # Check the call to the base class method
        mock_send_count.assert_called_once()
        call_args = mock_send_count.call_args[1]

        assert call_args["name"] == PLUGIN_KEYS_INIT_FAILURES_METRIC
        assert call_args["attributes"]["plugin_key"] == "test_key"
        assert call_args["attributes"]["plugin_subject"] == "test_key.test_subject"
        assert call_args["attributes"]["failure_type"] == "request_not_initialized"
        assert call_args["attributes"]["execution_time"] == 100
        assert call_args["attributes"]["test_attr"] == "test_value"

    @mock.patch('codemie.service.monitoring.base_monitoring_service.BaseMonitoringService.send_count_metric')
    def test_send_plugin_init_failure_metrics_response_not_initialized(self, mock_send_count):
        """Test sending plugin initialization failure metrics for response not initialized."""
        # Call the method
        PluginMonitoringService.send_plugin_init_failure_metrics(
            plugin_key="test_key",
            subject="test_key.test_subject",
            failure_type="response_not_initialized",
            execution_time_ms=100,
            additional_attributes={"test_attr": "test_value"},
        )

        # Check the call to the base class method
        mock_send_count.assert_called_once()
        call_args = mock_send_count.call_args[1]

        assert call_args["name"] == PLUGIN_KEYS_INIT_FAILURES_METRIC
        assert call_args["attributes"]["plugin_key"] == "test_key"
        assert call_args["attributes"]["plugin_subject"] == "test_key.test_subject"
        assert call_args["attributes"]["failure_type"] == "response_not_initialized"
        assert call_args["attributes"]["execution_time"] == 100
        assert call_args["attributes"]["test_attr"] == "test_value"

    @mock.patch('codemie.service.monitoring.base_monitoring_service.BaseMonitoringService.send_count_metric')
    def test_send_plugin_init_failure_metrics_no_execution_time(self, mock_send_count):
        """Test sending plugin initialization failure metrics without execution time."""
        # Call the method
        PluginMonitoringService.send_plugin_init_failure_metrics(
            plugin_key="test_key", subject="test_key.test_subject", failure_type="request_not_initialized"
        )

        # Check the call to the base class method
        mock_send_count.assert_called_once()
        call_args = mock_send_count.call_args[1]

        assert call_args["name"] == PLUGIN_KEYS_INIT_FAILURES_METRIC
        assert call_args["attributes"]["plugin_key"] == "test_key"
        assert call_args["attributes"]["plugin_subject"] == "test_key.test_subject"
        assert call_args["attributes"]["failure_type"] == "request_not_initialized"
        assert "execution_time" not in call_args["attributes"]
