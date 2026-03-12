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

from codemie.rest_api.security.user import User
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService
from codemie.service.monitoring.metrics_constants import MetricsAttributes


class ProjectMonitoringService(BaseMonitoringService):
    PROJECT_BASE_METRIC = "project"

    @classmethod
    def send_project_creation_metric(
        cls,
        user: User,
        project_name: str,
    ):
        attributes = {
            MetricsAttributes.USER_ID: user.id,
            MetricsAttributes.USER_NAME: user.name,
            MetricsAttributes.PROJECT: project_name,
        }
        cls.send_count_metric(name=f"create_{cls.PROJECT_BASE_METRIC}", attributes=attributes)
