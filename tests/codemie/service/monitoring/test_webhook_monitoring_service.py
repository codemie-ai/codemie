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

from unittest.mock import patch

from codemie.service.monitoring.webhook_monitoring_service import WebhookMonitoringService
from codemie.service.monitoring.metrics_constants import MetricsAttributes


class TestWebhookMonitoringService:
    @patch.object(WebhookMonitoringService, 'send_count_metric')
    def test_send_webhook_invocation_metric_success(self, mock_send_count_metric):
        webhook_id = "test_webhook_id"
        project_name = "test_project"
        user_id = "test_user_id"
        resource_type = "ASSISTANT"
        resource_id = "test_resource_id"
        webhook_alias = "test_webhook"

        expected_attributes = {
            MetricsAttributes.WEBHOOK_ID: webhook_id,
            MetricsAttributes.PROJECT: project_name,
            MetricsAttributes.USER_ID: user_id,
            MetricsAttributes.WEBHOOK_RESOURCE_TYPE: resource_type,
            MetricsAttributes.WEBHOOK_RESOURCE_ID: resource_id,
            MetricsAttributes.WEBHOOK_ALIAS: webhook_alias,
            MetricsAttributes.STATUS: "success",
        }

        WebhookMonitoringService.send_webhook_invocation_metric(
            webhook_id=webhook_id,
            project_name=project_name,
            user_id=user_id,
            success=True,
            resource_type=resource_type,
            resource_id=resource_id,
            webhook_alias=webhook_alias,
        )

        mock_send_count_metric.assert_called_with(
            name=f"{WebhookMonitoringService.WEBHOOK_BASE_METRIC}_total",
            description="Total number of webhook invocations",
            attributes=expected_attributes,
        )

        mock_send_count_metric.assert_called_once()

    @patch.object(WebhookMonitoringService, 'send_count_metric')
    def test_send_webhook_invocation_metric_error(self, mock_send_count_metric):
        webhook_id = "test_webhook_id"
        project_name = "test_project"
        user_id = "test_user_id"
        resource_type = "ASSISTANT"
        resource_id = "test_resource_id"
        webhook_alias = "test_webhook"
        additional_attributes = {"error_cause": "webhook_not_found"}

        expected_attributes = {
            MetricsAttributes.WEBHOOK_ID: webhook_id,
            MetricsAttributes.PROJECT: project_name,
            MetricsAttributes.USER_ID: user_id,
            MetricsAttributes.WEBHOOK_RESOURCE_TYPE: resource_type,
            MetricsAttributes.WEBHOOK_RESOURCE_ID: resource_id,
            MetricsAttributes.WEBHOOK_ALIAS: webhook_alias,
            MetricsAttributes.STATUS: "error",
            "error_cause": "webhook_not_found",
        }

        WebhookMonitoringService.send_webhook_invocation_metric(
            webhook_id=webhook_id,
            project_name=project_name,
            user_id=user_id,
            success=False,
            resource_type=resource_type,
            resource_id=resource_id,
            webhook_alias=webhook_alias,
            additional_attributes=additional_attributes,
        )

        mock_send_count_metric.assert_any_call(
            name=f"{WebhookMonitoringService.WEBHOOK_BASE_METRIC}_total",
            description="Total number of webhook invocations",
            attributes=expected_attributes,
        )

        mock_send_count_metric.assert_any_call(
            name=f"{WebhookMonitoringService.WEBHOOK_BASE_METRIC}_error_total",
            description="Total number of failed webhook invocations",
            attributes=expected_attributes,
        )

        assert mock_send_count_metric.call_count == 2
