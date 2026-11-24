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

"""A withdraw by an actor with no `users` row commits instead of violating a FK (EPMCDME-17100).

`assistants.marketplace_decided_by`, `mm_review_decision.decided_by_id` and `activity_events.actor_id` all FK
`users.id`. An authenticated actor whose identity provider never provisioned a `users` row used to abort the
whole decision with `ForeignKeyViolation`; the id is now written as NULL and who acted stays on record through
`decided_by_email`.

Only a real, migrated PostgreSQL enforces those FKs, so these tests use `mm_pg_engine` and **skip** when the
database is unreachable. A skip is not a pass: run them locally against the dev stack (or `MM_TEST_PG_URL`)
before the MR, as `.ai-run/guides/testing/local-verification.md` describes.
"""

from __future__ import annotations

from typing import Iterator
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from codemie.service.marketplace_management.queue_service import MarketplaceQueueService

_SCHEMA = "codemie"
_ASSISTANT_ID = "a-no-user-row"


@pytest.fixture
def db(mm_pg_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """Point the application's `get_session()` at the throwaway database and seed an APPROVED assistant."""
    from codemie.clients import postgres as pg

    monkeypatch.setattr(pg.PostgresClient, "get_engine", classmethod(lambda cls: mm_pg_engine))
    _clean(mm_pg_engine)
    with mm_pg_engine.connect() as conn:
        conn.execute(
            text(
                "insert into codemie.assistants "
                "(id, name, description, system_prompt, project, shared, is_react, "
                " created_date, creator, is_global, categories, marketplace_status, "
                " marketplace_version, unique_users_count, unique_likes_count, "
                " unique_dislikes_count, version_count) "
                "values (:id, 'Approved helper', 'a seeded assistant', 'you are seeded', 'demo', true, true, "
                "        now(), 'seed-creator', true, '[]'::jsonb, 'APPROVED', 0, 3, 1, 0, 1)"
            ),
            {"id": _ASSISTANT_ID},
        )
        conn.commit()
    try:
        yield mm_pg_engine
    finally:
        _clean(mm_pg_engine)


def _clean(engine: Engine) -> None:
    with engine.connect() as conn:
        for tbl in ("mm_review_decision", "activity_events", "assistants", "users"):
            conn.execute(text(f"delete from {_SCHEMA}.{tbl}"))
        conn.commit()


def _seed_user(engine: Engine, user_id: str) -> None:
    with engine.connect() as conn:
        conn.execute(
            text(
                "insert into codemie.users (id, username, email, name, is_admin, is_active, date) "
                "values (:id, :id, :email, 'Known Admin', true, true, now())"
            ),
            {"id": user_id, "email": f"{user_id}@codemie.ai"},
        )
        conn.commit()


def _withdraw(*, actor: str, decided_by_id: str, decided_by_email: str | None) -> None:
    # The index projection and metrics are outside the transaction under test.
    with (
        patch.object(MarketplaceQueueService, "_side_effects", return_value=None),
        patch("codemie.service.marketplace_management.queue_service.track_marketplace_metric"),
    ):
        MarketplaceQueueService().withdraw(
            assistant_id=_ASSISTANT_ID, actor=actor, decided_by_id=decided_by_id, decided_by_email=decided_by_email
        )


def _stored(engine: Engine):
    with engine.connect() as conn:
        assistant = (
            conn.execute(
                text("select marketplace_status, marketplace_decided_by from codemie.assistants where id = :id"),
                {"id": _ASSISTANT_ID},
            )
            .mappings()
            .one()
        )
        ledger = (
            conn.execute(
                text("select decided_by_id, decided_by_email from codemie.mm_review_decision where entity_id = :id"),
                {"id": _ASSISTANT_ID},
            )
            .mappings()
            .one()
        )
    return assistant, ledger


def test_withdraw_by_an_actor_without_a_users_row_commits_with_a_null_id(db: Engine) -> None:
    _withdraw(actor="ghost", decided_by_id="no-such-user", decided_by_email="ghost@x.io")

    assistant, ledger = _stored(db)
    assert (assistant["marketplace_status"], assistant["marketplace_decided_by"]) == ("WITHDRAWN", None)
    assert (ledger["decided_by_id"], ledger["decided_by_email"]) == (None, "ghost@x.io")


def test_withdraw_by_an_actor_with_a_users_row_keeps_the_id(db: Engine) -> None:
    _seed_user(db, "known-admin")

    _withdraw(actor="known", decided_by_id="known-admin", decided_by_email="known-admin@codemie.ai")

    assistant, ledger = _stored(db)
    assert (assistant["marketplace_status"], assistant["marketplace_decided_by"]) == ("WITHDRAWN", "known-admin")
    assert ledger["decided_by_id"] == "known-admin"


def test_withdraw_without_a_users_row_or_email_records_the_actor_string(db: Engine) -> None:
    _withdraw(actor="ghost-username", decided_by_id="no-such-user", decided_by_email=None)

    assistant, ledger = _stored(db)
    assert (assistant["marketplace_status"], assistant["marketplace_decided_by"]) == ("WITHDRAWN", None)
    assert (ledger["decided_by_id"], ledger["decided_by_email"]) == (None, "ghost-username")
