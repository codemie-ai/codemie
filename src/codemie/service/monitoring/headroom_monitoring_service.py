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

"""Headroom analytics monitoring service: emits headroom_compaction_usage events."""

from __future__ import annotations

from typing import TYPE_CHECKING

from codemie.core.headroom_savings import HeadroomSavings
from codemie.service.analytics.metric_names import MetricName
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService

if TYPE_CHECKING:
    from codemie.rest_api.security.user import User


class HeadroomMonitoringService(BaseMonitoringService):
    """Emit headroom_compaction_usage events to Elasticsearch via the OpenTelemetry log exporter."""

    @classmethod
    def send_compaction_metric(  # noqa: PLR0913 - keyword-only, one argument per event attribute
        cls,
        *,
        user: User,
        savings: HeadroomSavings | None,
        conversation_id: str | None,
        assistant_id: str | None,
        project: str,
        request_id: str | None = None,
    ) -> None:
        """Emit one event for a request that saved tokens; a no-op when nothing was saved.

        Token counts are ints (not floats) so the Elasticsearch mapping stays numeric-integer.
        """
        if savings is None or savings.tokens_saved <= 0:
            return
        cls.send_count_metric(
            name=MetricName.HEADROOM_COMPACTION_USAGE.value,
            attributes={
                "user_id": str(user.id),
                "user_email": getattr(user, "email", None) or getattr(user, "username", None),
                "project": project,
                "conversation_id": conversation_id,
                "assistant_id": str(assistant_id) if assistant_id else None,
                "request_id": request_id,
                "tokens_original": int(savings.tokens_original),
                "tokens_compacted": int(savings.tokens_compacted),
                "tokens_saved": int(savings.tokens_saved),
                "mode": savings.mode,
                "strategy": savings.strategy,
                "calls": int(savings.calls),
            },
        )
