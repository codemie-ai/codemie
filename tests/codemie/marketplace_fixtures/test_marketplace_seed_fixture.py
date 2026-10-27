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

"""Self-test for the backend seeding fixtures (EXECUTION-PLAN 10.2, spec §4.4).

The Playwright side of 10.2 owns browser-visible seeding; this is its backend
counterpart, and it exists for the same reason: eleven downstream pytest cases
will assert on rows, so the thing that creates and removes those rows has to be
proved rather than trusted.

Three properties, matching the frontend fixture:

  1. the row is created **through the service layer**, not a hand-written
     INSERT, so an ORM/schema gap cannot hide behind the fixture;
  2. it is addressable by its own id and carries the run token in both `name`
     and `description`, so every verification can be
     ``where name like 'E2E <run_id>%'`` instead of the racy
     ``order by created_date desc limit 1``;
  3. teardown removes the row *and* its moderation rows, and is idempotent —
     the property that lets the suite run twice against one database.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlmodel import Session

from codemie.rest_api.models.assistant import Assistant


def test_seeded_assistant_is_addressable_by_id_and_carries_the_run_token(marketplace_assistant, mm_run_id):
    seeded = marketplace_assistant("fixture self test")

    fetched = Assistant.find_by_id(seeded.id)
    assert fetched is not None, "the fixture reported a create but the row is not readable back"
    assert fetched.name == seeded.name
    assert mm_run_id in fetched.name, "seeded name must carry the per-run token"
    assert mm_run_id in (fetched.description or ""), "seeded description must carry the same token"


def test_the_run_token_predicate_finds_the_seeded_row(marketplace_assistant, mm_run_id, mm_engine):
    seeded = marketplace_assistant("token predicate")

    with Session(mm_engine) as session:
        ids = [
            row[0]
            for row in session.execute(
                text("select id from assistants where name like :pattern"),
                {"pattern": f"E2E {mm_run_id}%"},
            )
        ]

    # Assert on OUR OWN id (spec §4.2). Never `rows.first()`, never a count.
    assert seeded.id in ids


def test_pending_seed_reaches_pending_review_through_the_queue_service(marketplace_pending_assistant, mm_engine):
    seeded = marketplace_pending_assistant("pending seed")

    with Session(mm_engine) as session:
        status, is_global = session.execute(
            text("select marketplace_status, is_global from assistants where id = :i"),
            {"i": seeded.id},
        ).one()
        decisions = (
            session.execute(
                text("select decision from mm_review_decision where entity_id = :i"),
                {"i": seeded.id},
            )
            .scalars()
            .all()
        )

    assert status == "PENDING_REVIEW"
    assert is_global is False, "a submission must stay private until a moderator approves it"
    assert "SUBMIT" in decisions, "the queue service recorded no SUBMIT decision"


def test_teardown_removes_the_row_and_its_moderation_rows_and_is_idempotent(
    marketplace_pending_assistant, mm_teardown, mm_engine
):
    seeded = marketplace_pending_assistant("teardown")

    mm_teardown([seeded.id])
    # Twice: a teardown that is not idempotent cannot survive a re-run, and a
    # crashed test would leave the second call to clean up after it.
    mm_teardown([seeded.id])

    with Session(mm_engine) as session:
        rows = session.execute(text("select count(*) from assistants where id = :i"), {"i": seeded.id}).scalar_one()
        decisions = session.execute(
            text("select count(*) from mm_review_decision where entity_id = :i"), {"i": seeded.id}
        ).scalar_one()
        activity = session.execute(
            text("select count(*) from activity_events where entity_id = :i"), {"i": seeded.id}
        ).scalar_one()

    assert rows == 0, "the assistant row survived teardown"
    assert decisions == 0, "mm_review_decision rows survived teardown"
    assert activity == 0, "activity_events rows survived teardown"
