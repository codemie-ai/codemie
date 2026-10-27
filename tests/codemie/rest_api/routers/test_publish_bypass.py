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

"""`ignore_recommendations` is a waiver, not a skip button.

EXECUTION-PLAN 7.7 — traces F-83.

Today `assistant.py:1411` reads the flag and hands it to
`_llm_validate_on_marketplace_publish`, whose first branch is::

    # If user already chose to bypass, skip validation entirely
    if is_bypassing:
        return True

No severity check and no role check: anybody who can publish can send
``ignore_recommendations: true`` and skip every quality control the product
has. The flag may waive OPTIONAL findings, for admins and maintainers only;
the deterministic gates from 7.6 stay unconditional for everyone.
"""

from __future__ import annotations

from typing import Any, Iterator
from unittest.mock import patch

import pytest
from tests.codemie.marketplace_fixtures.mounted_app import mounted_marketplace_app
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session

from codemie.configs import config
from codemie.core.models import CreatedByUser
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User

PUBLISH = "/v1/assistants/{aid}/marketplace/publish"

GOOD_PROMPT = (
    "You are a release-notes assistant for a platform engineering group. "
    "You summarise merge requests into human readable release notes, you group "
    "them by component, and you always cite the merge request identifier next "
    "to every bullet you produce. When the user asks for a changelog you answer "
    "with markdown headings per component and a short paragraph explaining the "
    "user visible impact of each change. You never invent a merge request that "
    "was not given to you, and when the input is empty you say so plainly "
    "instead of producing an empty document. "
) * 3


@pytest.fixture(scope="module")
def app() -> Iterator[FastAPI]:
    """The production ASGI app, with the marketplace-management router mounted."""
    yield from mounted_marketplace_app()


def _user(*, admin: bool) -> User:
    user = User(
        id="publisher-1",
        username="publisher",
        name="Priya Publisher",
        email="priya@codemie.ai",
        project_names=["demo"],
    )
    # ENV=local force-promotes every user, so pin the flags explicitly.
    user.is_admin = admin
    user.is_maintainer = False
    return user


@pytest.fixture
def make_client(app: FastAPI, mm_publish_db) -> Iterator[Any]:
    saved = dict(app.dependency_overrides)

    def _factory(*, admin: bool) -> tuple[TestClient, User]:
        user = _user(admin=admin)

        async def _fake_authenticate(request: Request) -> User:
            request.state.user = user
            return user

        app.dependency_overrides[authenticate] = _fake_authenticate
        with mm_publish_db.connect() as conn:
            conn.execute(
                text(
                    "insert into codemie.users (id, username, email, name, is_admin, is_active, date) "
                    "values (:i, :u, :e, :n, :a, true, now()) on conflict (id) do nothing"
                ),
                {"i": user.id, "u": user.username, "e": user.email, "n": user.name, "a": admin},
            )
            conn.commit()
        return TestClient(app, raise_server_exceptions=False), user

    yield _factory
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


def _seed(engine, user: User, **overrides: Any) -> str:
    fields: dict[str, Any] = {
        "id": "pub-1",
        "name": "Release Notes Assistant",
        "description": (
            "Summarises merge requests into structured, component-grouped release notes "
            "with citations back to every source merge request identifier."
        ),
        "system_prompt": GOOD_PROMPT,
        "categories": ["engineering"],
        "project": "demo",
        "creator": user.id,
        "created_by": CreatedByUser(id=user.id, username=user.username, name=user.name),
        "is_global": False,
        "marketplace_status": "DRAFT",
        "shared": True,
    }
    fields.update(overrides)
    with Session(engine) as session:
        session.add(Assistant(**fields))
        session.commit()
    return fields["id"]


def _publish(client: TestClient, aid: str, **body: Any):
    payload: dict[str, Any] = {"categories": ["engineering"], "ignore_recommendations": True}
    payload.update(body)
    with (
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch("codemie.service.assistant.category_service.category_service.validate_category_ids", return_value=None),
        patch("codemie.service.monitoring.base_monitoring_service.send_log_metric"),
        patch.object(config, "MARKETPLACE_APPROVAL_GATE_ENABLED", True),
        # The gate is armed only together with marketplace management (`is_approval_gate_armed`).
        patch.object(config, "MARKETPLACE_MANAGEMENT_ENABLED", True),
        patch.object(config, "MARKETPLACE_LLM_VALIDATION_ON_PUBLISH_ENABLED", False),
    ):
        return client.post(PUBLISH.format(aid=aid), json=payload)


# ---------------------------------------------------------------------------


def test_bypass_is_denied_for_non_admin_on_critical(make_client, mm_publish_db) -> None:
    """A non-admin cannot waive anything, and the gates still ran to say why."""
    client, user = make_client(admin=False)
    aid = _seed(mm_publish_db, user, name="Bot", description="A little helper for the team, sort of.")

    response = _publish(client, aid)

    assert response.status_code == 403, response.text
    # The gates still ran: the refusal explains which rules are failing rather
    # than merely refusing the waiver.
    assert "name_length" in response.text, response.text

    with mm_publish_db.connect() as conn:
        row = (
            conn.execute(text("select marketplace_status, is_global from codemie.assistants where id = :i"), {"i": aid})
            .mappings()
            .one()
        )
    assert row["marketplace_status"] == "DRAFT"
    assert row["is_global"] is False


def test_bypass_never_skips_deterministic_gates(make_client, mm_publish_db, monkeypatch) -> None:
    """Even an admin's waiver is scoped to OPTIONAL: CRITICAL still blocks."""
    client, user = make_client(admin=True)
    aid = _seed(mm_publish_db, user, name="Bot", description="A little helper for the team, sort of.")

    response = _publish(client, aid)

    assert response.status_code == 422, response.text
    assert "name_length" in response.text, response.text

    with mm_publish_db.connect() as conn:
        row = (
            conn.execute(text("select marketplace_status, is_global from codemie.assistants where id = :i"), {"i": aid})
            .mappings()
            .one()
        )
    assert row["marketplace_status"] == "DRAFT"
    assert row["is_global"] is False


def test_admin_bypass_waives_only_optional_findings(make_client, mm_publish_db, monkeypatch) -> None:
    """The waiver's whole legitimate scope: an OPTIONAL finding, acknowledged."""
    from codemie.configs.marketplace_management import get_marketplace_management_config

    monkeypatch.setattr(get_marketplace_management_config("assistants").core_gates, "description_min", 10)
    client, user = make_client(admin=True)
    aid = _seed(mm_publish_db, user, name="Release Notes Assistant", description="Release Notes Assistant")

    response = _publish(client, aid)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert [f["rule"] for f in payload["gate_findings"]] == ["description_not_equal_name"]
    assert payload["acknowledged"] is True
