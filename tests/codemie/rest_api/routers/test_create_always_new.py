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


"""AC5 create-always-new tests (EPMCDME-15310): POSTing an identical document without an id creates a new entity.

Reuses the in-memory SQLite harness and per-entity environments of ``test_idempotent_write`` (fixtures are
imported by name; its autouse auth fixture is replaced by a local one that restores ``dependency_overrides``).
Every test POSTs the same document twice through TestClient and asserts through the stored rows: two rows, two
distinct ids, and row 1 untouched by POST 2 (``assert_stored_unchanged``).

Entities whose POST route legitimately rejects a duplicate are pinned with the observed status instead.
"""

# ruff: noqa: F811
# (fixtures imported from test_idempotent_write are re-declared as test arguments)

import sqlite3
from unittest.mock import patch

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import text

from codemie.core.workflow_models import WorkflowConfig
from codemie.rest_api.main import app
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.category import Category
from codemie.rest_api.models.settings import Settings
from codemie.rest_api.models.skill import Skill
from codemie.rest_api.security.authentication import authenticate
from codemie_tools.base.models import CredentialTypes
from tests.codemie.rest_api.routers.idempotency_helpers import assert_stored_unchanged
from tests.codemie.rest_api.routers.test_idempotent_write import (  # noqa: F401  (fixtures reused by name)
    ASSISTANT_DOC,
    PROJECT,
    SETTINGS_DOCS,
    SKILL_DOC,
    USER,
    WORKFLOW_DOC,
    _read_all,
    assistant_env,
    category_admin,
    db,
    encryption,
    settings_env,
    skill_env,
    workflow_env,
)


@pytest.fixture(autouse=True)
def _auth_create_always_new():
    def _authenticate(request: Request = None):
        if request is not None:
            request.state.user = USER
        return USER

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[authenticate] = _authenticate
    yield
    app.dependency_overrides = previous


SETTING_DOC = {
    "project_name": PROJECT,
    "alias": "the-alias",
    "credential_type": CredentialTypes.LITE_LLM.value,
    "credential_values": SETTINGS_DOCS[CredentialTypes.LITE_LLM],
}


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def headers():
    return {"user-id": USER.id}


def _post_twice(client, headers, path: str, body: dict, db, model):
    """POST the same document twice; return (rows after POST 1, rows after POST 2, both responses)."""
    first_response = client.post(path, json=body, headers=headers)
    first_rows = _read_all(db, model)
    second_response = client.post(path, json=body, headers=headers)
    return first_rows, _read_all(db, model), first_response, second_response


def _assert_created_distinct(first_rows, second_rows, first_response, second_response):
    assert first_response.status_code in (200, 201), first_response.text
    assert second_response.status_code in (200, 201), second_response.text
    assert len(first_rows) == 1
    assert len(second_rows) == 2
    assert len({row["id"] for row in second_rows}) == 2
    row_1 = next(row for row in second_rows if row["id"] == first_rows[0]["id"])
    assert_stored_unchanged(first_rows[0], row_1)


@pytest.fixture
def create_workflow_env(workflow_env):
    # create_workflow validates that every referenced assistant exists; that lookup is not persistence under test
    with (
        patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow"),
        patch("codemie.service.workflow_service.ensure_application_exists"),  # applications table
    ):
        yield workflow_env


@pytest.fixture
def create_settings_env(settings_env):
    with patch("codemie.service.settings.settings.ensure_application_exists"):  # applications table
        yield settings_env


@pytest.fixture
def category_db(db, monkeypatch):
    """Restore what the harness drops: the unique index on ``categories.name`` and, since SQLite raises its own
    integrity error, the exception type the service maps to 409 (psycopg ``UniqueViolation`` in PostgreSQL)."""
    monkeypatch.setattr("codemie.service.assistant.category_service._UNIQUE_CONSTRAINT", sqlite3.IntegrityError)
    with db.begin() as connection:
        connection.execute(text(f"CREATE UNIQUE INDEX uq_category_name ON {Category.__tablename__} (name)"))
    return db


class TestPostCreatesNewEntity:
    def test_assistant_post_twice_creates_two_assistants(self, client, headers, assistant_env):
        result = _post_twice(client, headers, "/v1/assistants", ASSISTANT_DOC, assistant_env, Assistant)

        _assert_created_distinct(*result)

    def test_workflow_post_twice_creates_two_workflows(self, client, headers, create_workflow_env):
        result = _post_twice(client, headers, "/v1/workflows", WORKFLOW_DOC, create_workflow_env, WorkflowConfig)

        _assert_created_distinct(*result)


class TestPostRejectsDuplicate:
    """Routes that legitimately refuse an identical second POST. Pinned, not changed: the AC5 story text needs
    correcting for these entities (they are create-or-409/422, not create-always-new)."""

    @staticmethod
    def _assert_rejected(db, model, first_response, second_response, status_code: int):
        assert first_response.status_code in (200, 201), first_response.text
        assert second_response.status_code == status_code, second_response.text
        assert len(_read_all(db, model)) == 1

    def test_skill_second_post_with_same_name_is_409(self, client, headers, skill_env):
        # SkillService.create_skill: name must be unique per author per project
        _, rows, first, second = _post_twice(client, headers, "/v1/skills", SKILL_DOC, skill_env, Skill)

        self._assert_rejected(skill_env, Skill, first, second, 409)
        assert rows[0]["name"] == SKILL_DOC["name"]

    def test_category_second_post_with_same_name_is_409(self, client, headers, category_db, category_admin):
        # categories.name is unique in PostgreSQL; CategoryService.create_category maps the violation to 409
        body = {"name": "Engineering", "description": "d"}

        _, _, first, second = _post_twice(client, headers, "/v1/assistants/categories", body, category_db, Category)

        self._assert_rejected(category_db, Category, first, second, 409)

    @pytest.mark.parametrize("path", ["project", "user"])
    def test_setting_second_post_with_same_alias_is_422(self, client, headers, create_settings_env, path):
        # Settings.check_alias_unique raises ValueError, which the route maps to 422
        _, _, first, second = _post_twice(
            client, headers, f"/v1/settings/{path}", SETTING_DOC, create_settings_env, Settings
        )

        self._assert_rejected(create_settings_env, Settings, first, second, 422)
