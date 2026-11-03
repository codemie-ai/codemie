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

"""Destination resolver for CLI analytics evidence requests (EPMCDME-16440, spec 5 and 9)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from codemie.configs import config
from codemie.core.models import Application
from codemie.repository.cli_analytics.ports import TelemetryStorageUnavailableError
from codemie.rest_api.security.user import User
from codemie.service.analytics.cli_analytics_project_gate import DESTINATION_EVENTS, resolve_destination

MODULE = "codemie.service.analytics.cli_analytics_project_gate"
EMAIL = "u@example.com"
PERSONAL = Application.ProjectType.PERSONAL
SHARED = Application.ProjectType.SHARED


def make_user(**kwargs) -> User:
    kwargs.setdefault("project_names", ["Linked", "Registered", "Default", EMAIL])
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        return User(id="u-1", email=EMAIL, **kwargs)


def app(name, enabled=True, deleted=False, project_type=SHARED, created_by="someone") -> Application:
    return Application(
        id=name,
        name=name,
        cli_analytics_enabled=enabled,
        deleted_at=datetime.now() if deleted else None,
        project_type=project_type,
        created_by=created_by,
    )


class World:
    def __init__(self):
        self.projects = {name.lower(): app(name) for name in ("Linked", "Registered", "Default", "Other")}
        self.projects[EMAIL] = app(EMAIL, enabled=False, project_type=PERSONAL, created_by="u-1")
        self.registrations: list[str] = []
        self.ensure = AsyncMock(return_value=True)
        self.membership = AsyncMock(return_value=object())

    def remove(self, name):
        self.projects.pop(name.lower(), None)


@pytest.fixture
def world():
    w = World()

    @asynccontextmanager
    async def session():
        yield object()

    async def lookup(_session, name):
        found = w.projects.get(name.lower())
        return None if found is None or found.deleted_at is not None else found

    with (
        patch.object(config, "ENABLE_USER_MANAGEMENT", True),
        patch(f"{MODULE}.get_async_session", session),
        patch(f"{MODULE}.application_repository.aget_active_by_name_case_insensitive", side_effect=lookup),
        patch(f"{MODULE}.user_project_repository.aget_by_user_and_project", w.membership),
        patch(
            f"{MODULE}._registrations",
            side_effect=lambda _r: [SimpleNamespace(project_name=p) for p in w.registrations],
        ),
        patch(f"{MODULE}.personal_project_service.ensure_personal_project_async", w.ensure),
    ):
        yield w


async def resolve(user=None, *, subscription="corporate", header=None, source=None, remote="git@h:o/r"):
    return await resolve_destination(
        user or make_user(default_project="Default"),
        subscription_type=subscription,
        header_project=header,
        project_source=source,
        repo_remote=remote,
        client_subscription_type="corporate",
        client_policy_version="v1",
    )


# --- AC3: header project only with a source -------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["linked_folder", "profile"])
async def test_header_with_source_routes_there(world, source):
    decision = await resolve(header="linked", source=source)

    assert (decision.project, decision.project_origin, decision.route_note) == ("Linked", source, "")
    assert decision.reason is None
    assert (decision.subscription_type, decision.client_subscription_type, decision.client_policy_version) == (
        "corporate",
        "corporate",
        "v1",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("source", [None, "", "elsewhere"])
async def test_header_without_source_is_ignored(world, source):
    decision = await resolve(header="Linked", source=source, remote=None)

    assert (decision.project, decision.project_origin, decision.route_note) == (
        "Default",
        "default_project",
        "folder_not_linked",
    )


# --- AC4: link beats registration beats default ---------------------------------------------


@pytest.mark.asyncio
async def test_link_beats_registration(world):
    world.registrations = ["Registered"]
    assert (await resolve(header="Linked", source="linked_folder")).project == "Linked"


@pytest.mark.asyncio
async def test_registration_beats_default(world):
    world.registrations = ["Registered"]
    decision = await resolve()
    assert (decision.project, decision.project_origin, decision.route_note) == (
        "Registered",
        "registered_repository",
        "",
    )


@pytest.mark.asyncio
async def test_unavailable_link_falls_through_to_registration(world):
    world.registrations = ["Registered"]
    world.remove("Linked")
    decision = await resolve(header="Linked", source="profile")
    assert (decision.project, decision.route_note) == ("Registered", "")


@pytest.mark.asyncio
async def test_unavailable_link_without_registration_goes_to_default_with_note(world):
    world.membership.side_effect = lambda _s, _u, name: None if name == "Linked" else object()
    decision = await resolve(header="Linked", source="linked_folder")
    assert (decision.project, decision.project_origin, decision.route_note) == (
        "Default",
        "default_project",
        "linked_project_unavailable",
    )


# --- AC7: registration inaccessible / deleted / ambiguous -----------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("breakage", ["deleted", "no_access", "no_membership"])
async def test_inaccessible_registration_is_not_used(world, breakage):
    world.registrations = ["Registered"]
    user = make_user(default_project="Default")
    if breakage == "deleted":
        world.projects["registered"] = app("Registered", deleted=True)
    elif breakage == "no_access":
        user = make_user(default_project="Default", project_names=["Default"])
    else:
        world.membership.side_effect = lambda _s, _u, name: None if name == "Registered" else object()

    decision = await resolve(user)
    assert (decision.project, decision.route_note) == ("Default", "registered_project_inaccessible")


@pytest.mark.asyncio
async def test_ambiguous_registration_picks_nothing(world):
    world.registrations = ["Registered", "Other"]
    decision = await resolve()
    assert (decision.project, decision.route_note) == ("Default", "registration_ambiguous")


# --- AC8: no link, no registration ----------------------------------------------------------


@pytest.mark.asyncio
async def test_default_project_with_folder_not_linked(world):
    decision = await resolve(remote=None)
    assert (decision.project, decision.project_type, decision.project_origin, decision.route_note) == (
        "Default",
        "shared",
        "default_project",
        "folder_not_linked",
    )


@pytest.mark.asyncio
async def test_disabled_shared_destination_is_discarded(world):
    world.projects["default"] = app("Default", enabled=False)
    decision = await resolve()
    assert (decision.project, decision.reason) == (None, "cli_analytics_disabled")


# --- AC9, AC12: Personal-derived ------------------------------------------------------------


@pytest.mark.asyncio
async def test_personal_goes_to_owned_personal_project_ignoring_header(world):
    world.registrations = ["Registered"]
    decision = await resolve(subscription="personal", header="Linked", source="linked_folder")

    assert (decision.project, decision.project_type, decision.project_origin, decision.route_note) == (
        EMAIL,
        "personal",
        "personal",
        "",
    )
    assert decision.reason is None  # no cli_analytics_enabled needed
    world.ensure.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_personal_project_is_provisioned(world):
    created = app(EMAIL, project_type=PERSONAL, created_by="u-1", enabled=False)
    world.remove(EMAIL)

    async def ensure(user_id, email):
        world.projects[email] = created
        return True

    world.ensure.side_effect = ensure
    decision = await resolve(make_user(project_names=[]), subscription="personal")

    assert decision.project == EMAIL
    world.ensure.assert_awaited_once_with("u-1", EMAIL)


@pytest.mark.asyncio
@pytest.mark.parametrize("returns", [False, True])
async def test_personal_provisioning_failure_is_503(world, returns):
    world.remove(EMAIL)
    world.ensure.return_value = returns  # True but still absent counts as failure too
    with pytest.raises(TelemetryStorageUnavailableError, match="personal_destination_unavailable"):
        await resolve(subscription="personal")


@pytest.mark.asyncio
async def test_personal_name_held_by_a_shared_project_never_lands_there(world):
    world.projects[EMAIL] = app(EMAIL, project_type=SHARED)
    world.ensure.return_value = False
    with pytest.raises(TelemetryStorageUnavailableError):
        await resolve(subscription="personal")


@pytest.mark.asyncio
async def test_excluded_user_type_personal_goes_to_a_work_project_and_stays_personal_paid(world):
    """External / service-account users have no personal project (EPMCDME-14413): Personal-paid usage is routed
    like any other (here the linked folder), keeps its billing class and provisions nothing."""
    with patch(f"{MODULE}.is_personal_project_excluded", return_value=True):
        decision = await resolve(subscription="personal", header="Linked", source="linked_folder")
    assert (decision.project, decision.subscription_type, decision.project_origin) == (
        "Linked",
        "personal",
        "linked_folder",
    )
    assert decision.reason is None
    world.ensure.assert_not_awaited()


@pytest.mark.asyncio
async def test_excluded_user_type_personal_without_a_work_project_is_retryable(world):
    """AC9: no usable shared destination is a 503 retry, never a discard and never a personal project."""
    with patch(f"{MODULE}.is_personal_project_excluded", return_value=True):
        with pytest.raises(TelemetryStorageUnavailableError, match="default_project_unavailable"):
            await resolve(make_user(project_names=[]), subscription="personal", remote=None)
    world.ensure.assert_not_awaited()


@pytest.mark.asyncio
async def test_excluded_user_type_personal_on_a_disabled_work_project_is_the_shared_discard(world):
    world.projects["linked"].cli_analytics_enabled = False
    with patch(f"{MODULE}.is_personal_project_excluded", return_value=True):
        decision = await resolve(subscription="personal", header="Linked", source="linked_folder")
    assert (decision.project, decision.reason) == (None, "cli_analytics_disabled")


@pytest.mark.asyncio
async def test_personal_lookup_error_is_503(world):
    with patch(f"{MODULE}.application_repository.aget_active_by_name_case_insensitive", side_effect=RuntimeError("db")):
        with pytest.raises(TelemetryStorageUnavailableError):
            await resolve(subscription="personal")


# --- AC13: default failures -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_projects_is_a_retryable_503_never_a_drop(world):
    """AC9: no destination yet (an admin may add the user to a project) is kept pending, not discarded."""
    before = DESTINATION_EVENTS["default_project_unavailable:retry"]
    with pytest.raises(TelemetryStorageUnavailableError, match="default_project_unavailable"):
        await resolve(make_user(project_names=[]), remote=None)
    assert DESTINATION_EVENTS["default_project_unavailable:retry"] == before + 1
    world.ensure.assert_not_awaited()  # and no personal or shared project is invented for it


@pytest.mark.asyncio
@pytest.mark.parametrize("breakage", ["deleted", "missing", "no_access", "no_membership"])
async def test_broken_default_is_retryable_503_never_rerouted(world, breakage):
    user = make_user(default_project="Default")
    if breakage == "deleted":
        world.projects["default"] = app("Default", deleted=True)
    elif breakage == "missing":
        world.remove("Default")
    elif breakage == "no_access":
        user = make_user(default_project="Default", project_names=["Other"])
    else:
        world.membership.side_effect = lambda _s, _u, name: None if name == "Default" else object()

    with pytest.raises(TelemetryStorageUnavailableError, match="default_project_unavailable"):
        await resolve(user, remote=None)


@pytest.mark.asyncio
async def test_broken_default_never_affects_personal(world):
    world.remove("Default")
    assert (await resolve(subscription="personal")).project == EMAIL


@pytest.mark.asyncio
async def test_policy_lookup_error_is_503(world):
    with patch(f"{MODULE}.application_repository.aget_active_by_name_case_insensitive", side_effect=RuntimeError("db")):
        with pytest.raises(TelemetryStorageUnavailableError):
            await resolve()


# --- AC14: default is the owner's personal project ------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("subscription", ["corporate", "codemie", "unknown"])
async def test_default_is_personal_is_stored_there_without_flag(world, subscription):
    user = make_user(project_names=["Other", EMAIL])  # no stored default -> personal by email
    decision = await resolve(user, subscription=subscription, remote=None)

    assert (decision.project, decision.project_type, decision.project_origin, decision.route_note) == (
        EMAIL,
        "personal",
        "default_project",
        "folder_not_linked",
    )
    assert decision.reason is None


@pytest.mark.asyncio
async def test_another_users_personal_project_cannot_be_targeted(world):
    # An admin passes every access check, yet another user's personal project is never a destination.
    other = make_user(project_names=[EMAIL], default_project=EMAIL, is_admin=True)
    other = other.model_copy(update={"id": "u-2", "email": "x@example.com"})

    with pytest.raises(TelemetryStorageUnavailableError, match="default_project_unavailable"):
        await resolve(other, header=EMAIL, source="linked_folder", remote=None)


# --- Excluded user types: an old personal project is no destination for any classification --------------


@pytest.fixture
def external():
    with patch(f"{MODULE}.is_personal_project_excluded", return_value=True):
        yield make_user(default_project="Default")


@pytest.mark.asyncio
@pytest.mark.parametrize("subscription", ["personal", "corporate", "codemie", "unknown"])
async def test_excluded_user_header_naming_the_old_personal_project_falls_through(world, external, subscription):
    decision = await resolve(external, subscription=subscription, header=EMAIL, source="linked_folder", remote=None)

    assert (decision.project, decision.project_origin, decision.route_note) == (
        "Default",
        "default_project",
        "linked_project_unavailable",
    )
    world.ensure.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("subscription", ["personal", "corporate", "codemie", "unknown"])
async def test_excluded_user_registration_on_the_old_personal_project_falls_through(world, external, subscription):
    world.registrations = [EMAIL]

    decision = await resolve(external, subscription=subscription)

    assert (decision.project, decision.project_origin) == ("Default", "default_project")
    assert decision.route_note == "registered_project_inaccessible"


@pytest.mark.asyncio
@pytest.mark.parametrize("subscription", ["personal", "corporate", "codemie", "unknown"])
async def test_excluded_user_default_on_the_old_personal_project_is_retryable_not_stored(world, subscription):
    """No usable NEW destination: retry, with no personal project stored into, created or substituted."""
    with patch(f"{MODULE}.is_personal_project_excluded", return_value=True):
        user = make_user(default_project=EMAIL)
        with pytest.raises(TelemetryStorageUnavailableError, match="default_project_unavailable"):
            await resolve(user, subscription=subscription, remote=None)
    world.ensure.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("subscription", ["corporate", "codemie", "unknown"])
async def test_ordinary_user_keeps_a_personal_default_for_non_personal_sessions(world, subscription):
    """The restriction is for excluded user types only (test_default_is_personal_is_stored_there_without_flag)."""
    decision = await resolve(make_user(default_project=EMAIL), subscription=subscription, remote=None)

    assert decision.project == EMAIL
