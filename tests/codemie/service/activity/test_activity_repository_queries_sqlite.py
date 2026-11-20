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


"""Activity reads by global scope and project-instance ID against in-memory SQLite.

Covers what mocked-session tests cannot: tie ordering across pages, totals under filters, and what a row shows
after its actor, project or entity changed. Postgres-only behaviour (the migration, JSONB operators) is not
exercised here; sqlite runs the same SQL the repository builds for these queries.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch
from uuid import UUID

import pytest
from sqlalchemy import delete
from sqlmodel import Session

from codemie.rest_api.models.user_management import UserDB
from codemie.service.activity.activity_models import ActivityEvent
from codemie.service.activity.activity_repository import SQLActivityEventRepository
from tests.codemie.sqlite_harness import constrained_sqlite_engine

NOON = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
PROJECT_ID = UUID("4d35d8a6-99eb-4a39-8dc5-49ad1c9ee37c")
OTHER_PROJECT_ID = UUID("e89e1d73-3cf9-46d1-97d4-754f71ad6a2a")


@pytest.fixture
def session():
    engine = constrained_sqlite_engine()
    ActivityEvent.metadata.create_all(engine, tables=[UserDB.__table__, ActivityEvent.__table__])
    with (
        Session(engine) as db_session,
        patch("codemie.service.activity.activity_repository.config") as mock_config,
    ):
        mock_config.ACTIVITY_EVENTS_ENABLED = True
        yield db_session
    engine.dispose()


def _event(event_id: str, **kwargs) -> ActivityEvent:
    defaults = {
        "domain": "project_management",
        "event_type": "project.updated",
        "created_at": NOON,
        "project_instance_id": PROJECT_ID,
    }
    return ActivityEvent(id=event_id, **{**defaults, **kwargs})


def _user(user_id: str = "u-1", name: str | None = "Live Name") -> UserDB:
    return UserDB(id=user_id, username=f"user-{user_id}", email=f"{user_id}@live.test", name=name)


@pytest.mark.parametrize("sort_dir", ["asc", "desc"])
def test_rows_sharing_a_timestamp_appear_exactly_once_across_pages(session, sort_dir):
    session.add_all([_event(f"evt-{i}", project_name="acme") for i in range(5)])
    session.commit()

    seen, totals = [], set()
    for offset in (0, 2, 4):
        rows, total = SQLActivityEventRepository().find_by_project_instance_id(
            PROJECT_ID, sort_dir=sort_dir, limit=2, offset=offset, session=session
        )
        seen.extend(row.id for row in rows)
        totals.add(total)

    assert sorted(seen) == [f"evt-{i}" for i in range(5)]
    assert len(seen) == len(set(seen))
    assert totals == {5}


def test_filtered_total_counts_every_match_not_the_page(session):
    session.add_all(
        [
            _event(f"b-{i}", project_name="acme", domain="budget_management", event_type="budget.created")
            for i in range(3)
        ]
        + [_event("s-1", project_name="acme")]
        + [
            _event(
                "other-1",
                project_name="acme",
                project_instance_id=OTHER_PROJECT_ID,
                domain="budget_management",
                event_type="budget.created",
            )
        ]
    )
    session.commit()
    repo = SQLActivityEventRepository()

    by_category, total_by_category = repo.find_by_project_instance_id(
        PROJECT_ID, category=["Budget"], limit=2, offset=0, session=session
    )
    by_domain, total_by_domain = repo.find_by_project_instance_id(
        PROJECT_ID, domain=["budget_management"], limit=2, offset=0, session=session
    )
    everything, total = repo.find_by_project_instance_id(PROJECT_ID, limit=10, offset=0, session=session)

    assert (len(by_category), total_by_category) == (2, 3)
    assert (len(by_domain), total_by_domain) == (2, 3)
    assert (len(everything), total) == (4, 4)
    assert {row.project_name for row in everything} == {"acme"}


def test_project_events_are_isolated_by_generation_and_exclude_legacy_rows(session):
    session.add_all(
        [
            _event("same-name-current", project_name="acme"),
            _event("same-name-other-generation", project_name="acme", project_instance_id=OTHER_PROJECT_ID),
            _event("legacy", project_name="acme", project_instance_id=None),
        ]
    )
    session.commit()

    rows, total = SQLActivityEventRepository().find_by_project_instance_id(
        PROJECT_ID, limit=10, offset=0, session=session
    )

    assert total == 1
    assert {row.id for row in rows} == {"same-name-current"}


def test_project_event_metadata_isolated_by_generation(session):
    session.add_all(
        [
            _event("current", project_name="acme", event_type="project.created"),
            _event(
                "same-name-old-generation",
                project_name="acme",
                project_instance_id=OTHER_PROJECT_ID,
                event_type="project.deleted",
            ),
        ]
    )
    session.commit()

    event_types = SQLActivityEventRepository().get_distinct_event_types_by_project_instance_id(PROJECT_ID, session)

    assert event_types == ["project.created"]


def test_project_event_metadata_returns_only_domains_for_selected_generation(session):
    session.add_all(
        [
            _event("old-generation", project_name="acme", project_instance_id=OTHER_PROJECT_ID, domain="assistant"),
            _event("new-budget-1", project_name="acme", domain="budget_management"),
            _event("new-budget-2", project_name="acme", domain="budget_management"),
            _event("new-project", project_name="acme", domain="project_management"),
            _event("legacy", project_name="acme", project_instance_id=None, domain="user_management"),
        ]
    )
    session.commit()

    domains = SQLActivityEventRepository().get_distinct_domains_by_project_instance_id(PROJECT_ID, session)

    assert domains == ["budget_management", "project_management"]


def test_category_filter_returns_budget_and_member_spend_enforcement_events(session):
    session.add_all(
        [
            _event("e-1", project_name="acme", event_type="project.member_spend_enforcement.updated"),
            _event("e-2", project_name="acme", domain="budget_management", event_type="budget.created"),
            _event("e-3", project_name="acme", event_type="project.updated"),
        ]
    )
    session.commit()

    rows, total = SQLActivityEventRepository().find_by_project_instance_id(
        PROJECT_ID, category=["Budget"], limit=10, offset=0, session=session
    )

    assert total == 2
    assert {row.id for row in rows} == {"e-1", "e-2"}


def test_events_of_a_deleted_generation_are_still_returned(session):
    # The event's instance ID remains queryable after its applications row is hard-deleted.
    session.add(_event("evt-1", project_name="deleted-project", event_type="project.deleted"))
    session.commit()

    rows, total = SQLActivityEventRepository().find_by_project_instance_id(
        PROJECT_ID, limit=10, offset=0, session=session
    )

    assert (total, [row.id for row in rows]) == (1, ["evt-1"])


def test_renamed_entity_keeps_the_label_recorded_at_event_time(session):
    session.add(_event("evt-1", project_name="acme", entity_type="assistant", entity_id="a-1", entity_label="Old Name"))
    session.commit()
    # The assistant is renamed later; nothing rewrites the event.
    session.add(_event("evt-2", project_name="acme", entity_type="assistant", entity_id="a-1", entity_label="New Name"))
    session.commit()

    rows, _ = SQLActivityEventRepository().find_by_project_instance_id(
        PROJECT_ID, sort_dir="asc", limit=10, offset=0, session=session
    )

    assert {row.id: row.entity_label for row in rows} == {"evt-1": "Old Name", "evt-2": "New Name"}


def test_find_all_reads_the_live_user_only_so_a_deleted_one_has_no_identity(session):
    session.add_all([_user("u-1"), _user("u-2", name="Doomed")])
    session.commit()
    session.add_all(
        [
            _event("live", actor_id="u-1", actor_email="snap-1@old.test", actor_display_name="Snap One"),
            _event("gone", actor_id="u-2", actor_email="snap-2@old.test", actor_display_name="Snap Two"),
            _event("system"),
        ]
    )
    session.commit()
    session.execute(delete(UserDB).where(UserDB.id == "u-2"))  # FK ON DELETE SET NULL clears events.actor_id
    session.commit()

    rows, total = SQLActivityEventRepository().find_all(sort_dir="asc", limit=10, offset=0, session=session)

    by_id = {row.id: row for row in rows}
    assert total == 3
    assert (by_id["live"].actor_email, by_id["live"].actor_name) == ("u-1@live.test", "Live Name")
    assert by_id["gone"].actor_id is None
    assert (by_id["gone"].actor_email, by_id["gone"].actor_name) == (None, None)
    assert (by_id["system"].actor_email, by_id["system"].actor_name) == (None, None)


def test_find_all_global_listing_keeps_its_pre_existing_fields(session):
    session.add(_user("u-1"))
    session.commit()
    session.add(
        _event(
            "evt-1",
            domain="user_management",
            event_type="user.created",
            entity_type="user",
            entity_id="u-9",
            entity_label="Nine",
            actor_id="u-1",
            attributes={"k": "v"},
        )
    )
    session.commit()

    (row,), total = SQLActivityEventRepository().find_all(limit=10, offset=0, session=session)

    assert total == 1
    assert (row.id, row.domain, row.event_type) == ("evt-1", "user_management", "user.created")
    assert (row.entity_type, row.entity_id, row.entity_label) == ("user", "u-9", "Nine")
    assert (row.actor_id, row.actor_email, row.actor_name) == ("u-1", "u-1@live.test", "Live Name")
    assert row.attributes == {"k": "v"}


def test_find_by_project_instance_id_shows_the_live_user_for_a_renamed_one(session):
    session.add(_user("u-1", name="Renamed Since"))
    session.commit()
    session.add(
        _event(
            "evt-1", project_name="acme", actor_id="u-1", actor_email="at-write@old.test", actor_display_name="At Write"
        )
    )
    session.commit()

    (row,), _ = SQLActivityEventRepository().find_by_project_instance_id(
        PROJECT_ID, limit=10, offset=0, session=session
    )

    assert (row.actor_email, row.actor_name) == ("u-1@live.test", "Renamed Since")


def test_find_by_project_instance_id_falls_back_to_the_snapshot_for_a_deleted_user(session):
    session.add(_user("u-2", name="Doomed"))
    session.commit()
    session.add(
        _event(
            "gone", project_name="acme", actor_id="u-2", actor_email="snap-2@old.test", actor_display_name="Snap Two"
        )
    )
    session.commit()
    session.execute(delete(UserDB).where(UserDB.id == "u-2"))  # FK ON DELETE SET NULL clears events.actor_id
    session.commit()

    (row,), _ = SQLActivityEventRepository().find_by_project_instance_id(
        PROJECT_ID, limit=10, offset=0, session=session
    )

    assert (row.actor_id, row.actor_email, row.actor_name) == (None, "snap-2@old.test", "Snap Two")
