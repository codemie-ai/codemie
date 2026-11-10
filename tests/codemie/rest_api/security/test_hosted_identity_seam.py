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

"""The user providers record the hosted identity only after the platform loaded the user."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.user import User
from codemie.rest_api.security.user_providers.legacy_jwt import LegacyJwtUserProvider
from codemie.rest_api.security.user_providers.persistent import PersistentUserProvider

SEAM = "codemie.core.hosted_applications.identity.record_platform_identity"


def idp_accepting(calls):
    async def authenticate(request):
        calls.append("idp")
        return User(id="kc-user", username="kc-user", auth_token="tok", project_names=[], knowledge_bases=[])

    idp = MagicMock()
    idp.authenticate = AsyncMock(side_effect=authenticate)
    return idp


def idp_rejecting():
    idp = MagicMock()
    idp.authenticate = AsyncMock(side_effect=ExtendedHTTPException(code=401, message="Authentication failed"))
    return idp


@pytest.fixture
def persistent_parts():
    with (
        patch("codemie.rest_api.security.user_providers.persistent.config") as config,
        patch("codemie.rest_api.security.user_providers.persistent.authentication_service") as service,
    ):
        config.ENV = "production"
        config.IDP_PROVIDER = "keycloak"
        service.authenticate_persistent_user = AsyncMock(
            return_value=User(id="db-user", project_names=[], knowledge_bases=[])
        )
        yield config, service


@pytest.mark.asyncio
async def test_identity_is_recorded_after_the_user_is_loaded(persistent_parts):
    _, service = persistent_parts
    calls = []
    request = MagicMock()
    request.headers.get = MagicMock(return_value=None)

    async def load(*args, **kwargs):
        calls.append("load")
        return User(id="db-user", project_names=[], knowledge_bases=[])

    service.authenticate_persistent_user = AsyncMock(side_effect=load)
    with patch(SEAM, side_effect=lambda *a, **k: calls.append(("seam", a, k))) as seam:
        await PersistentUserProvider().authenticate_and_load_user(request, idp_accepting(calls))
    assert calls == ["idp", "load", ("seam", (request, "db-user"), {"provider": "keycloak", "verified": None})]
    seam.assert_called_once()


@pytest.mark.asyncio
async def test_no_identity_is_recorded_when_the_idp_rejects_the_request(persistent_parts):
    request = MagicMock()
    request.headers.get = MagicMock(return_value=None)
    with patch(SEAM) as seam, pytest.raises(ExtendedHTTPException):
        await PersistentUserProvider().authenticate_and_load_user(request, idp_rejecting())
    seam.assert_not_called()


@pytest.mark.asyncio
async def test_no_identity_is_recorded_when_user_loading_rejects_the_request(persistent_parts):
    _, service = persistent_parts
    service.authenticate_persistent_user = AsyncMock(
        side_effect=ExtendedHTTPException(code=401, message="Authentication failed")
    )
    request = MagicMock()
    request.headers.get = MagicMock(return_value=None)
    with patch(SEAM) as seam, pytest.raises(ExtendedHTTPException):
        await PersistentUserProvider().authenticate_and_load_user(request, idp_accepting([]))
    seam.assert_not_called()


@pytest.mark.asyncio
async def test_local_sign_in_passes_the_validated_jwt():
    with (
        patch("codemie.rest_api.security.user_providers.persistent.config") as config,
        patch("codemie.rest_api.security.user_providers.persistent.authentication_service") as service,
        patch("codemie.rest_api.security.user_providers.persistent._extract_local_auth_token", return_value="t"),
        patch("codemie.rest_api.security.jwt_local.validate_local_jwt", return_value={"sub": "local-user"}),
        patch(SEAM) as seam,
    ):
        config.ENV = "production"
        config.IDP_PROVIDER = "local"
        service.authenticate_persistent_user = AsyncMock(
            return_value=User(id="local-user", project_names=[], knowledge_bases=[])
        )
        request = MagicMock()
        await PersistentUserProvider().authenticate_and_load_user(request, MagicMock())
    seam.assert_called_once_with(request, "local-user", provider="local", verified=({"sub": "local-user"}, "t"))


@pytest.mark.asyncio
async def test_legacy_provider_records_nothing():
    with patch("codemie.rest_api.security.user_providers.legacy_jwt.authentication_service") as service:
        service.ensure_project_exists = AsyncMock()
        with patch(SEAM) as seam:
            await LegacyJwtUserProvider().authenticate_and_load_user(MagicMock(), idp_accepting([]))
    seam.assert_not_called()
