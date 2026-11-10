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

from datetime import datetime
from uuid import uuid4

import pytest

from codemie.service.hosted_applications.projections import app_projection
from tests.codemie.core.hosted_applications.test_access import PROJECT, make_app, make_user

MANAGER_ONLY = {"selected_version_id", "active_version_id", "lifecycle_state", "revision", "etag"}
# Capabilities come from ``user_abilities``/``has_active_version``; the suspend state only as ``suspended``.
REMOVED = {
    "capabilities",
    "audience",
    "discoverable",
    "publication_state",
    "can_manage",
    "can_launch",
    "security_state",
}
ALL = ["read", "write", "delete"]
READ = ["read"]

# persona -> (app kwargs, user_abilities, can_share, can_moderate)
CASES = {
    "creator": ({"shared": True}, ALL, True, False),
    "project_admin": ({"shared": True}, ALL, False, False),
    "platform_admin": ({"shared": True}, ALL, False, True),
    "maintainer": ({"shared": True}, ALL, False, True),
    "member": ({"shared": True}, READ, False, False),
    "outsider": ({"shared": True}, [], False, False),
    "service_account": ({"shared": True}, [], False, False),
    "external": ({"shared": True}, [], False, False),
}


def project(app, user, **kwargs):
    app.name, app.description, app.revision, app.lifecycle_state = "App", "d", 3, "active"
    app.created_at = app.updated_at = datetime(2026, 1, 1)
    app.selected_version_id, app.active_version_id = kwargs.pop("selected", None), kwargs.pop("active", None)
    return app_projection(app, user, **kwargs)


@pytest.mark.parametrize("persona", CASES)
def test_each_persona_gets_abilities_share_and_moderate_flags(persona):
    app_kwargs, abilities, can_share, can_moderate = CASES[persona]
    result = project(make_app(**app_kwargs), make_user(persona))
    assert result["user_abilities"] == abilities
    assert result["can_share"] is can_share
    assert result["can_moderate"] is can_moderate
    assert REMOVED.isdisjoint(result)


def test_marketplace_viewer_reads_a_global_app():
    result = project(make_app(is_global=True), make_user("outsider"))
    assert result["user_abilities"] == READ
    assert (result["is_global"], result["shared"], result["can_share"], result["can_moderate"]) == (
        True,
        False,
        False,
        False,
    )


def test_viewer_of_an_app_whose_creator_is_deactivated_is_a_plain_reader():
    app = make_app(creator_id="u-gone", shared=True)
    app.created_by.name = "Gone"
    result = project(app, make_user("member"), publisher="Gone")
    assert result["user_abilities"] == READ and result["can_share"] is False
    assert result["created_by"]["id"] == "u-gone"
    assert result["publisher"] == {"display_name": "Gone"}


def test_response_carries_project_sharing_and_creator():
    result = project(make_app(shared=True), make_user("creator"))
    assert result["project"] == PROJECT
    assert (result["shared"], result["is_global"]) == (True, False)
    assert result["created_by"] == {"id": "u-creator", "username": "u-creator", "name": "u-creator"}


@pytest.mark.parametrize("state, expected", [("clear", False), ("suspended", True)])
def test_suspended_reads_the_security_state_through_the_access_module(state, expected):
    result = project(make_app(security_state=state), make_user("platform_admin"))
    assert result["suspended"] is expected


def test_has_active_version_follows_the_active_version():
    user = make_user("creator")
    assert project(make_app(), user)["has_active_version"] is False
    assert project(make_app(), user, active=uuid4())["has_active_version"] is True


def test_managers_see_manager_fields_and_viewers_do_not():
    version = uuid4()
    for persona in ("creator", "project_admin", "platform_admin", "maintainer"):
        result = project(make_app(shared=True), make_user(persona), selected=version)
        assert set(result) >= MANAGER_ONLY, persona
        assert result["selected_version_id"] == str(version)
        assert "write" in result["user_abilities"] and REMOVED.isdisjoint(result)
    for persona in ("member",):
        result = project(make_app(shared=True), make_user(persona), selected=version)
        assert MANAGER_ONLY.isdisjoint(result) and "write" not in result["user_abilities"]


def test_cover_passes_through():
    assert project(make_app(shared=True), make_user("member"), has_cover=True)["has_cover"] is True


def test_manager_response_carries_the_suspend_state_only_as_suspended():
    result = project(make_app(security_state="suspended"), make_user("creator"))
    assert result["suspended"] is True and "security_state" not in result


@pytest.mark.parametrize("state, expected", [("clear", []), ("suspended", ["activate", "move", "publish", "share_on"])])
def test_suspension_refuses_projects_the_backend_refusal_list(state, expected):
    """UI AR-8 / AR-12: clients read the refused actions from the server, from the one access-module constant."""
    from codemie.core.hosted_applications.access import SUSPEND_GATED_ACTIONS

    for persona in ("creator", "member", "platform_admin"):
        result = project(make_app(shared=True, security_state=state), make_user(persona))
        if persona == "member" and state == "suspended":
            continue  # a suspended app is not shown to a viewer at all
        assert result["suspension_refuses"] == expected
    assert sorted(SUSPEND_GATED_ACTIONS) == ["activate", "move", "publish", "share_on"]
