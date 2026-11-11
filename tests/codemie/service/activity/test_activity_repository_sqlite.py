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

"""insert() against a real database: the caller's failures reach the caller, event-row failures do not."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import ForeignKey, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlmodel import Session

from codemie.service.activity.activity_models import ActivityDomain, ActivityEventCreate, UserManagementEvent
from codemie.service.activity.activity_repository import SQLActivityEventRepository
from tests.codemie.sqlite_harness import constrained_sqlite_engine


class _ProbeBase(DeclarativeBase):
    pass


class _Parent(_ProbeBase):
    __tablename__ = "parent"

    id: Mapped[int] = mapped_column(primary_key=True)


class _Child(_ProbeBase):
    __tablename__ = "child"

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey("parent.id"))


@pytest.fixture
def session():
    # No activity_events table: the event row itself always fails to insert.
    engine = constrained_sqlite_engine()
    _ProbeBase.metadata.create_all(engine)
    with (
        Session(engine) as db_session,
        patch("codemie.service.activity.activity_repository.config") as mock_config,
    ):
        mock_config.ACTIVITY_EVENTS_ENABLED = True
        yield db_session
    engine.dispose()


def _event() -> ActivityEventCreate:
    return ActivityEventCreate(domain=ActivityDomain.USER_MANAGEMENT, event_type=UserManagementEvent.USER_CREATED)


def test_insert_propagates_callers_pending_failure(session):
    session.add(_Child(id=1, parent_id=404))

    with pytest.raises(IntegrityError):
        SQLActivityEventRepository().insert(_event(), session)

    session.rollback()
    assert session.execute(text("SELECT 1")).scalar_one() == 1
    assert session.scalars(select(_Child)).all() == []


def test_insert_swallows_event_row_failure_and_caller_commits(session):
    session.add(_Parent(id=1))

    assert SQLActivityEventRepository().insert(_event(), session) is None

    session.commit()
    assert session.scalars(select(_Parent.id)).all() == [1]
