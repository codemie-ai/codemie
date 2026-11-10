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

"""Hosted viewing sessions end with the platform sign-out.

A sign-out records a per-person cutoff, so every hosted viewing session and launch ticket that person
holds stops working at once, on every device. It is best effort, bounded and never raises: a database
or feature outage never blocks the platform sign-out. Before it signs out, the UI also calls
POST /v1/hosted-applications/sessions/revoke, which reports a failure instead of hiding it.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress

from codemie.configs.logger import logger
from codemie.core.hosted_applications.identity import PrincipalRevocation
from codemie.service.hosted_applications.observability import hosted_request_monitoring

# A platform sign-out never waits longer than this for hosted revocation.
LOGOUT_REVOCATION_TIMEOUT_SECONDS = 2.0


async def revoke_hosted_sessions_best_effort(request, *, principal: str | None) -> None:
    """End every hosted session of the signed-out ``principal``; returns at once while the feature is off."""
    runtime = getattr(getattr(request.scope.get("app"), "state", None), "hosted_applications", None)
    settings = getattr(runtime, "settings", None)
    if not principal or settings is None or not settings.enabled:
        return
    try:
        async with asyncio.timeout(LOGOUT_REVOCATION_TIMEOUT_SECONDS):
            await runtime.launch.logout(PrincipalRevocation(principal))
        result = "revoked"
    except TimeoutError:
        result = "timeout"
        logger.warning("Hosted session revocation timed out; platform logout continues")
    except Exception as exc:
        result = "failed"
        logger.warning(f"Hosted session revocation failed; platform logout continues: {type(exc).__name__}")
    with suppress(Exception):
        hosted_request_monitoring().logout(result)
