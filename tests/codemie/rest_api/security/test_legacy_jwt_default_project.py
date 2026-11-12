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

"""IDP-claims mode derives the default project for budget attribution (EPMCDME-16625, D8)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.rest_api.security.user import User
from codemie.rest_api.security.user_providers.legacy_jwt import LegacyJwtUserProvider


def _attribution_default(user: User) -> str | None:
    """The derived default is used by budget attribution only; the user object keeps no default."""
    from codemie.service.budget.attribution_resolver import AssetKind, attribution_input_for_user

    assert user.default_project is None
    with patch("codemie.service.budget.attribution_resolver.config.ENABLE_USER_MANAGEMENT", False):
        return attribution_input_for_user(user, kind=AssetKind.NO_ASSET).default_project


_REMEMBER = "codemie.rest_api.security.user_providers.legacy_jwt.idp_user_snapshot_service.remember"


async def _authenticate(user: User) -> User:
    idp = MagicMock()
    idp.authenticate = AsyncMock(return_value=user)
    with (
        patch(
            "codemie.rest_api.security.user_providers.legacy_jwt.authentication_service.ensure_project_exists",
            new=AsyncMock(),
        ),
        patch(_REMEMBER, new=AsyncMock()),
    ):
        return await LegacyJwtUserProvider().authenticate_and_load_user(MagicMock(), idp)


@pytest.mark.asyncio
async def test_legacy_jwt_user_gets_personal_default_project():
    user = User(id="u1", username="dmytro", email="d@example.com", project_names=["zeta", "d@example.com"])

    result = await _authenticate(user)

    assert _attribution_default(result) == "d@example.com"


@pytest.mark.asyncio
async def test_legacy_jwt_user_without_personal_project_gets_alphabetical_first():
    user = User(id="u1", username="dmytro", email="d@example.com", project_names=["zeta", "alpha"])

    result = await _authenticate(user)

    assert _attribution_default(result) == "alpha"


@pytest.mark.asyncio
async def test_legacy_jwt_username_workspace_is_the_default_when_no_email_project():
    user = User(id="u1", username="dmytro", email="d@example.com", project_names=["alpha", "dmytro"])

    result = await _authenticate(user)

    assert _attribution_default(result) == "dmytro"


def test_personal_workspace_names_include_username_workspace_in_idp_mode():
    from codemie.rest_api.security.user import personal_workspace_names

    user = User(id="u1", username="dmytro", email="d@example.com", project_names=["alpha", "dmytro"])

    with patch("codemie.rest_api.security.user.config.ENABLE_USER_MANAGEMENT", False):
        assert personal_workspace_names(user.email, user.username, user.project_names) == frozenset(
            {"d@example.com", "dmytro"}
        )


def test_personal_workspace_names_are_email_only_with_user_management():
    from codemie.rest_api.security.user import personal_workspace_names

    user = User(id="u1", username="dmytro", email="d@example.com", project_names=["alpha", "dmytro"])

    with patch("codemie.rest_api.security.user.config.ENABLE_USER_MANAGEMENT", True):
        assert personal_workspace_names(user.email, user.username, user.project_names) == frozenset({"d@example.com"})


@pytest.mark.asyncio
async def test_legacy_jwt_login_stores_the_claims_for_runs_without_a_request():
    """Triggers and their loopback calls read the owner's claims back from this snapshot (EPMCDME-16625 D7)."""
    user = User(id="u1", username="dmytro", email="d@example.com", project_names=["alpha"])
    idp = MagicMock()
    idp.validates_token = True
    idp.authenticate = AsyncMock(return_value=user)

    with (
        patch(
            "codemie.rest_api.security.user_providers.legacy_jwt.authentication_service.ensure_project_exists",
            new=AsyncMock(),
        ),
        patch(_REMEMBER, new=AsyncMock()) as remember,
    ):
        await LegacyJwtUserProvider().authenticate_and_load_user(MagicMock(), idp)

    remember.assert_awaited_once_with(user)


@pytest.mark.asyncio
async def test_header_only_local_sign_in_never_writes_the_snapshot():
    """AR-5: LocalIdp trusts a bare user-id header, so only a token-validating IdP may write the snapshot."""
    from codemie.rest_api.security.idp.local import LocalIdp

    user = User(id="u1", username="dmytro", email="d@example.com", project_names=[])
    idp = LocalIdp()

    with (
        patch.object(LocalIdp, "authenticate", new=AsyncMock(return_value=user)),
        patch(
            "codemie.rest_api.security.user_providers.legacy_jwt.authentication_service.ensure_project_exists",
            new=AsyncMock(),
        ),
        patch(_REMEMBER, new=AsyncMock()) as remember,
    ):
        await LegacyJwtUserProvider().authenticate_and_load_user(MagicMock(), idp)

    remember.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_idp_that_does_not_declare_token_validation_never_writes_the_snapshot():
    """Final review AR-5: a new IdP class writes snapshots only once it declares that it validates the token."""
    from codemie.rest_api.security.idp.base import BaseIdp

    user = User(id="u1", username="dmytro", email="d@example.com", project_names=["alpha"])

    class HeaderIdp(BaseIdp):
        async def authenticate(self, request):
            return user

        def get_session_cookie(self) -> str:
            return "session"

    with (
        patch(
            "codemie.rest_api.security.user_providers.legacy_jwt.authentication_service.ensure_project_exists",
            new=AsyncMock(),
        ),
        patch(_REMEMBER, new=AsyncMock()) as remember,
    ):
        await LegacyJwtUserProvider().authenticate_and_load_user(MagicMock(), HeaderIdp())

    remember.assert_not_awaited()


def test_token_validating_idps_declare_it():
    from codemie.enterprise.idp.dependencies import _wrap_enterprise_idp
    from codemie.rest_api.security.idp.jwks_validating import JwksValidatingIdp
    from codemie.rest_api.security.idp.local import LocalIdp

    assert JwksValidatingIdp.validates_token is True
    assert _wrap_enterprise_idp(MagicMock, "Test").validates_token is True
    assert LocalIdp.validates_token is False


@pytest.mark.parametrize(
    "user_management,jwks,warned",
    [(False, False, True), (False, True, False), (True, False, False)],
    ids=["idp-claims-without-jwks", "idp-claims-with-jwks", "user-management"],
)
def test_startup_warns_when_snapshots_rely_on_a_gateway(user_management, jwks, warned):
    """Code review CR-002: the signature-verification precondition cannot be checked, so startup says it."""
    from codemie.configs import config
    from codemie.enterprise.idp import dependencies

    with (
        patch.object(config, "ENABLE_USER_MANAGEMENT", user_management),
        patch.object(config, "JWKS_VALIDATION_ENABLED", jwks),
        patch.object(dependencies, "logger") as log,
    ):
        dependencies._warn_if_snapshots_rely_on_a_gateway()

    assert log.warning.called is warned


def test_derived_default_is_not_used_with_user_management():
    from codemie.service.budget.attribution_resolver import AssetKind, attribution_input_for_user

    user = User(id="u1", username="dmytro", email="d@example.com", project_names=["zeta", "alpha"])

    with patch("codemie.service.budget.attribution_resolver.config.ENABLE_USER_MANAGEMENT", True):
        assert attribution_input_for_user(user, kind=AssetKind.NO_ASSET).default_project is None


@pytest.mark.parametrize("management", [False, True])
def test_credentials_project_ignores_the_derived_default(management):
    """Row 18: a marketplace non-member's own key and DIAL settings are looked up as before (the email)."""
    from codemie.service.llm_service.utils import _credentials_project

    user = User(id="u1", username="dmytro", email="d@example.com", project_names=["zeta"])
    asset = MagicMock(project="market", is_global=True)

    with patch("codemie.rest_api.security.user.config.ENABLE_USER_MANAGEMENT", management):
        assert _credentials_project(asset, None, user) == "d@example.com"
