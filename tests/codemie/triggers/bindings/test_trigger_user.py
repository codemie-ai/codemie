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

"""Trigger owners are resolved with their real memberships and default project (EPMCDME-16625 D7)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request

from codemie.rest_api.models.user_management import IdpUserSnapshot
from codemie.rest_api.security.idp.local import USER_ID_HEADER, LocalIdp
from codemie.service.budget.attribution_resolver import AssetKind, attribution_input_for_user, resolve_attribution
from codemie.triggers.bindings.utils import resolve_trigger_user

_SESSION = "codemie.clients.postgres.get_session"
_USERS = "codemie.repository.user_repository.user_repository"
_PROJECTS = "codemie.repository.user_project_repository.user_project_repository"
_KBS = "codemie.repository.user_kb_repository.user_kb_repository"
_SNAPSHOTS = "codemie.repository.idp_user_snapshot_repository.idp_user_snapshot_repository"
_USER_MANAGEMENT = "codemie.triggers.bindings.utils.config.ENABLE_USER_MANAGEMENT"
_ENV = "codemie.triggers.bindings.utils.config.ENV"


@pytest.fixture(autouse=True)
def _not_local():
    with patch(_ENV, "dev"):
        yield


def _session():
    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    return session


def _db_user(name="Carol"):
    return SimpleNamespace(
        id="u1",
        username="carol",
        name=name,
        email="carol@example.com",
        picture="",
        user_type="regular",
        is_admin=False,
        is_maintainer=False,
        is_auditor=False,
        project_limit=None,
    )


def _snapshot(hours_ago: float = 1) -> IdpUserSnapshot:
    return IdpUserSnapshot(
        update_date=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=hours_ago),
        id="u1",
        username="carol",
        email="carol@example.com",
        name="Carol",
        project_names=["team-a", "carol@example.com"],
        admin_project_names=["team-a"],
    )


def test_resolve_trigger_user_loads_memberships_and_default():
    rows = [
        SimpleNamespace(project_name="team-a", is_default=False, is_project_admin=True),
        SimpleNamespace(project_name="team-d", is_default=True, is_project_admin=False),
    ]

    with (
        patch(_SESSION, return_value=_session()),
        patch(_USERS) as users,
        patch(_PROJECTS) as projects,
        patch(_KBS) as kbs,
    ):
        users.get_active_by_id.return_value = _db_user()
        projects.get_by_user_id.return_value = rows
        kbs.get_by_user_id.return_value = [SimpleNamespace(kb_name="kb-1")]
        user = resolve_trigger_user("u1")

    assert user.email == "carol@example.com"
    assert user.project_names == ["team-a", "team-d"]
    assert user.admin_project_names == ["team-a"]
    assert user.default_project == "team-d"
    assert user.knowledge_bases == ["kb-1"]


def test_resolve_trigger_user_without_assigned_default_derives_it():
    rows = [
        SimpleNamespace(project_name="zeta", is_default=False, is_project_admin=False),
        SimpleNamespace(project_name="carol@example.com", is_default=False, is_project_admin=False),
    ]

    with patch(_SESSION, return_value=_session()), patch(_USERS) as users, patch(_PROJECTS) as projects, patch(_KBS):
        users.get_active_by_id.return_value = _db_user(name="")
        projects.get_by_user_id.return_value = rows
        user = resolve_trigger_user("u1")

    assert user.default_project == "carol@example.com"


def test_resolve_trigger_user_db_failure_returns_a_stand_in_without_memberships():
    with patch(_USER_MANAGEMENT, False), patch(_SESSION, side_effect=RuntimeError("db down")):
        user = resolve_trigger_user("u1")

    assert user.id == "u1"
    assert user.default_project is None
    assert user.project_names == []


def test_idp_claims_owner_is_rebuilt_from_their_last_login_without_admin_rights():
    """IDP-claims mode keeps no users rows; the claims snapshot supplies memberships (D7, D8), never project-admin
    rights (AR-5)."""
    with (
        patch(_SESSION, return_value=_session()),
        patch(_USERS) as users,
        patch(_USER_MANAGEMENT, False),
        patch(_SNAPSHOTS) as snapshots,
    ):
        users.get_active_by_id.return_value = None
        users.get_by_id.return_value = None
        snapshots.get.return_value = _snapshot()
        user = resolve_trigger_user("u1")

    snapshots.get.assert_called_once()
    assert user.username == "carol"
    assert user.project_names == ["team-a", "carol@example.com"]
    assert user.admin_project_names == []
    assert not user.is_application_admin("team-a")


def test_deactivated_owner_is_skipped_with_user_management():
    """With user management a row without an active user is a deactivated owner; an old snapshot must not revive
    them (EPMCDME-13928)."""
    with (
        patch(_SESSION, return_value=_session()),
        patch(_USERS) as users,
        patch(_USER_MANAGEMENT, True),
        patch(_SNAPSHOTS) as snapshots,
    ):
        users.get_active_by_id.return_value = None
        users.get_by_id.return_value = SimpleNamespace(id="u1")
        user = resolve_trigger_user("u1")

    snapshots.get.assert_not_called()
    assert user is None


def test_idp_claims_owner_with_a_stale_snapshot_keeps_their_identity_without_memberships():
    """Final review AR-1: past the trust window the owner is charged to their own personal budget (username carol),
    not to a customer named after the id; memberships are no longer replayed, and no stand-in is counted."""
    with (
        patch(_SESSION, return_value=_session()),
        patch(_USERS) as users,
        patch(_USER_MANAGEMENT, False),
        patch(_SNAPSHOTS) as snapshots,
        patch("codemie.rest_api.security.user.logger") as log,
    ):
        users.get_active_by_id.return_value = None
        users.get_by_id.return_value = None
        snapshots.get.return_value = _snapshot(hours_ago=25)
        user = resolve_trigger_user("u1")

    assert (user.id, user.username, user.email) == ("u1", "carol", "carol@example.com")
    assert user.project_names == []
    assert user.admin_project_names == []
    log.info.assert_not_called()


def test_idp_claims_owner_past_the_identity_retention_is_a_stand_in_without_memberships():
    """AR-3: the stand-in has no ['demo'] membership, and each use is logged so it can be counted."""
    with (
        patch(_SESSION, return_value=_session()),
        patch(_USERS) as users,
        patch(_USER_MANAGEMENT, False),
        patch(_SNAPSHOTS) as snapshots,
        patch("codemie.rest_api.security.user.logger") as log,
    ):
        users.get_active_by_id.return_value = None
        users.get_by_id.return_value = None
        snapshots.get.return_value = _snapshot(hours_ago=24 * 31)
        user = resolve_trigger_user("u1")

    assert (user.id, user.username, user.name) == ("u1", "u1", "u1")
    assert user.project_names == []
    assert user.admin_project_names == []
    log.info.assert_called_once_with("budget_event=stand_in_owner component=trigger_owner user_id='u1'")


def test_stand_in_owner_is_charged_personal_in_idp_claims_mode():
    """AR-3: an owner who cannot be loaded is no longer billed to 'demo'. With no memberships the walk goes to the
    personal budget without probing a project, also for an asset in a project."""
    with patch(_SESSION, side_effect=RuntimeError("db down")), patch(_USER_MANAGEMENT, False):
        owner = resolve_trigger_user("u1")

    probed = []

    def is_funded(project: str) -> bool:
        probed.append(project)
        return True

    with patch("codemie.service.budget.attribution_resolver.config.ENABLE_USER_MANAGEMENT", False):
        for kind, asset_project in ((AssetKind.PROJECT_ASSET, "team-a"), (AssetKind.NO_ASSET, None)):
            inp = attribution_input_for_user(owner, kind=kind, asset_project=asset_project)
            decision = resolve_attribution(inp, is_funded)

            assert inp.default_project is None
            assert decision.charged_project is None
    assert probed == []


def test_checking_an_owner_does_not_build_or_log_a_stand_in():
    """AR-10: the activity check before a run must not count a stand-in the run itself counts again."""
    from codemie.triggers.bindings.utils import is_trigger_owner_active

    with (
        patch(_SESSION, return_value=_session()),
        patch(_USERS) as users,
        patch(_USER_MANAGEMENT, False),
        patch(_SNAPSHOTS) as snapshots,
        patch("codemie.rest_api.security.user.logger") as log,
    ):
        users.get_active_by_id.return_value = None
        users.get_by_id.return_value = None
        snapshots.get.return_value = None
        active = is_trigger_owner_active("u1")

    assert active is True
    log.info.assert_not_called()


def test_a_deactivated_owner_is_not_active():
    from codemie.triggers.bindings.utils import is_trigger_owner_active

    with patch(_SESSION, return_value=_session()), patch(_USERS) as users, patch(_USER_MANAGEMENT, True):
        users.get_active_by_id.return_value = None
        users.get_by_id.return_value = SimpleNamespace(id="u1")
        assert is_trigger_owner_active("u1") is False


class _AsyncSession:
    async def __aenter__(self):
        return MagicMock()

    async def __aexit__(self, *_):
        pass


@pytest.mark.asyncio
async def test_webhook_started_workflow_callback_sees_the_owner_resolve_trigger_user_sees(mocker):
    """AR-10: a webhook-started workflow runs through a signed loopback call; LocalIdp loads that owner with the
    loader resolve_trigger_user uses, so both see the same memberships and default."""
    from codemie.service.user import user_by_id_loader

    mocker.patch(_USER_MANAGEMENT, False)
    mocker.patch("codemie.rest_api.security.idp.local.config.ENV", "dev")
    mocker.patch("codemie.clients.postgres.get_async_session", return_value=_AsyncSession())
    mocker.patch(_SESSION, return_value=_session())
    mocker.patch(f"{_USERS}.get_active_by_id", return_value=None)
    mocker.patch(f"{_USERS}.get_by_id", return_value=None)
    mocker.patch(f"{_USERS}.aget_active_by_id", new=AsyncMock(return_value=None))
    mocker.patch(f"{_USERS}.aget_by_id", new=AsyncMock(return_value=None))
    mocker.patch(f"{_SNAPSHOTS}.get", return_value=_snapshot())
    mocker.patch(f"{_SNAPSHOTS}.aget", new=AsyncMock(return_value=_snapshot()))
    aload = mocker.spy(user_by_id_loader, "aload_user_by_id")
    request = Request(scope={"type": "http", "method": "POST", "headers": [(USER_ID_HEADER.encode(), b"u1")]})

    callback_user = await LocalIdp().authenticate(request, signed_loopback=True)
    run_user = resolve_trigger_user("u1")

    assert aload.call_args.kwargs == {"with_snapshot": True}
    assert callback_user.model_dump(exclude={"auth_token"}) == run_user.model_dump(exclude={"auth_token"})
    assert callback_user.project_names == ["team-a", "carol@example.com"]
