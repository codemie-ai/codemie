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

"""Shared `analyticsInsights`/`analyticsCLIInsights` feature gates, mirroring budget/feature_gate.py."""

from __future__ import annotations

from fastapi import Depends

from codemie.configs.customer_config import customer_config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.authentication import authenticate


def ensure_analytics_insights_enabled() -> None:
    if not customer_config.is_feature_enabled("analyticsInsights"):
        raise ExtendedHTTPException(
            code=403,
            message="Feature not available",
            details="Insights analytics are not enabled for this deployment.",
            help="Insights require LiteLLM to be configured and enabled.",
        )


def require_analytics_insights_enabled() -> None:
    ensure_analytics_insights_enabled()


def ensure_analytics_cli_insights_enabled() -> None:
    if not customer_config.is_feature_enabled("analyticsCLIInsights"):
        raise ExtendedHTTPException(
            code=403,
            message="Feature not available",
            details="CLI Insights analytics are not enabled for this deployment.",
            help="CLI Insights require LiteLLM to be configured and enabled.",
        )


def require_analytics_cli_insights_enabled() -> None:
    ensure_analytics_cli_insights_enabled()


# authenticate must run first so an unauthenticated caller gets 401, never 403.
analytics_insights_guard = [Depends(authenticate), Depends(require_analytics_insights_enabled)]
analytics_cli_insights_guard = [Depends(authenticate), Depends(require_analytics_cli_insights_enabled)]
