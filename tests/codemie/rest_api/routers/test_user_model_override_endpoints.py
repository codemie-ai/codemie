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

"""Tests for user model override API endpoints."""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from codemie.core.exceptions import ModelNotWhitelistedException
from codemie.rest_api.models.user_model_override import UserModelOverride
from codemie.rest_api.main import app
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.rest_api.routers.projects._common import _authorize_project_access
from codemie.rest_api.security.authentication import authenticate

SERVICE = "codemie.rest_api.routers.projects.user_model_overrides.UserModelOverrideService"
PROJECT = "test-project"
ADMIN_ID = "admin-1"
ADMIN_EMAIL = "admin-1@example.com"
BASE = f"/v1/projects/{PROJECT}/user-model-overrides"


def _unauthorized():
    raise ExtendedHTTPException(code=404, message="Project not found")


@pytest.fixture
def session():
    """Session mock handed out by the patched get_session."""
    return MagicMock()


@pytest.fixture
def make_client(session):
    """Build a TestClient whose authenticated user is/is not a project admin."""

    def _make(is_admin: bool = True, is_platform_admin: bool = False) -> TestClient:
        user = MagicMock()
        user.id = ADMIN_ID
        user.email = ADMIN_EMAIL
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


def test_put_user_model_override_success(make_client, session):
    """Verify POST /projects/{project}/user-model-overrides creates override."""
    override = _override(allowed_models=["gpt-4", "claude-opus"], default_model="gpt-4")
    payload = {"user_id": override.user_id, "allowed_models": ["gpt-4", "claude-opus"], "default_model": "gpt-4"}

    with patch(f"{SERVICE}.set_override", return_value=override) as mock_set:
        response = _set(make_client(), payload)

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == override.user_id
    assert body["allowed_models"] == ["gpt-4", "claude-opus"]
    assert body["default_model"] == "gpt-4"
    mock_set.assert_called_once_with(
        session,
        override.user_id,
        PROJECT,
        ["gpt-4", "claude-opus"],
        "gpt-4",
        actor_id=ADMIN_ID,
        actor_email=ADMIN_EMAIL,
    )
    session.commit.assert_called_once()


def test_put_user_model_override_non_admin(make_client):
    """Verify POST requires admin role."""
    payload = {"user_id": str(uuid4()), "allowed_models": ["gpt-4"]}

    with patch(f"{SERVICE}.set_override") as mock_set:
        response = _set(make_client(is_admin=False), payload)

    assert response.status_code == 404
    mock_set.assert_not_called()


def test_get_user_model_overrides_list(make_client, session):
    """Verify GET /projects/{project}/user-model-overrides lists overrides."""
    overrides = [_override() for _ in range(3)]

    with patch(f"{SERVICE}.get_overrides_by_project", return_value=overrides) as mock_list:
        response = make_client().get(f"/v1/projects/{PROJECT}/user-model-overrides")

    assert response.status_code == 200
    assert len(response.json()) == 3
    mock_list.assert_called_once_with(session, PROJECT, 0, 100)


def test_get_user_model_override_single(make_client, session):
    """Verify GET /projects/{project}/user-model-overrides/{user_id} fetches one override."""
    override = _override()

    with patch(f"{SERVICE}.get_override", return_value=override) as mock_get:
        response = make_client().get(f"/v1/projects/{PROJECT}/user-model-overrides/{override.user_id}")

    assert response.status_code == 200
    assert response.json()["id"] == str(override.id)
    mock_get.assert_called_once_with(session, override.user_id, PROJECT)


def test_get_user_model_override_not_found(make_client):
    """Verify GET returns 404 when the user has no override."""
    with patch(f"{SERVICE}.get_override", return_value=None):
        response = make_client().get(f"/v1/projects/{PROJECT}/user-model-overrides/{uuid4()}")

    assert response.status_code == 404


def test_delete_user_model_override(make_client, session):
    """Verify DELETE /projects/{project}/user-model-overrides/{user_id} removes override."""
    user_id = str(uuid4())

    with patch(f"{SERVICE}.delete_override") as mock_delete:
        response = make_client().delete(f"/v1/projects/{PROJECT}/user-model-overrides/{user_id}")

    assert response.status_code == 204
    mock_delete.assert_called_once_with(session, user_id, PROJECT, actor_id=ADMIN_ID, actor_email=ADMIN_EMAIL)
    session.commit.assert_called_once()


def test_post_batch_grant(make_client, session):
    """Verify POST /projects/{project}/user-model-overrides/batch grants overrides atomically."""
    user_ids = [str(uuid4()), str(uuid4())]
    overrides = [_override(user_id=uid) for uid in user_ids]
    payload = {"operation": "grant", "user_ids": user_ids, "allowed_models": ["gpt-4"]}

    with patch(f"{SERVICE}.batch_set_overrides", return_value=overrides) as mock_batch:
        response = make_client().post(f"/v1/projects/{PROJECT}/user-model-overrides/batch", json=payload)

    assert response.status_code == 200
    assert [o["user_id"] for o in response.json()] == user_ids
    mock_batch.assert_called_once_with(
        session, PROJECT, user_ids, "grant", ["gpt-4"], None, actor_id=ADMIN_ID, actor_email=ADMIN_EMAIL
    )
    session.commit.assert_called_once()


def test_post_batch_validation_failure_rolls_back(make_client, session):
    """Verify batch operation fails with 400 and rolls back on validation error."""
    payload = {"operation": "grant", "user_ids": [str(uuid4())], "allowed_models": ["invalid-model"]}

    with patch(
        f"{SERVICE}.batch_set_overrides",
        side_effect=ModelNotWhitelistedException("invalid-model", PROJECT),
    ):
        response = make_client().post(f"/v1/projects/{PROJECT}/user-model-overrides/batch", json=payload)

    assert response.status_code == 400
    session.rollback.assert_called_once()
    session.commit.assert_not_called()
