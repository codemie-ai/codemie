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

"""Plugin monitoring service for tracking plugin key usage."""

from typing import Optional

from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService
from codemie.service.monitoring.metrics_constants import (
    PLUGIN_KEYS_TOTAL_METRIC,
    PLUGIN_KEYS_ERRORS_METRIC,
    PLUGIN_KEYS_INIT_FAILURES_METRIC,
    MetricsAttributes,
    PLUGIN_AUTH_METRIC,
)


class PluginMonitoringService(BaseMonitoringService):
    """Service for monitoring plugin usage."""

    @classmethod
    def send_plugin_key_metrics(
        cls,
        subject: str,
        success: bool,
        plugin_key: Optional[str] = None,
        execution_time_ms: Optional[int] = None,
        additional_attributes: Optional[dict] = None,
    ):
        """
        Send metrics about plugin key usage.

        Args:
            plugin_key: The plugin key that was used
            subject: The subject that was called
            success: Whether the call was successful
            execution_time_ms: Execution time in milliseconds, if available
            additional_attributes: Any additional attributes to include
        """
        attributes = {
            MetricsAttributes.PLUGIN_KEY: plugin_key,
            MetricsAttributes.PLUGIN_SUBJECT: subject,
        }

        if execution_time_ms is not None:
            attributes[MetricsAttributes.EXECUTION_TIME] = execution_time_ms

        if additional_attributes:
            attributes.update(additional_attributes)

        if success:
            cls.send_count_metric(
                name=PLUGIN_KEYS_TOTAL_METRIC,
                attributes=attributes,
            )
        else:
            cls.send_count_metric(
                name=PLUGIN_KEYS_ERRORS_METRIC,
                attributes=attributes,
            )

    @classmethod
    def send_plugin_init_failure_metrics(
        cls,
        subject: str,
        failure_type: str,
        plugin_key: Optional[str] = None,
        execution_time_ms: Optional[int] = None,
        additional_attributes: Optional[dict] = None,
    ):
        """
        Send metrics about plugin initialization failures.

        Args:
            plugin_key: The plugin key that was used
            subject: The subject that was called
            failure_type: Type of initialization failure (e.g., "request_not_initialized", "response_not_initialized")
            execution_time_ms: Execution time in milliseconds, if available
            additional_attributes: Any additional attributes to include
        """
        attributes = {
            MetricsAttributes.PLUGIN_KEY: plugin_key,
            MetricsAttributes.PLUGIN_SUBJECT: subject,
            MetricsAttributes.FAILURE_TYPE: failure_type,
        }

        if execution_time_ms is not None:
            attributes[MetricsAttributes.EXECUTION_TIME] = execution_time_ms

        if additional_attributes:
            attributes.update(additional_attributes)

        cls.send_count_metric(
            name=PLUGIN_KEYS_INIT_FAILURES_METRIC,
            attributes=attributes,
        )

    @classmethod
    def send_plugin_auth_metrics(
        cls,
        plugin_key: str,
        success: bool,
        user_id: Optional[str] = "",
        project: Optional[str] = "",
        additional_attributes: Optional[dict] = None,
    ):
        """
        Send metrics about plugin auth for user.
        """
        attributes = {
            MetricsAttributes.PLUGIN_KEY: plugin_key,
            MetricsAttributes.STATUS: success,
        }
        if user_id:
            attributes[MetricsAttributes.USER_ID] = user_id
        if project:
            attributes[MetricsAttributes.PROJECT] = project
        if additional_attributes:
            attributes.update(additional_attributes)

        cls.send_count_metric(
            name=PLUGIN_AUTH_METRIC,
            attributes=attributes,
        )
