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

"""The deterministic gates must run AT PUBLISH, not only in the batch auditor.

EXECUTION-PLAN 7.6 — traces F-82.

The eight gates in `validator/gates.py` are correct and `gates.run_all` has
exactly one caller in the repository: `validator/engine.py:470`, the *batch
auditor*. `grep -n "marketplace_management" rest_api/routers/assistant.py`
returns nothing. Spec P1-01 requires the gates at publish time and P1-05
requires a CRITICAL gate to block with an actionable message, so today an
assistant named `Bot` with a 40-character description publishes with a 200.
Correct code in the wrong call graph.

These tests drive the real app and the real database: publishing under the
armed gate is a compare-and-set on `assistants` plus an `mm_review_decision`
insert in one transaction, and P1-21 requires the reviewer to *read* the gate
result rather than recompute it — which is a persistence claim.
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

# Long enough to clear `substance` (>= 800 chars) and `system_prompt_length`
# (>= 200) without tripping `no_inline_credentials`.
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def app() -> Iterator[FastAPI]:
    """The production ASGI app, with the marketplace-management router mounted."""
    yield from mounted_marketplace_app()


def _identity(app: FastAPI, user: User) -> None:
    async def _fake_authenticate(request: Request) -> User:
        request.state.user = user
        return user

    app.dependency_overrides[authenticate] = _fake_authenticate


def _make_user(*, admin: bool) -> User:
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
def publisher() -> User:
    # Non-admin on purpose: these tests exercise the SUBMIT -> PENDING_REVIEW
    # path, and an admin/maintainer publish now skips the review queue
    # entirely (`assistant.py` `admin_skip_queue`).
    return _make_user(admin=False)


@pytest.fixture
def client(app: FastAPI, publisher: User, mm_publish_db) -> Iterator[TestClient]:
    saved = dict(app.dependency_overrides)
    _identity(app, publisher)
    with mm_publish_db.connect() as conn:
        conn.execute(
            text(
                "insert into codemie.users (id, username, email, name, is_admin, is_active, date) "
                "values (:i, :u, :e, :n, true, true, now()) on conflict (id) do nothing"
            ),
            {"i": publisher.id, "u": publisher.username, "e": publisher.email, "n": publisher.name},
        )
        conn.commit()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


def seed_assistant(engine, publisher: User, **overrides: Any) -> str:
    """Insert one private, never-published assistant and return its id."""
    fields: dict[str, Any] = {
        "id": "pub-1",
        "name": "Release Notes Assistant",
        "description": GOOD_DESCRIPTION,
        "system_prompt": GOOD_PROMPT,
        "categories": ["engineering"],
        "project": "demo",
        "creator": publisher.id,
        "created_by": CreatedByUser(id=publisher.id, username=publisher.username, name=publisher.name),
        "is_global": False,
        "marketplace_status": "DRAFT",
        "shared": True,
    }
    fields.update(overrides)
    with Session(engine) as session:
        session.add(Assistant(**fields))
        session.commit()
    return fields["id"]


def publish(client: TestClient, aid: str, **body: Any):
    payload: dict[str, Any] = {"categories": ["engineering"]}
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
# P1-05 — a CRITICAL gate blocks, with the message gates.py already produces
# ---------------------------------------------------------------------------


def test_critical_gate_blocks_publish(client: TestClient, mm_publish_db, publisher: User) -> None:
    """A 3-char name and a 40-char description must not reach the marketplace."""
    aid = seed_assistant(
        mm_publish_db,
        publisher,
        name="Bot",
        description="A little helper for the team, sort of.",  # 38 chars
    )

    response = publish(client, aid)

    assert response.status_code == 422, response.text
    body = response.text
    # The failing rules are NAMED, per P1-05.
    assert "name_length" in body, body
    assert "description_length" in body, body
    # ...and carry the actionable message gates.py already produces.
    assert "Name is 3 chars, minimum is 5." in body, body
    assert "minimum is 100" in body, body

    # And nothing moved: no submission, no status change, no version bump.
    with mm_publish_db.connect() as conn:
        row = (
            conn.execute(
                text("select marketplace_status, is_global, marketplace_version from codemie.assistants where id = :i"),
                {"i": aid},
            )
            .mappings()
            .one()
        )
        decisions = conn.execute(text("select count(*) from codemie.mm_review_decision")).scalar_one()
    assert row["marketplace_status"] == "DRAFT"
    assert row["is_global"] is False
    assert row["marketplace_version"] == 0
    assert decisions == 0


# ---------------------------------------------------------------------------
# An OPTIONAL finding is advisory: it is returned, it does not block
# ---------------------------------------------------------------------------


def test_optional_gate_does_not_block(
    client: TestClient, mm_publish_db, publisher: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`description_not_equal_name` is OPTIONAL: publish proceeds, finding returned.

    Under the shipped config this gate is unreachable — `description_min` is 100
    and `name_max` is 60, so a description can never equal a name that passes
    `name_length`. The threshold is lowered here so the *severity contract*
    (OPTIONAL is advisory, CRITICAL blocks) can be exercised at all.
    """
    from codemie.configs.marketplace_management import get_marketplace_management_config

    mm_cfg = get_marketplace_management_config("assistants")
    monkeypatch.setattr(mm_cfg.core_gates, "description_min", 10)

    aid = seed_assistant(
        mm_publish_db,
        publisher,
        name="Release Notes Assistant",
        description="Release Notes Assistant",
    )

    response = publish(client, aid)

    assert response.status_code == 200, response.text
    payload = response.json()
    findings = payload["gate_findings"]
    assert [f["rule"] for f in findings] == ["description_not_equal_name"], payload
    assert findings[0]["severity"] == "OPTIONAL"
    assert payload["marketplace_status"] == "PENDING_REVIEW"

    with mm_publish_db.connect() as conn:
        row = (
            conn.execute(text("select marketplace_status, is_global from codemie.assistants where id = :i"), {"i": aid})
            .mappings()
            .one()
        )
    assert row["marketplace_status"] == "PENDING_REVIEW"
    assert row["is_global"] is False


# ---------------------------------------------------------------------------
# P1-21 — the gate result is persisted at SUBMIT time, and the reviewer's
# queue recomputes it live at read time (EPMCDME-14234) so it can never
# disagree with the detail endpoint about the current assistant.
# ---------------------------------------------------------------------------


def test_gate_results_are_persisted_with_the_submission(
    app: FastAPI, client: TestClient, mm_publish_db, publisher: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The finding set is stored with the PENDING_REVIEW row, and a live recompute
    of an unedited assistant agrees with what was submitted (EPMCDME-14234)."""
    from codemie.configs.marketplace_management import get_marketplace_management_config

    mm_cfg = get_marketplace_management_config("assistants")
    monkeypatch.setattr(mm_cfg.core_gates, "description_min", 10)

    aid = seed_assistant(
        mm_publish_db,
        publisher,
        name="Release Notes Assistant",
        description="Release Notes Assistant",
    )

    assert publish(client, aid).status_code == 200

    # 1. Persisted alongside the submission, in the decision ledger.
    with mm_publish_db.connect() as conn:
        stored = (
            conn.execute(
                text(
                    "select decision, result from codemie.mm_review_decision "
                    " where entity_id = :i order by created_at desc limit 1"
                ),
                {"i": aid},
            )
            .mappings()
            .one()
        )
    assert stored["decision"] == "SUBMIT"
    persisted = stored["result"]["gate_findings"]
    assert [f["rule"] for f in persisted] == ["description_not_equal_name"]

    # 2. Read back by the reviewer's queue, recomputed live against the
    # assistant's current data — unedited since submission, so it must agree.
    # `/pending` is admin/maintainer-only, so read it as a reviewer distinct
    # from the (non-admin, by design — see the `publisher` fixture) submitter.
    _identity(app, _make_user(admin=True))
    queue = client.get(PENDING)

    assert queue.status_code == 200, queue.text
    items = queue.json()["items"]
    assert len(items) == 1, items
    assert [f["rule"] for f in items[0]["gate_findings"]] == ["description_not_equal_name"]


DETAIL = "/v1/marketplace-management/entities/assistants/{eid}"


def test_pending_and_detail_agree_after_a_post_submit_edit(
    app: FastAPI, client: TestClient, mm_publish_db, publisher: User
) -> None:
    """EPMCDME-14234 repro: an edit after SUBMIT must not leave the queue stale."""
    aid = seed_assistant(mm_publish_db, publisher, name="Release Notes Assistant")

    assert publish(client, aid).status_code == 200

    # Simulate a post-submission edit that introduces a CRITICAL failure.
    with mm_publish_db.connect() as conn:
        conn.execute(
            text("update codemie.assistants set system_prompt = :p where id = :i"),
            {"p": "short", "i": aid},
        )
        conn.commit()

    # `/pending` and the detail endpoint are admin/maintainer-only, so read
    # them as a reviewer distinct from the (non-admin) submitter.
    _identity(app, _make_user(admin=True))
    pending = client.get(PENDING)
    detail = client.get(DETAIL.format(eid=aid))

    assert pending.status_code == 200, pending.text
    assert detail.status_code == 200, detail.text
    pending_rules = {f["rule"] for f in pending.json()["items"][0]["gate_findings"]}
    detail_rules = {f["rule"] for f in detail.json()["gate_findings"]}
    assert "system_prompt_length" in pending_rules
    assert pending_rules == detail_rules
