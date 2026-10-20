# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""Project gate for CLI analytics ingestion (EPMCDME-16025).

Analytics is stored only for a project that has `cli_analytics_enabled`. The project is the one the
CLI names in `X-CodeMie-Project`; the sender must have access to it, and no explicit project value
inside the request may differ from it. Anything else is discarded with a reason, answered as HTTP 200
(a 403 would suspend the CLI's forwarding, other errors would be retried forever). Only a policy
lookup that fails raises, so the endpoint answers 503 and the CLI retries.
"""

from __future__ import annotations

import logging

from codemie.clients.postgres import get_async_session
from codemie.repository.application_repository import application_repository
from codemie.repository.cli_analytics.ports import IngestGate, TelemetryStorageUnavailableError
from codemie.rest_api.security.user import User

logger = logging.getLogger(__name__)

REASON_DISABLED = "cli_analytics_disabled"
REASON_CONTEXT_REQUIRED = "project_context_required"
REASON_CONTEXT_CONFLICT = "project_context_conflict"


class _ProjectIngestGate:
    """The gate of one request. When it accepts the request, `canonical_project` is the stored name of the
    project it was accepted for (the request may spell it in another case), so what is persisted follows the
    project that was found, not the spelling that was sent."""

    def __init__(self, user: User, header_project: str | None) -> None:
        self._user = user
        self._project = header_project or ""
        self.canonical_project: str | None = None

    async def __call__(self, explicit: frozenset[str]) -> str | None:
        self.canonical_project = None
        project = self._project
        if not project.strip():  # blank counts as absent; the value itself is used exactly as received
            return REASON_CONTEXT_REQUIRED
        # Same rule as the lookup below (it compares lower()): no strip, no casefold.
        if len({value.lower() for value in explicit} | {project.lower()}) > 1:
            return REASON_CONTEXT_CONFLICT
        try:
            async with get_async_session() as session:
                application = await application_repository.aget_active_by_name_case_insensitive(session, project)
        except Exception as exc:
            logger.warning("cli_analytics: project policy lookup failed: %s", type(exc).__name__)
            raise TelemetryStorageUnavailableError("Cannot read the project's CLI analytics setting") from exc
        if application is None or application.deleted_at is not None:
            return REASON_CONTEXT_REQUIRED
        if not self._user.has_access_to_application(application.name):
            return REASON_CONTEXT_REQUIRED
        if not application.cli_analytics_enabled:
            return REASON_DISABLED
        self.canonical_project = application.name
        return None


def build_ingest_gate(user: User, header_project: str | None) -> IngestGate:
    """The gate for one request: `explicit` is every project value the request body carries."""
    return _ProjectIngestGate(user, header_project)
