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

"""Analytics service module for dashboard metrics and reporting.

This module provides business logic for querying and formatting analytics data
from Elasticsearch with role-based access control.
"""

from __future__ import annotations

from codemie.service.analytics.analytics_service import AnalyticsService

__all__ = ["AnalyticsService"]
