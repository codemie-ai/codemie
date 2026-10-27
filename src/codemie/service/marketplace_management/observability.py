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

"""Metrics for the marketplace moderation operations (EXECUTION-PLAN 9.4, F-36).

Before this module, the one marketplace operation that changes *nothing* --
`POST /{eid}/assess` -- was observable, and the four that change moderation
state were not: `grep '_track_assistant_management_metric\\|metric'` over
`queue_service.py` and `marketplace_management.py` returned nothing, while the
legacy publish path in `routers/assistant.py` tracked every mutation.

Why a separate emitter rather than reusing `_track_assistant_management_metric`
(`routers/assistant.py:2291`):

That helper takes an `Assistant` ORM instance and a `User` and reads eight
attributes off them. The queue service has neither -- it deliberately never
hydrates the row (the whole point of the compare-and-set is that it does not
read-modify-write), and it is a service, not a router, so importing a private
router function into it would invert the dependency and close an import cycle
(`routers/assistant.py` already imports `queue_service`). What matters for
observability is that the counters reach the *same sink* under the same naming
convention, which is what this does: `BaseMonitoringService.send_count_metric`,
`<name>` on success and `<name>_error` on failure, `MetricsAttributes` keys.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from codemie.configs import logger
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService
from codemie.service.monitoring.metrics_constants import MetricsAttributes

# One metric per moderation transition, plus the audit run. Named for the
# operation rather than for the resulting status so a dashboard can read
# "how many force-publishes happened" without joining anything.
METRIC_SUBMIT = "marketplace_submit"
METRIC_RE_SUBMIT = "marketplace_re_submit"
METRIC_APPROVE = "marketplace_approve"
METRIC_REJECT = "marketplace_reject"
METRIC_FORCE_PUBLISH = "marketplace_force_publish"
METRIC_WITHDRAW = "marketplace_withdraw"
METRIC_AUDIT_RUN = "marketplace_audit_run"


def track_marketplace_metric(
    metric_name: str,
    *,
    success: bool = True,
    entity_id: Optional[str] = None,
    entity_name: Optional[str] = None,
    actor_id: Optional[str] = None,
    actor_email: Optional[str] = None,
    additional_attributes: Optional[Mapping[str, Any]] = None,
) -> None:
    """Emit one count metric for a marketplace operation.

    Never raises: a moderation decision is already committed by the time this
    is called, and losing a counter must not turn a successful takedown into a
    500. `send_count_metric` swallows its own failures too; the belt here is for
    anything that goes wrong assembling the attributes.
    """
    try:
        attributes: dict[str, Any] = {}
        if entity_id:
            attributes[MetricsAttributes.ASSISTANT_ID] = entity_id
        if entity_name:
            attributes[MetricsAttributes.ASSISTANT_NAME] = entity_name
        if actor_id:
            attributes[MetricsAttributes.USER_ID] = actor_id
        if actor_email:
            attributes[MetricsAttributes.USER_EMAIL] = actor_email
        if additional_attributes:
            attributes.update({k: v for k, v in additional_attributes.items() if v is not None})

        BaseMonitoringService.send_count_metric(
            name=metric_name if success else f"{metric_name}_error",
            attributes=attributes,
        )
    except Exception as exc:  # noqa: BLE001 - observability must never fail a decision
        logger.warning(f"Failed to track marketplace metric {metric_name!r}: {exc}")
