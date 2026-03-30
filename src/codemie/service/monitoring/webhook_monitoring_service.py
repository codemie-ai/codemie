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

from typing import Optional

from codemie.core.dependecies import get_current_project
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService
from codemie.service.monitoring.metrics_constants import MetricsAttributes


class WebhookMonitoringService(BaseMonitoringService):
    WEBHOOK_BASE_METRIC = "webhook_invocation"

    @classmethod
    def send_webhook_invocation_metric(
        cls,
        webhook_id: str,
        project_name: str,
        user_id: str,
        success: bool,
        resource_type: str,
        resource_id: str,
        webhook_alias: str,
        additional_attributes: Optional[dict] = None,
    ):
        """
        Send webhook invocation metrics.

        Args:
            webhook_id (str): ID of the webhook
            project_name (str): Name of the project
            user_id (str): ID of the user
            success (bool): Whether the invocation was successful
            resource_type (str): Type of resource (ASSISTANT, WORKFLOW, DATASOURCE)
            resource_id (str): ID of the resource
            webhook_alias (str): Alias of the webhook
            additional_attributes (Optional[dict]): Additional attributes to include
        """
        attributes = {
            MetricsAttributes.WEBHOOK_ID: webhook_id,
            MetricsAttributes.PROJECT: get_current_project(fallback=project_name),
            MetricsAttributes.USER_ID: user_id,
            MetricsAttributes.WEBHOOK_RESOURCE_TYPE: resource_type,
            MetricsAttributes.WEBHOOK_RESOURCE_ID: resource_id,
            MetricsAttributes.WEBHOOK_ALIAS: webhook_alias,
            MetricsAttributes.STATUS: "success" if success else "error",
        }

        if additional_attributes:
            attributes.update(additional_attributes)

        # Send metric for total invocations
        cls.send_count_metric(
            name=f"{cls.WEBHOOK_BASE_METRIC}_total",
            description="Total number of webhook invocations",
            attributes=attributes,
        )

        if not success:
            # Send metric for failed invocations
            cls.send_count_metric(
                name=f"{cls.WEBHOOK_BASE_METRIC}_error_total",
                description="Total number of failed webhook invocations",
                attributes=attributes,
            )
