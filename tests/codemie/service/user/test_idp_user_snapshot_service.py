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

"""IDP-claims mode keeps each user's last claims for runs that start without a request (EPMCDME-16625 D7, D8)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.rest_api.models.user_management import IdpUserSnapshot
from codemie.rest_api.security.user import User
from codemie.service.user import idp_user_snapshot_service as module
from codemie.service.user.idp_user_snapshot_service import (
    current_user_from_snapshot,
    idp_user_snapshot_service,
    user_from_snapshot,
)

_UPSERT = "codemie.repository.idp_user_snapshot_repository.idp_user_snapshot_repository.aupsert"
_PRUNE = "codemie.repository.idp_user_snapshot_repository.idp_user_snapshot_repository.adelete_last_seen_before"


class _AsyncSession:
    def __init__(self):
        self.session = MagicMock(commit=AsyncMock())

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_):
        return False


@pytest.fixture(autouse=True)
def _fresh_write_cache():
    module._written.clear()
    module._last_prune = None
    yield
    module._written.clear()
    module._last_prune = None


@pytest.fixture(autouse=True)
def prune():
    with patch(_PRUNE, new=AsyncMock(return_value=0)) as delete:
        yield delete


def _user(projects=("team-a",)):
    return User(id="u1", username="carol", email="carol@example.com", name="Carol", project_names=list(projects))


@pytest.mark.asyncio
async def test_claims_are_written_once_while_unchanged():
    with (
        patch("codemie.clients.postgres.get_async_session", side_effect=lambda: _AsyncSession()),
        patch(_UPSERT, new=AsyncMock()) as upsert,
    ):
        await idp_user_snapshot_service.remember(_user())
        await idp_user_snapshot_service.remember(_user())

    upsert.assert_awaited_once()
    assert upsert.await_args.kwargs["project_names"] == ["team-a"]
    assert upsert.await_args.kwargs["username"] == "carol"


@pytest.mark.asyncio
async def test_project_admin_claims_are_not_stored():
    """AR-5: snapshots never grant project-admin rights, so they do not keep them either."""
    user = _user(("team-a",))
    user.admin_project_names = ["team-a"]

    with (
        patch("codemie.clients.postgres.get_async_session", side_effect=lambda: _AsyncSession()),
        patch(_UPSERT, new=AsyncMock()) as upsert,
    ):
        await idp_user_snapshot_service.remember(user)

    assert upsert.await_args.kwargs["admin_project_names"] == []


def test_snapshot_user_has_memberships_but_no_project_admin_rights():
    snapshot = IdpUserSnapshot(
        id="u1", username="carol", project_names=["team-a"], admin_project_names=["team-a"], update_date=None
    )

    user = user_from_snapshot(snapshot)

    assert user.project_names == ["team-a"]
    assert user.admin_project_names == []
    assert not user.is_application_admin("team-a")


@pytest.mark.asyncio
async def test_writes_prune_rows_past_the_identity_retention_at_most_hourly(prune):
    """AR-9: rows older than IDP_USER_SNAPSHOT_IDENTITY_RETENTION_DAYS are never read again, so a write deletes
    them, throttled per pod."""
    with (
        patch("codemie.clients.postgres.get_async_session", side_effect=lambda: _AsyncSession()),
        patch(_UPSERT, new=AsyncMock()),
        patch("codemie.service.user.idp_user_snapshot_service.config.IDP_USER_SNAPSHOT_MAX_AGE_HOURS", 24),
        patch("codemie.service.user.idp_user_snapshot_service.config.IDP_USER_SNAPSHOT_IDENTITY_RETENTION_DAYS", 30),
    ):
        await idp_user_snapshot_service.remember(_user(("team-a",)))
        await idp_user_snapshot_service.remember(_user(("team-b",)))
        prune.assert_awaited_once()
        cutoff = prune.await_args.args[1]
        expected = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=30)
        assert abs((cutoff - expected).total_seconds()) < 60

        module._last_prune -= module._PRUNE_SECONDS
        await idp_user_snapshot_service.remember(_user(("team-c",)))

    assert prune.await_count == 2


@pytest.mark.asyncio
async def test_a_failed_prune_never_fails_login(prune):
    prune.side_effect = RuntimeError("db down")

    with (
        patch("codemie.clients.postgres.get_async_session", side_effect=lambda: _AsyncSession()),
        patch(_UPSERT, new=AsyncMock()) as upsert,
    ):
        await idp_user_snapshot_service.remember(_user())

    upsert.assert_awaited_once()
    prune.assert_awaited_once()


@pytest.mark.asyncio
async def test_changed_claims_are_written_again():
    with (
        patch("codemie.clients.postgres.get_async_session", side_effect=lambda: _AsyncSession()),
        patch(_UPSERT, new=AsyncMock()) as upsert,
    ):
        await idp_user_snapshot_service.remember(_user(("team-a",)))
        await idp_user_snapshot_service.remember(_user(("team-a", "team-b")))

    assert upsert.await_count == 2
    assert upsert.await_args.kwargs["project_names"] == ["team-a", "team-b"]


@pytest.mark.asyncio
async def test_a_failed_write_never_fails_login_and_is_retried():
    with (
        patch("codemie.clients.postgres.get_async_session", side_effect=lambda: _AsyncSession()),
        patch(_UPSERT, new=AsyncMock(side_effect=[RuntimeError("db down"), None])) as upsert,
    ):
        await idp_user_snapshot_service.remember(_user())
        await idp_user_snapshot_service.remember(_user())

    assert upsert.await_count == 2


@pytest.mark.parametrize(
    "projects,expected",
    [
        (["zeta", "carol@example.com", "carol"], "carol@example.com"),
        (["zeta", "carol"], "carol"),
        (["zeta", "alpha"], "alpha"),
        ([], None),
    ],
    ids=["email-project", "username-workspace", "alphabetical", "no-projects"],
)
def test_snapshot_user_is_attributed_with_the_idp_default_project(projects, expected):
    snapshot = IdpUserSnapshot(
        id="u1", username="carol", email="carol@example.com", project_names=projects, admin_project_names=[]
    )

    user = user_from_snapshot(snapshot)

    from codemie.service.budget.attribution_resolver import AssetKind, attribution_input_for_user

    assert user.project_names == projects
    assert user.default_project is None
    with patch("codemie.service.budget.attribution_resolver.config.ENABLE_USER_MANAGEMENT", False):
        assert attribution_input_for_user(user, kind=AssetKind.NO_ASSET).default_project == expected


def _snapshot_seen(hours_ago: float | None) -> IdpUserSnapshot:
    seen = None if hours_ago is None else datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=hours_ago)
    return IdpUserSnapshot(
        id="u1", username="carol", email="carol@example.com", project_names=["team-a"], update_date=seen
    )


@pytest.mark.parametrize("hours_ago,usable", [(1, True), (23, True), (None, False)])
def test_only_a_recent_snapshot_is_used(hours_ago, usable):
    """A user who stopped logging in may have lost access; their memberships are trusted only for the max age."""
    with patch("codemie.service.user.idp_user_snapshot_service.config.IDP_USER_SNAPSHOT_MAX_AGE_HOURS", 24):
        user = current_user_from_snapshot(_snapshot_seen(hours_ago))

    assert (user is not None) is usable
    if usable:
        assert user.project_names == ["team-a"]


@pytest.mark.parametrize("hours_ago", [25, 24 * 29])
def test_a_stale_snapshot_keeps_the_identity_but_no_memberships(hours_ago):
    """Final review AR-1: the stale owner is charged to their own personal budget, not an id-named customer."""
    from codemie.service.budget.attribution_resolver import AssetKind, attribution_input_for_user

    with (
        patch("codemie.service.user.idp_user_snapshot_service.config.IDP_USER_SNAPSHOT_MAX_AGE_HOURS", 24),
        patch("codemie.service.user.idp_user_snapshot_service.config.IDP_USER_SNAPSHOT_IDENTITY_RETENTION_DAYS", 30),
    ):
        user = current_user_from_snapshot(_snapshot_seen(hours_ago))

    assert user.username == "carol"
    assert user.email == "carol@example.com"
    assert user.project_names == []
    assert user.admin_project_names == []
    with patch("codemie.service.budget.attribution_resolver.config.ENABLE_USER_MANAGEMENT", False):
        inp = attribution_input_for_user(user, kind=AssetKind.PROJECT_ASSET, asset_project="team-a")
    assert inp.member_projects == frozenset()
    assert inp.default_project is None


def test_a_snapshot_past_the_identity_retention_is_not_used():
    with (
        patch("codemie.service.user.idp_user_snapshot_service.config.IDP_USER_SNAPSHOT_MAX_AGE_HOURS", 24),
        patch("codemie.service.user.idp_user_snapshot_service.config.IDP_USER_SNAPSHOT_IDENTITY_RETENTION_DAYS", 30),
    ):
        assert current_user_from_snapshot(_snapshot_seen(24 * 31)) is None


def test_the_identity_retention_never_undercuts_the_trust_window():
    with (
        patch("codemie.service.user.idp_user_snapshot_service.config.IDP_USER_SNAPSHOT_MAX_AGE_HOURS", 72),
        patch("codemie.service.user.idp_user_snapshot_service.config.IDP_USER_SNAPSHOT_IDENTITY_RETENTION_DAYS", 1),
    ):
        user = current_user_from_snapshot(_snapshot_seen(48))

    assert user.project_names == ["team-a"]


def test_the_loader_ignores_a_snapshot_past_the_identity_retention():
    from codemie.service.user.user_by_id_loader import load_user_by_id

    with (
        patch("codemie.repository.user_repository.user_repository.get_active_by_id", return_value=None),
        patch("codemie.repository.user_repository.user_repository.get_by_id", return_value=None),
        patch(
            "codemie.repository.idp_user_snapshot_repository.idp_user_snapshot_repository.get",
            return_value=_snapshot_seen(24 * 31),
        ),
        patch("codemie.service.user.idp_user_snapshot_service.config.IDP_USER_SNAPSHOT_MAX_AGE_HOURS", 24),
        patch("codemie.service.user.user_by_id_loader.config.ENABLE_USER_MANAGEMENT", False),
    ):
        lookup = load_user_by_id(MagicMock(), "u1", with_snapshot=True)

    assert lookup.user is None
    assert lookup.has_row is False
