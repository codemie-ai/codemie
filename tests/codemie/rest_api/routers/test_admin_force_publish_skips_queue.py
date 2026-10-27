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

"""Admins/maintainers publishing from Project Assistants skip the moderation
queue and land straight on APPROVED/Verified, and a CRITICAL gate block can be
overridden with a "Force-Publish" the same way the marketplace-management
queue's Force-Publish action already works — both routes now call the same
`MarketplaceQueueService.force_publish` transition.
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
PENDING = "/v1/marketplace-management/entities/assistants/pending"

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
GOOD_DESCRIPTION = (
    "Summarises merge requests into structured, component-grouped release notes "
    "with citations back to every source merge request identifier."
)


@pytest.fixture(scope="module")
def app() -> Iterator[FastAPI]:
    """The production ASGI app, with the marketplace-management router mounted."""
    yield from mounted_marketplace_app()


def _identity(app: FastAPI, user: User) -> None:
    async def _fake_authenticate(request: Request) -> User:
        request.state.user = user
        return user

    app.dependency_overrides[authenticate] = _fake_authenticate


def _make_user(*, id_: str, admin: bool, maintainer: bool = False) -> User:
    user = User(
        id=id_,
        username=id_,
        name=id_.replace("-", " ").title(),
        email=f"{id_}@codemie.ai",
        project_names=["demo"],
    )
    user.is_admin = admin
    user.is_maintainer = maintainer
    return user


@pytest.fixture
def admin_user() -> User:
    return _make_user(id_="admin-1", admin=True)


@pytest.fixture
def regular_user() -> User:
    return _make_user(id_="regular-1", admin=False)


def _client_for(app: FastAPI, user: User, mm_publish_db) -> TestClient:
    _identity(app, user)
    with mm_publish_db.connect() as conn:
        conn.execute(
            text(
                "insert into codemie.users (id, username, email, name, is_admin, is_active, date) "
                "values (:i, :u, :e, :n, :a, true, now()) on conflict (id) do nothing"
            ),
            {"i": user.id, "u": user.username, "e": user.email, "n": user.name, "a": user.is_admin},
        )
        conn.commit()
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def admin_client(app: FastAPI, admin_user: User, mm_publish_db) -> Iterator[TestClient]:
    saved = dict(app.dependency_overrides)
    yield _client_for(app, admin_user, mm_publish_db)
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


@pytest.fixture
def regular_client(app: FastAPI, regular_user: User, mm_publish_db) -> Iterator[TestClient]:
    saved = dict(app.dependency_overrides)
    yield _client_for(app, regular_user, mm_publish_db)
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


def seed_assistant(engine, owner: User, **overrides: Any) -> str:
    fields: dict[str, Any] = {
        "id": "force-pub-1",
        "name": "Release Notes Assistant",
        "description": GOOD_DESCRIPTION,
        "system_prompt": GOOD_PROMPT,
        "categories": ["engineering"],
        "project": "demo",
        "creator": owner.id,
        "created_by": CreatedByUser(id=owner.id, username=owner.username, name=owner.name),
        "is_global": False,
        "marketplace_status": "DRAFT",
        "shared": True,
    }
    fields.update(overrides)
    with Session(engine) as session:
        session.add(Assistant(**fields))
        session.commit()
    return fields["id"]


def publish(client: TestClient, aid: str, *, llm: bool = False, **body: Any):
    payload: dict[str, Any] = {"categories": ["engineering"]}
    payload.update(body)
    with (
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch("codemie.service.assistant.category_service.category_service.validate_category_ids", return_value=None),
        patch("codemie.service.monitoring.base_monitoring_service.send_log_metric"),
        patch.object(config, "MARKETPLACE_APPROVAL_GATE_ENABLED", True),
        # The gate is armed only together with marketplace management (`is_approval_gate_armed`).
        patch.object(config, "MARKETPLACE_MANAGEMENT_ENABLED", True),
        patch.object(config, "MARKETPLACE_LLM_VALIDATION_ON_PUBLISH_ENABLED", llm),
    ):
        return client.post(PUBLISH.format(aid=aid), json=payload)


def _row(engine, aid: str) -> dict[str, Any]:
    with engine.connect() as conn:
        return dict(
            conn.execute(
                text("select marketplace_status, is_global, is_verified from codemie.assistants where id = :i"),
                {"i": aid},
            )
            .mappings()
            .one()
        )


def _last_decision(engine, aid: str) -> dict[str, Any]:
    with engine.connect() as conn:
        return dict(
            conn.execute(
                text(
                    "select decision, override_notice from codemie.mm_review_decision "
                    " where entity_id = :i order by created_at desc limit 1"
                ),
                {"i": aid},
            )
            .mappings()
            .one()
        )


# ---------------------------------------------------------------------------
# Admin/maintainer publish skips the queue
# ---------------------------------------------------------------------------


def test_admin_publish_skips_queue_goes_straight_to_approved(
    admin_client: TestClient, mm_publish_db, admin_user: User
) -> None:
    aid = seed_assistant(mm_publish_db, admin_user)

    response = publish(admin_client, aid)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["marketplace_status"] == "APPROVED"

    row = _row(mm_publish_db, aid)
    assert row["marketplace_status"] == "APPROVED"
    assert row["is_global"] is True
    assert row["is_verified"] is True

    decision = _last_decision(mm_publish_db, aid)
    assert decision["decision"] == "FORCE_PUBLISH"
    assert decision["override_notice"]

    # Never touched the review queue.
    pending = admin_client.get(PENDING)
    assert pending.json()["total"] == 0, pending.text


def test_non_admin_publish_still_goes_to_pending_review(
    regular_client: TestClient, mm_publish_db, regular_user: User
) -> None:
    """Regression check: this endpoint's existing behavior for everyone else
    is unchanged by the admin/maintainer skip-queue path."""
    aid = seed_assistant(mm_publish_db, regular_user, id="force-pub-2")

    response = publish(regular_client, aid)

    assert response.status_code == 200, response.text
    assert response.json()["marketplace_status"] == "PENDING_REVIEW"

    row = _row(mm_publish_db, aid)
    assert row["marketplace_status"] == "PENDING_REVIEW"
    assert row["is_global"] is False

    decision = _last_decision(mm_publish_db, aid)
    assert decision["decision"] == "SUBMIT"


# ---------------------------------------------------------------------------
# A CRITICAL gate finding still blocks admin/maintainer publish without
# force_publish — only the explicit Force-Publish action overrides it.
# ---------------------------------------------------------------------------


def test_admin_publish_without_force_still_blocks_on_critical(
    admin_client: TestClient, mm_publish_db, admin_user: User
) -> None:
    aid = seed_assistant(
        mm_publish_db,
        admin_user,
        id="force-pub-3",
        name="Bot",
        description="A little helper for the team, sort of.",  # 38 chars, below the 100 min
    )

    response = publish(admin_client, aid)

    assert response.status_code == 422, response.text
    assert "description_length" in response.text

    row = _row(mm_publish_db, aid)
    assert row["marketplace_status"] == "DRAFT"
    assert row["is_global"] is False


def test_admin_force_publish_bypasses_critical_gate(admin_client: TestClient, mm_publish_db, admin_user: User) -> None:
    aid = seed_assistant(
        mm_publish_db,
        admin_user,
        id="force-pub-4",
        name="Bot",
        description="A little helper for the team, sort of.",
    )

    response = publish(
        admin_client,
        aid,
        force_publish=True,
        override_notice="Reviewed offline with the assistant owner; publishing ahead of the gate.",
    )

    assert response.status_code == 200, response.text
    assert response.json()["marketplace_status"] == "APPROVED"

    row = _row(mm_publish_db, aid)
    assert row["marketplace_status"] == "APPROVED"
    assert row["is_global"] is True

    decision = _last_decision(mm_publish_db, aid)
    assert decision["decision"] == "FORCE_PUBLISH"
    assert decision["override_notice"] == "Reviewed offline with the assistant owner; publishing ahead of the gate."


def test_force_publish_requires_override_notice(admin_client: TestClient, mm_publish_db, admin_user: User) -> None:
    aid = seed_assistant(mm_publish_db, admin_user, id="force-pub-5")

    response = publish(admin_client, aid, force_publish=True, override_notice="too short")

    assert response.status_code == 422, response.text
    assert "override_notice" in response.text

    row = _row(mm_publish_db, aid)
    assert row["marketplace_status"] == "DRAFT"


def test_force_publish_forbidden_for_non_admin(regular_client: TestClient, mm_publish_db, regular_user: User) -> None:
    aid = seed_assistant(mm_publish_db, regular_user, id="force-pub-6")

    response = publish(
        regular_client,
        aid,
        force_publish=True,
        override_notice="Trying to skip the queue without the right role.",
    )

    assert response.status_code == 403, response.text

    row = _row(mm_publish_db, aid)
    assert row["marketplace_status"] == "DRAFT"


# ---------------------------------------------------------------------------
# force_publish also waives the *advisory* LLM quality gate — a second,
# separate check further down the handler from the deterministic gates above.
# Without this, an admin's Force-Publish could still 422 on a rejected LLM
# verdict, defeating the point of the override.
# ---------------------------------------------------------------------------


def _reject_verdict():
    from codemie.rest_api.models.assistant import QualityValidationResult

    return QualityValidationResult(
        decision="reject",
        reasoning_comment="The system prompt does not meet marketplace quality standards.",
        recommendations={"fields": [], "toolkits": [], "context": []},
    )


def test_admin_force_publish_also_waives_the_llm_quality_gate(
    admin_client: TestClient, mm_publish_db, admin_user: User
) -> None:
    aid = seed_assistant(mm_publish_db, admin_user, id="force-pub-7")

    with patch(
        "codemie.service.assistant_generator_service.AssistantGeneratorService.validate_assistant_for_publish",
        return_value=_reject_verdict(),
    ) as stub:
        response = publish(
            admin_client,
            aid,
            llm=True,
            force_publish=True,
            override_notice="Reviewed offline with the assistant owner; publishing ahead of both gates.",
        )

    assert response.status_code == 200, response.text
    assert response.json()["marketplace_status"] == "APPROVED"
    assert not stub.called, "force_publish must waive the LLM check, not just tolerate its verdict"

    row = _row(mm_publish_db, aid)
    assert row["marketplace_status"] == "APPROVED"
    assert row["is_global"] is True
