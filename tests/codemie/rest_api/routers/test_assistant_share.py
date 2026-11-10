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

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.main import app
from codemie.rest_api.security.user import User


def _auth_user(user_id="owner-1"):
    u = MagicMock(spec=User)
    u.id = user_id
    u.username = "owner"
    u.name = "Owner"
    return u


@pytest.fixture
def client():
    from codemie.rest_api.routers.assistant import management as assistant_router

    app.dependency_overrides[assistant_router.authenticate] = lambda: _auth_user()
    with patch("codemie.rest_api.routers.assistant.management.config.ENABLE_USER_MANAGEMENT", True):
        yield TestClient(app)
    app.dependency_overrides = {}


_FIND = "codemie.rest_api.routers.assistant.management.Assistant.find_by_id"
_SVC = "codemie.rest_api.routers.assistant.management.AssistantShareService"

# (method, url, json body, service method the route must call)
_ROUTES = [
    ("GET", "/v1/assistants/asst-1/shared-with", None, "list_for_owner"),
    ("POST", "/v1/assistants/asst-1/share", {"user_ids": ["u1"]}, "grant"),
    ("DELETE", "/v1/assistants/asst-1/share", {"user_ids": ["u1"]}, "revoke"),
    ("GET", "/v1/assistants/asst-1/sharable-users?q=bo", None, "search_sharable_users_for_owner"),
]


def test_get_shared_with_returns_list(client):
    with (
        patch(
            f"{_SVC}.list_for_owner",
            return_value=[{"id": "u1", "username": "alice", "email": "a@b.com", "name": "Alice", "picture": None}],
        ),
        patch(_FIND, return_value=MagicMock(id="asst-1")),
    ):
        resp = client.get("/v1/assistants/asst-1/shared-with")
    assert resp.status_code == 200
    assert "shared_with" in resp.json()


def test_post_share_non_owner_returns_403(client):
    with (
        patch(_FIND, return_value=MagicMock()),
        patch(f"{_SVC}.grant", side_effect=ExtendedHTTPException(code=403, message="forbidden")),
    ):
        resp = client.post("/v1/assistants/asst-1/share", json={"user_ids": ["u1"]})
    assert resp.status_code == 403


def test_delete_share_revokes_access(client):
    with (
        patch(_FIND, return_value=MagicMock(id="asst-1")),
        patch(f"{_SVC}.revoke", return_value=[]),
    ):
        resp = client.request("DELETE", "/v1/assistants/asst-1/share", json={"user_ids": ["u1"]})
    assert resp.status_code == 200
    assert resp.json()["shared_with"] == []


def test_sharable_users_filters_internal_only(client):
    with (
        patch(_FIND, return_value=MagicMock(id="asst-1")),
        patch(
            f"{_SVC}.search_sharable_users_for_owner",
            new_callable=AsyncMock,
            return_value=[{"id": "u2", "username": "bob", "email": "b@b.com", "name": "Bob", "picture": None}],
        ),
    ):
        resp = client.get("/v1/assistants/asst-1/sharable-users?q=bo")
    assert resp.status_code == 200
    data = resp.json()
    assert "users" in data
    assert data["users"][0]["username"] == "bob"


@pytest.mark.parametrize("method,url,body,svc_method", _ROUTES)
def test_share_routes_return_400_when_user_management_disabled(client, method, url, body, svc_method):
    with (
        patch("codemie.rest_api.routers.assistant.management.config.ENABLE_USER_MANAGEMENT", False),
        patch(_FIND) as find,
        patch(f"{_SVC}.{svc_method}") as svc,
    ):
        resp = client.request(method, url, json=body)
    assert resp.status_code == 400
    assert "User management not enabled" in resp.text
    find.assert_not_called()
    svc.assert_not_called()


@pytest.mark.parametrize("method,url,body,svc_method", _ROUTES)
def test_share_routes_return_404_for_unknown_assistant(client, method, url, body, svc_method):
    with patch(_FIND, return_value=None), patch(f"{_SVC}.{svc_method}") as svc:
        resp = client.request(method, url, json=body)
    assert resp.status_code == 404
    svc.assert_not_called()


def test_sharable_users_rejects_one_char_query(client):
    with patch(_FIND, return_value=MagicMock()), patch(f"{_SVC}.search_sharable_users_for_owner") as svc:
        resp = client.get("/v1/assistants/asst-1/sharable-users?q=b")
    assert resp.status_code == 422
    svc.assert_not_called()


def test_sharable_users_rejects_omitted_query(client):
    with patch(_FIND, return_value=MagicMock()), patch(f"{_SVC}.search_sharable_users_for_owner") as svc:
        resp = client.get("/v1/assistants/asst-1/sharable-users")
    assert resp.status_code == 422
    svc.assert_not_called()


# ---- personal-integration guard on update ----

_UPD = "codemie.rest_api.routers.assistant.management"
_SETTINGS = "codemie.service.assistant.assistant_share_service.Settings.get_by_ids"


def _request_with_integration():
    from codemie.rest_api.models.assistant import AssistantRequest, ToolDetails, ToolKitDetails
    from codemie.rest_api.models.settings import SettingsBase

    tool = ToolDetails(name="t1", settings=SettingsBase.model_construct(id="s1"))
    return AssistantRequest(
        name="a",
        system_prompt="p",
        llm_model_type="m",
        toolkits=[ToolKitDetails(toolkit="tk", tools=[tool])],
        skip_integration_validation=True,
    )


def _run_update(request, repo, *, has_grants, setting_type):
    from codemie.rest_api.models.settings import SettingType
    from codemie.rest_api.routers.assistant.management import update_assistant

    assistant = MagicMock(id="asst-1", is_global=False, context=[], mcp_servers=[])
    stored = [MagicMock(setting_type=SettingType(setting_type))]
    with (
        patch(f"{_UPD}.project_access_check"),
        patch(f"{_UPD}.get_assistant_by_id_or_raise", return_value=assistant),
        patch(f"{_UPD}.check_user_can_access_assistant"),
        patch(f"{_UPD}._validate_remote_entities_and_raise"),
        patch(f"{_UPD}.category_service"),
        patch(f"{_UPD}.GuardrailService"),
        patch(f"{_UPD}._track_assistant_management_metric"),
        patch(f"{_UPD}.AssistantRepository", return_value=repo),
        patch("codemie.service.assistant.assistant_share_service.config.ENABLE_USER_MANAGEMENT", True),
        patch(f"{_SVC}.has_named_grants", return_value=has_grants),
        patch(_SETTINGS, return_value=stored),
    ):
        update_assistant("asst-1", request, background_tasks=MagicMock(), user=_auth_user())


def test_update_with_grants_and_personal_integration_returns_400_and_does_not_persist():
    repo = MagicMock()
    with pytest.raises(ExtendedHTTPException) as exc:
        _run_update(_request_with_integration(), repo, has_grants=True, setting_type="user")
    assert exc.value.code == 400
    assert "personal integrations" in exc.value.message.lower()
    assert "sharing" in exc.value.message
    repo.update.assert_not_called()


@pytest.mark.parametrize(
    "has_grants,setting_type",
    [(False, "user"), (True, "project"), (False, "project")],
)
def test_update_allowed_without_grants_or_with_only_project_integrations(has_grants, setting_type):
    repo = MagicMock()
    _run_update(_request_with_integration(), repo, has_grants=has_grants, setting_type=setting_type)
    repo.update.assert_called_once()


def test_create_assistant_is_not_guarded_by_share_checks():
    import inspect

    from codemie.rest_api.routers.assistant import management as mod

    assert "has_named_grants" not in inspect.getsource(mod.create_assistant)


# ---- seam: the central assistant access check reads preloaded grants (fail closed without them) --------------------


def _grantee_and_private_assistant():
    from codemie.rest_api.models.assistant import Assistant

    assistant = Assistant(
        id="00000000-0000-0000-0000-0000000000a1",
        name="n",
        description="d",
        system_prompt="s",
        project="other",
        shared=False,
        is_global=False,
        creator="owner",
    )
    user = User(id="grantee", username="grantee", project_names=[])
    user.is_admin = False
    return user, assistant


@pytest.mark.parametrize("granted", [True, False])
def test_access_check_admits_a_grantee_through_the_grants_ability_loads(granted):
    """No preload at the call site: ``Ability`` reads the user's assistant grants itself (finding 2)."""
    from codemie.core.ability import Action, PreloadedGrants
    from codemie.rest_api.routers.utils import check_user_can_access_assistant

    user, assistant = _grantee_and_private_assistant()
    grants = PreloadedGrants("Assistant", {assistant.id: Action.READ} if granted else {})
    with patch("codemie.core.ability.load_class_grants", return_value=grants) as load:
        if granted:
            check_user_can_access_assistant(user, assistant, "view", Action.READ)
        else:
            with pytest.raises(ExtendedHTTPException):
                check_user_can_access_assistant(user, assistant, "view", Action.READ)
    load.assert_called_once_with(user, "Assistant")


def test_access_check_for_write_reads_no_grants():
    from codemie.core.ability import Action
    from codemie.rest_api.routers.utils import check_user_can_access_assistant

    user, assistant = _grantee_and_private_assistant()
    with patch("codemie.core.ability.load_class_grants") as load:
        with pytest.raises(ExtendedHTTPException):
            check_user_can_access_assistant(user, assistant, "update", Action.WRITE)
    load.assert_not_called()


def test_shared_with_items_add_grant_source_and_keep_the_assistant_shape(client):
    listed = [{"id": "u1", "username": "u", "email": "e", "name": "U", "picture": None, "grant_source": "name"}]
    with patch(_FIND, return_value=MagicMock()), patch(f"{_SVC}.list_for_owner", return_value=listed):
        response = client.get("/v1/assistants/asst-1/shared-with")
    assert response.status_code == 200
    assert response.json() == {"shared_with": listed}
