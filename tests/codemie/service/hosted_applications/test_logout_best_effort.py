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

"""Platform logout never depends on hosted revocation: best effort, bounded, never raising."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from starlette.requests import Request

from codemie.core.hosted_applications.identity import PrincipalRevocation
from codemie.core.hosted_applications.settings import IdentityAuthority
from codemie.service.hosted_applications import logout as hosted_logout

AUTHORITY = IdentityAuthority("codemie-local")


def runtime(enabled=True, logout=None):
    settings = SimpleNamespace(enabled=enabled, authority=AUTHORITY)
    return SimpleNamespace(settings=settings, launch=SimpleNamespace(logout=logout or AsyncMock()))


def request(hosted=None):
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/v1/user/log_out",
        "headers": [],
        "query_string": b"",
        "app": SimpleNamespace(state=SimpleNamespace(hosted_applications=hosted)),
        "state": {},
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_sign_out_cuts_off_every_hosted_session_of_the_person():
    hosted = runtime()
    with patch.object(hosted_logout, "hosted_request_monitoring") as monitoring:
        await hosted_logout.revoke_hosted_sessions_best_effort(request(hosted), principal="user-1")
    hosted.launch.logout.assert_awaited_once_with(PrincipalRevocation("user-1"))
    monitoring.return_value.logout.assert_called_once_with("revoked")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("hosted", "principal"),
    [(None, "user-1"), (runtime(enabled=False), "user-1"), (runtime(), None), (runtime(), "")],
)
async def test_nothing_happens_without_the_feature_or_a_signed_out_person(hosted, principal):
    await hosted_logout.revoke_hosted_sessions_best_effort(request(hosted), principal=principal)
    if hosted is not None:
        hosted.launch.logout.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failing_revocation_never_breaks_platform_logout():
    hosted = runtime(logout=AsyncMock(side_effect=RuntimeError("database down")))
    with patch.object(hosted_logout, "hosted_request_monitoring") as monitoring:
        await hosted_logout.revoke_hosted_sessions_best_effort(request(hosted), principal="user-1")
    monitoring.return_value.logout.assert_called_once_with("failed")


@pytest.mark.asyncio
async def test_a_slow_revocation_is_abandoned_after_its_bound():
    async def stall(_):
        await asyncio.sleep(5)

    hosted = runtime(logout=stall)
    with (
        patch.object(hosted_logout, "LOGOUT_REVOCATION_TIMEOUT_SECONDS", 0.05),
        patch.object(hosted_logout, "hosted_request_monitoring") as monitoring,
    ):
        await asyncio.wait_for(
            hosted_logout.revoke_hosted_sessions_best_effort(request(hosted), principal="user-1"), timeout=1
        )
    monitoring.return_value.logout.assert_called_once_with("timeout")
