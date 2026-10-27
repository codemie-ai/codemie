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

"""A newly created assistant is a DRAFT, not an APPROVED one (EXECUTION-PLAN 12.3).

`5df1d2e53fbf:29` gave `assistants.marketplace_status` a server default of
`'APPROVED'` and the SQLModel class repeated it (`models/assistant.py:657`), so
every assistant the platform has ever created was stamped with the terminal
state of the review state machine before anybody looked at it. It was latent
only because `is_global` gates visibility; the record itself claimed a
moderation decision that no human made, and EXECUTION-PLAN 7.2 already had to
run a backfill to undo exactly that for 63 rows.

DRAFT rather than NULL, for three reasons that all come from the state machine
rather than from taste:

1. `assistant._SUBMITTABLE_STATUSES` is `("DRAFT", "REJECTED", "WITHDRAWN")`,
   and `queue_service`'s compare-and-set guards the transition with
   `AND marketplace_status = ANY(:allowed)`. NULL is equal to nothing in SQL,
   so a NULL row could never be submitted: its first publish would 409 against
   its own transition table.
2. `ck_assistants_marketplace_status` (`o1r2e3v4i5e6:106`) admits NULL only as
   legacy tolerance -- `MarketplaceStatus` itself has no NULL member. DRAFT is
   the enum's own initial state.
3. `AssistantAdapter.list_records` / `_CATALOGUE_SCOPE` already exclude DRAFT,
   so a draft stays off every moderation surface -- which is the property the
   7.2 backfill was reaching for.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import Request, status
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from codemie.rest_api.main import app
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User

_CREATE_URL = "/v1/assistants"


def _payload(name: str) -> dict:
    return {
        "name": name,
        "description": "An assistant nobody has submitted to the marketplace.",
        "system_prompt": "You answer questions about the codebase.",
        "project": "demo",
        "llm_model_type": "gpt-4o",
        "toolkits": [],
        "context": [],
    }


def test_the_orm_default_is_draft() -> None:
    """The in-memory object the create path builds is a draft.

    `create_assistant` constructs `Assistant(**request.model_dump(...))` and
    never touches `marketplace_status`, so whatever the field defaults to is
    what gets persisted.
    """
    fresh = Assistant(
        name="Fresh",
        description="d",
        system_prompt="p",
    )

    assert fresh.marketplace_status != "APPROVED", (
        "a never-submitted assistant is stamped with the review state machine's " "TERMINAL state"
    )
    assert fresh.marketplace_status == "DRAFT"


@pytest.mark.asyncio
async def test_create_persists_draft_not_approved(mm_publish_db) -> None:
    """End to end: POST /v1/assistants stores DRAFT in Postgres.

    Driven against the real migrated schema so the column's *server* default is
    exercised as well as the ORM's -- the two are separate defaults and either
    one alone reintroduces the defect.
    """
    user = User(id="creator-1", username="creator", name="Cara Creator", project_names=["demo"])
    user.is_admin = True

    async def _authenticate(request: Request) -> User:
        request.state.user = user
        return user

    app.dependency_overrides[authenticate] = _authenticate
    try:
        with (
            patch("codemie.core.ability.Ability.can", return_value=True),
            patch("codemie.rest_api.routers.assistant.management.project_access_check", return_value=None),
            patch(
                "codemie.rest_api.routers.assistant.management._validate_integrations_or_response",
                return_value=None,
            ),
            patch("codemie.rest_api.routers.assistant.management.ensure_application_exists"),
            patch("codemie.service.assistant_service.AssistantService.ensure_unique_slug", side_effect=lambda s, p: s),
            patch(
                "codemie.service.assistant.assistant_version_service." "AssistantVersionService.create_initial_version"
            ),
            patch(
                "codemie.service.guardrail.guardrail_service.GuardrailService." "sync_guardrail_assignments_for_entity"
            ),
            patch("codemie.service.monitoring.base_monitoring_service.send_log_metric"),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
                created = await ac.post(
                    _CREATE_URL,
                    headers={"Authorization": "Bearer testtoken"},
                    json=_payload("Never Submitted"),
                )
    finally:
        app.dependency_overrides.clear()

    assert created.status_code == status.HTTP_200_OK, created.text
    # `response_model_by_alias=True` -> the field ships as `assistantId`.
    assistant_id = created.json()["assistantId"]

    with mm_publish_db.connect() as conn:
        stored = (
            conn.execute(
                text(
                    "select marketplace_status, is_global, marketplace_submitted_at, "
                    "       marketplace_version, marketplace_decided_at "
                    "  from codemie.assistants where id = :i"
                ),
                {"i": assistant_id},
            )
            .mappings()
            .one()
        )

    assert stored["marketplace_status"] != "APPROVED", (
        "the create path persisted the review state machine's TERMINAL state for " "an assistant nobody submitted"
    )
    assert stored["marketplace_status"] == "DRAFT"
    assert stored["is_global"] is False
    assert stored["marketplace_submitted_at"] is None
    assert stored["marketplace_version"] == 0
    assert stored["marketplace_decided_at"] is None


def test_the_column_server_default_is_draft(mm_publish_db) -> None:
    """An INSERT that omits the column entirely still lands on DRAFT.

    The ORM default and the column default are independent. Any writer that
    does not go through SQLModel -- a migration, a bulk import, psql -- gets
    whatever the column says, so fixing only the Python side would leave the
    defect reachable.
    """
    with mm_publish_db.connect() as conn:
        conn.execute(
            text(
                "insert into codemie.assistants "
                "(id, name, description, system_prompt, project, shared, is_react, "
                " created_date, creator, is_global, categories, version_count) "
                "values ('raw-insert', 'raw', 'd', 'p', 'demo', true, true, now(), "
                "        'someone', false, '[]'::jsonb, 1)"
            )
        )
        conn.commit()
        stored = conn.execute(
            text("select marketplace_status from codemie.assistants where id = 'raw-insert'")
        ).scalar()
        conn.execute(text("delete from codemie.assistants where id = 'raw-insert'"))
        conn.commit()

    assert stored != "APPROVED", "the column default still stamps APPROVED on every insert"
    assert stored == "DRAFT"
