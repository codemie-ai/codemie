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

"""Gating, validation, error-mapping, pagination and batch-body cases for the override endpoints."""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from codemie.core.exceptions import ModelNotWhitelistedException, NotFoundException
from codemie.rest_api.models.user_model_override import UserModelOverride
from codemie.rest_api.main import app
from codemie.rest_api.routers.projects.user_model_overrides import MAX_ALLOWED_MODELS
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.rest_api.routers.projects._common import _authorize_project_access
from codemie.rest_api.security.authentication import authenticate
from codemie.service.user.user_model_override_service import BatchValidationError

SERVICE = "codemie.rest_api.routers.projects.user_model_overrides.UserModelOverrideService"
PROJECT = "test-project"
BASE = f"/v1/projects/{PROJECT}/user-model-overrides"
ADMIN_ID = "admin-1"


def _unauthorized():
    raise ExtendedHTTPException(code=404, message="Project not found")


@pytest.fixture
def session():
    return MagicMock()


@pytest.fixture
def make_client(session):
    def _make(is_admin: bool = True, is_platform_admin: bool = False) -> TestClient:
        user = MagicMock()
        user.id = ADMIN_ID
        app.dependency_overrides[authenticate] = lambda: user
        app.dependency_overrides[_authorize_project_access] = (
            (lambda: MagicMock(spec=Application)) if (is_admin or is_platform_admin) else _unauthorized
        )
        return TestClient(app)

    @contextmanager
    def fake_get_session():
        yield session

    with (
        patch("codemie.rest_api.routers.projects.user_model_overrides.customer_config") as feature_config,
        patch("codemie.rest_api.routers.projects.user_model_overrides.get_session", fake_get_session),
    ):
        feature_config.is_feature_enabled.return_value = True
        yield _make
    app.dependency_overrides.pop(authenticate, None)
    app.dependency_overrides.pop(_authorize_project_access, None)


def _set(client, payload):
    """PUT the override of payload["user_id"] with the rest of the payload as the body."""
    body = dict(payload)
    user_id = body.pop("user_id")
    return client.put(f"{BASE}/{user_id}", json=body)


def _override(**kwargs) -> MagicMock:
    override = MagicMock(spec=UserModelOverride)
    override.id = uuid4()
    override.user_id = str(uuid4())
    override.project_name = PROJECT
    override.allowed_models = ["gpt-4"]
    override.default_model = None
    override.date = None
    override.update_date = None
    for key, value in kwargs.items():
        setattr(override, key, value)
    return override


def test_get_list_non_admin(make_client):
    with patch(f"{SERVICE}.get_overrides_by_project") as mock_list:
        response = make_client(is_admin=False).get(BASE)

    assert response.status_code == 404
    mock_list.assert_not_called()


def test_get_single_non_admin(make_client):
    with patch(f"{SERVICE}.get_override") as mock_get:
        response = make_client(is_admin=False).get(f"{BASE}/{uuid4()}")

    assert response.status_code == 404
    mock_get.assert_not_called()


def test_delete_non_admin(make_client):
    with patch(f"{SERVICE}.delete_override") as mock_delete:
        response = make_client(is_admin=False).delete(f"{BASE}/{uuid4()}")

    assert response.status_code == 404
    mock_delete.assert_not_called()


def test_batch_non_admin(make_client):
    payload = {"operation": "grant", "user_ids": [str(uuid4())], "allowed_models": ["gpt-4"]}
    with patch(f"{SERVICE}.batch_set_overrides") as mock_batch:
        response = make_client(is_admin=False).post(f"{BASE}/batch", json=payload)

    assert response.status_code == 404
    mock_batch.assert_not_called()


def test_delete_success_returns_204(make_client, session):
    with patch(f"{SERVICE}.delete_override", return_value=_override()):
        response = make_client().delete(f"{BASE}/{uuid4()}")

    assert response.status_code == 204
    session.commit.assert_called_once()


def test_delete_not_found_returns_404(make_client, session):
    with patch(f"{SERVICE}.delete_override", return_value=None):
        response = make_client().delete(f"{BASE}/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Override not found"
    session.commit.assert_not_called()


def test_put_model_not_whitelisted_maps_to_400_and_rolls_back(make_client, session):
    payload = {"user_id": str(uuid4()), "allowed_models": ["bad"]}
    error = ModelNotWhitelistedException(
        "bad", PROJECT, details="default_model must be one of the override's allowed_models: ['gpt-4']"
    )

    with patch(f"{SERVICE}.set_override", side_effect=error):
        response = _set(make_client(), payload)

    assert response.status_code == 400
    assert response.json()["error"]["message"] == f"Model 'bad' is not allowed for project '{PROJECT}'"
    assert "default_model must be one of" in response.json()["error"]["details"]
    session.rollback.assert_called_once()


def test_put_project_or_user_not_found_maps_to_404(make_client):
    payload = {"user_id": str(uuid4()), "allowed_models": ["gpt-4"]}

    with patch(f"{SERVICE}.set_override", side_effect=NotFoundException("no project")):
        response = _set(make_client(), payload)

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "no project"


def test_put_value_error_maps_to_400(make_client):
    payload = {"user_id": str(uuid4()), "allowed_models": ["gpt-4"]}

    with patch(f"{SERVICE}.set_override", side_effect=ValueError("bad models")):
        response = _set(make_client(), payload)

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "bad models"


def test_put_integrity_error_maps_to_409(make_client, session):
    payload = {"user_id": str(uuid4()), "allowed_models": ["gpt-4"]}
    error = IntegrityError("insert", {}, Exception("duplicate"))

    with patch(f"{SERVICE}.set_override", side_effect=error):
        response = _set(make_client(), payload)

    assert response.status_code == 409
    session.rollback.assert_called_once()


@pytest.mark.parametrize("models", [[], [""], ["  "]])
def test_put_blank_or_empty_models_rejected_before_service(make_client, models):
    payload = {"user_id": str(uuid4()), "allowed_models": models}

    with patch(f"{SERVICE}.set_override") as mock_set:
        response = _set(make_client(), payload)

    assert response.status_code == 422
    mock_set.assert_not_called()


@pytest.mark.parametrize("query", ["skip=-1", "limit=0", "limit=501"])
def test_list_pagination_bounds(make_client, query):
    with patch(f"{SERVICE}.get_overrides_by_project", return_value=[]) as mock_list:
        response = make_client().get(f"{BASE}?{query}")

    assert response.status_code == 422
    mock_list.assert_not_called()


@pytest.mark.parametrize(
    "method, path, payload",
    [
        ("put", f"{BASE}/u1", {"allowed_models": ["gpt-4"]}),
        ("post", f"{BASE}/batch", {"operation": "revoke", "user_ids": ["u1"]}),
    ],
)
def test_feature_flag_off_returns_501_for_writes(make_client, method, path, payload):
    client = make_client()
    with patch("codemie.rest_api.routers.projects.user_model_overrides.customer_config") as feature_config:
        feature_config.is_feature_enabled.return_value = False
        response = getattr(client, method)(path, json=payload)

    assert response.status_code == 501
    assert response.json()["error"]["message"] == "User model overrides are not enabled on this platform"


def test_feature_flag_off_still_lists_reads_and_deletes(make_client, session):
    """Dormant rows must stay visible and removable while the feature is off."""
    uid = str(uuid4())
    client = make_client()

    with (
        patch("codemie.rest_api.routers.projects.user_model_overrides.customer_config") as feature_config,
        patch(f"{SERVICE}.get_overrides_by_project", return_value=[_override()]) as listed,
        patch(f"{SERVICE}.get_override", return_value=_override()) as one,
        patch(f"{SERVICE}.delete_override", return_value=_override()) as deleted,
    ):
        feature_config.is_feature_enabled.return_value = False
        assert client.get(BASE).status_code == 200
        assert client.get(f"{BASE}/{uid}").status_code == 200
        assert client.delete(f"{BASE}/{uid}").status_code == 204

    listed.assert_called_once()
    one.assert_called_once()
    deleted.assert_called_once()


def test_batch_grant_without_models_is_400(make_client):
    payload = {"operation": "grant", "user_ids": [str(uuid4())]}
    assert make_client().post(f"{BASE}/batch", json=payload).status_code == 400


def test_batch_empty_user_ids_is_400(make_client):
    payload = {"operation": "grant", "user_ids": [], "allowed_models": ["gpt-4"]}
    assert make_client().post(f"{BASE}/batch", json=payload).status_code == 400


def test_batch_unknown_operation_is_400(make_client):
    payload = {"operation": "purge", "user_ids": [str(uuid4())]}
    assert make_client().post(f"{BASE}/batch", json=payload).status_code == 400


def test_batch_dedupes_user_ids(make_client):
    uid = str(uuid4())
    payload = {"operation": "grant", "user_ids": [uid, uid], "allowed_models": ["gpt-4"]}

    with patch(f"{SERVICE}.batch_set_overrides", return_value=[]) as mock_batch:
        response = make_client().post(f"{BASE}/batch", json=payload)

    assert response.status_code == 200
    assert mock_batch.call_args.args[2] == [uid]


def test_batch_validation_failure_returns_the_standard_error_envelope(make_client, session):
    ids = [str(uuid4()), str(uuid4())]
    payload = {"operation": "grant", "user_ids": ids, "allowed_models": ["gpt-4"]}
    error = BatchValidationError(ids[1], "User not a member of this project")

    with patch(f"{SERVICE}.batch_set_overrides", side_effect=error):
        response = make_client().post(f"{BASE}/batch", json=payload)

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "message": "Batch operation failed; no users updated",
            "details": f"{ids[1]}: User not a member of this project",
            "help": None,
        }
    }
    session.rollback.assert_called_once()


def test_batch_validation_failure_keeps_the_model_error_reason(make_client):
    ids = [str(uuid4())]
    payload = {"operation": "grant", "user_ids": ids, "allowed_models": ["gpt-4"], "default_model": "bad"}
    error = ModelNotWhitelistedException("bad", PROJECT, details="default_model must be one of the allowed_models")

    with patch(f"{SERVICE}.batch_set_overrides", side_effect=error):
        response = make_client().post(f"{BASE}/batch", json=payload)

    assert response.status_code == 400
    assert response.json()["error"]["details"] == "default_model must be one of the allowed_models"


def test_batch_revoke_returns_deleted_records(make_client):
    deleted = _override()
    payload = {"operation": "revoke", "user_ids": [deleted.user_id]}

    with patch(f"{SERVICE}.batch_set_overrides", return_value=[deleted]):
        response = make_client().post(f"{BASE}/batch", json=payload)

    assert response.status_code == 200
    assert [r["user_id"] for r in response.json()] == [deleted.user_id]


def test_list_unknown_project_is_404(make_client):
    with patch(f"{SERVICE}.get_overrides_by_project", side_effect=NotFoundException("nope")):
        assert make_client().get(BASE).status_code == 404


@pytest.mark.parametrize("operation", ["grant", "revoke"])
def test_batch_unknown_project_is_404(make_client, session, operation):
    payload = {"operation": operation, "user_ids": [str(uuid4())], "allowed_models": ["gpt-4"]}
    with patch(f"{SERVICE}.batch_set_overrides", side_effect=NotFoundException("nope")):
        response = make_client().post(f"{BASE}/batch", json=payload)

    assert response.status_code == 404
    session.rollback.assert_called()


def test_put_user_not_in_project_is_400(make_client):
    payload = {"user_id": str(uuid4()), "allowed_models": ["gpt-4"]}
    with patch(f"{SERVICE}.set_override", side_effect=ValueError("not a member")):
        assert _set(make_client(), payload).status_code == 400


def test_batch_integrity_error_does_not_leak_the_sql(make_client, session):
    ids = [str(uuid4())]
    payload = {"operation": "grant", "user_ids": ids, "allowed_models": ["gpt-4"]}
    error = IntegrityError(
        "INSERT INTO user_model_overrides (user_id) VALUES (%(user_id)s)", {"user_id": ids[0]}, Exception("duplicate")
    )

    with patch(f"{SERVICE}.batch_set_overrides", side_effect=error):
        response = make_client().post(f"{BASE}/batch", json=payload)

    assert response.status_code == 400
    assert response.json()["error"]["details"] == "Conflicting concurrent change to this override; retry"
    assert "INSERT" not in response.text
    assert "SQL" not in response.text
    session.rollback.assert_called_once()


def test_single_integrity_error_does_not_leak_the_sql(make_client):
    payload = {"user_id": str(uuid4()), "allowed_models": ["gpt-4"]}
    error = IntegrityError("INSERT INTO user_model_overrides", {"user_id": "u"}, Exception("duplicate"))

    with patch(f"{SERVICE}.set_override", side_effect=error):
        response = _set(make_client(), payload)

    assert response.status_code == 409
    assert response.json()["error"]["message"] == "Conflicting concurrent change to this override; retry"
    assert "INSERT" not in response.text


def test_put_drops_repeated_model_ids_before_the_service(make_client):
    payload = {"user_id": str(uuid4()), "allowed_models": ["gpt-4", "claude-opus", "gpt-4"], "default_model": "gpt-4"}

    with patch(f"{SERVICE}.set_override", return_value=_override()) as mock_set:
        _set(make_client(), payload)

    assert mock_set.call_args.args[3] == ["gpt-4", "claude-opus"]


def test_batch_drops_repeated_model_ids_before_the_service(make_client):
    payload = {"operation": "grant", "user_ids": [str(uuid4())], "allowed_models": ["a", "b", "a"]}

    with patch(f"{SERVICE}.batch_set_overrides", return_value=[]) as mock_batch:
        response = make_client().post(f"{BASE}/batch", json=payload)

    assert response.status_code == 200
    assert mock_batch.call_args.args[4] == ["a", "b"]


def test_put_accepts_exactly_the_maximum_number_of_models(make_client):
    models = [f"model-{i}" for i in range(MAX_ALLOWED_MODELS)]
    payload = {"user_id": str(uuid4()), "allowed_models": models}

    with patch(f"{SERVICE}.set_override", return_value=_override()):
        response = _set(make_client(), payload)

    assert response.status_code == 200


def test_put_rejects_more_than_the_maximum_number_of_models(make_client):
    models = [f"model-{i}" for i in range(MAX_ALLOWED_MODELS + 1)]
    payload = {"user_id": str(uuid4()), "allowed_models": models}

    with patch(f"{SERVICE}.set_override") as mock_set:
        response = _set(make_client(), payload)

    assert response.status_code == 422
    mock_set.assert_not_called()


def test_batch_rejects_more_than_the_maximum_number_of_models(make_client):
    models = [f"model-{i}" for i in range(MAX_ALLOWED_MODELS + 1)]
    payload = {"operation": "grant", "user_ids": [str(uuid4())], "allowed_models": models}

    with patch(f"{SERVICE}.batch_set_overrides") as mock_batch:
        response = make_client().post(f"{BASE}/batch", json=payload)

    assert response.status_code == 422
    mock_batch.assert_not_called()


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("put", f"{BASE}/u1", {"allowed_models": ["gpt-4"]}),
        ("get", BASE, None),
        ("get", f"{BASE}/u1", None),
        ("delete", f"{BASE}/u1", None),
        ("post", f"{BASE}/batch", {"operation": "revoke", "user_ids": ["u1"]}),
    ],
)
def test_every_route_requires_user_management(make_client, method, path, body):
    """Overrides live on the users and memberships tables, so like the other project routes they need user management."""
    from codemie.core.exceptions import ExtendedHTTPException

    client = make_client()
    app.dependency_overrides.pop(_authorize_project_access)  # run the real dependency
    with (
        patch(
            "codemie.rest_api.routers.projects._common._ensure_user_management_enabled",
            side_effect=ExtendedHTTPException(code=400, message="User management not enabled"),
        ),
        patch(f"{SERVICE}.set_override") as set_override,
        patch(f"{SERVICE}.get_overrides_by_project") as listed,
        patch(f"{SERVICE}.get_override") as one,
        patch(f"{SERVICE}.delete_override") as deleted,
        patch(f"{SERVICE}.batch_set_overrides") as batch,
    ):
        response = client.request(method.upper(), path, json=body)

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "User management not enabled"
    for service_call in (set_override, listed, one, deleted, batch):
        service_call.assert_not_called()


def test_every_route_is_authorized_by_the_shared_project_dependency():
    """The routes rely on the project write check instead of a check of their own."""
    from codemie.rest_api.routers.projects.user_model_overrides import router

    assert len(router.routes) == 5
    for route in router.routes:
        calls = [dependency.call for dependency in route.dependant.dependencies]
        assert _authorize_project_access in calls, route.path
