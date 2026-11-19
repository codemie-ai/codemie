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

"""Where a caller's ``id`` and ``user_type`` come from (EPMCDME-16895).

Trace of ``authenticate`` -> ``PersistentUserProvider`` -> ``AuthenticationService``:

* ``User.id`` is the token subject (``claims["sub"]`` for the local IdP, ``idp_user.id`` for an
  enterprise IdP): the identity the IdP / signing key vouches for.
* For a user that already has a DB row, ``user_type`` is read from the DB row only
  (``_build_security_user``); ``sync_idp_user_profile`` copies email, name and picture,
  never ``user_type``. A claim cannot change it.
* On first login there is no row yet and ``create_user_from_idp`` stores ``idp_user.user_type``
  (validated by ``validate_user_type``). So the IdP decides ``user_type`` when the row is created.
  This is the accepted trust model: whoever can mint a token with the configured account id
  is already that account, so the ``user_type`` check is a second layer, not the only one.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from codemie.rest_api.models.user_management import UserDB
from codemie.rest_api.security import user as security_user
from codemie.service.user.authentication_service import AuthenticationService, clear_auth_token_cache


@pytest.mark.asyncio
async def test_claim_cannot_set_user_type_of_existing_user():
    clear_auth_token_cache()
    user_id = str(uuid4())
    db_user = UserDB(
        id=user_id,
        email="a@example.com",
        username="a",
        name="A",
        user_type="regular",
        is_active=True,
        deleted_at=None,
    )
    idp_user = security_user.User(
        id=user_id,
        username="a",
        name="A",
        email="a@example.com",
        user_type="service_account",
        roles=[],
        project_names=[],
        admin_project_names=[],
        knowledge_bases=[],
        is_admin=False,
    )
    session = AsyncMock()

    @asynccontextmanager
    async def _session_cm():
        yield session

    async def _finalize(user, _source):
        return user

    with (
        patch("codemie.clients.postgres.get_async_session", _session_cm),
        patch("codemie.service.user.authentication_service.user_repository") as repo,
        patch.object(AuthenticationService, "_finalize_authentication", new=AsyncMock(side_effect=_finalize)),
        patch("codemie.service.user.authentication_service.config") as cfg,
    ):
        cfg.IDP_PROVIDER = "keycloak"
        repo.aget_by_id = AsyncMock(return_value=db_user)
        repo.aupdate = AsyncMock(return_value=db_user)

        result = await AuthenticationService.authenticate_persistent_user(user_id, idp_user, "")

    assert result.id == user_id
    assert result.user_type == "regular"
