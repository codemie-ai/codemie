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

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from codemie.repository.llm_model_status_repository import LLMModelStatusRepository
from codemie.rest_api.models.llm_model_status import LLMModelStatus


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Scope table creation to only this table - SQLModel.metadata.create_all(engine)
    # would try to create every other registered SQLModel table too, some of which
    # use Postgres-only column types that SQLite can't create.
    LLMModelStatus.__table__.create(bind=engine)
    with Session(engine) as sess:
        yield sess
    engine.dispose()


def test_upsert_then_get_by_base_name_returns_row_with_status(session):
    LLMModelStatusRepository.upsert(
        session,
        base_name="gpt-4",
        status="disabled",
        admin_id="admin-1",
        admin_name="Admin One",
    )

    result = LLMModelStatusRepository.get_by_base_name(session, "gpt-4")

    assert result is not None
    assert result.base_name == "gpt-4"
    assert result.status == "disabled"
    assert result.updated_by_id == "admin-1"
    assert result.updated_by_name == "Admin One"


def test_upsert_populates_updated_at_on_first_insert(session):
    """CR-004: updated_at must not stay null on the very first status change."""
    LLMModelStatusRepository.upsert(
        session,
        base_name="gpt-4",
        status="disabled",
        admin_id="admin-1",
        admin_name="Admin One",
    )

    result = LLMModelStatusRepository.get_by_base_name(session, "gpt-4")

    assert result.created_at is not None
    assert result.updated_at is not None


def test_second_upsert_for_same_base_name_updates_in_place(session):
    LLMModelStatusRepository.upsert(
        session,
        base_name="gpt-4",
        status="disabled",
        admin_id="admin-1",
        admin_name="Admin One",
    )
    LLMModelStatusRepository.upsert(
        session,
        base_name="gpt-4",
        status="deprecated",
        admin_id="admin-2",
        admin_name="Admin Two",
    )

    all_rows = LLMModelStatusRepository.get_all(session)

    assert len(all_rows) == 1
    assert all_rows[0].status == "deprecated"
    assert all_rows[0].updated_by_id == "admin-2"
    assert all_rows[0].updated_by_name == "Admin Two"


def test_get_by_base_name_for_unknown_name_returns_none(session):
    result = LLMModelStatusRepository.get_by_base_name(session, "unknown-model")

    assert result is None


def test_upsert_retries_as_update_when_insert_races_on_unique_base_name(session):
    """Simulates the CR-002 concurrency gap: upsert's own SELECT sees `existing is None`
    (a stale read, as if a concurrent request already committed the row), so it attempts
    to INSERT and must recover from the resulting IntegrityError by retrying as an UPDATE
    instead of letting the exception bubble up as an unhandled 500.
    """
    # A concurrent request already persisted this base_name.
    LLMModelStatusRepository.upsert(
        session,
        base_name="gpt-4",
        status="enabled",
        admin_id="admin-1",
        admin_name="Admin One",
    )

    # Force upsert's initial existence check to observe a stale `None`, as a concurrent
    # first-time insert would after this session's own SELECT ran but before either commits.
    real_exec = session.exec
    stale_read = MagicMock()
    stale_read.first.return_value = None
    call_count = {"n": 0}

    def exec_with_first_call_stale(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return stale_read
        return real_exec(*args, **kwargs)

    session.exec = exec_with_first_call_stale

    result = LLMModelStatusRepository.upsert(
        session,
        base_name="gpt-4",
        status="disabled",
        admin_id="admin-2",
        admin_name="Admin Two",
    )

    session.exec = real_exec
    all_rows = LLMModelStatusRepository.get_all(session)

    assert result.status == "disabled"
    assert result.updated_by_id == "admin-2"
    # The race must resolve to a single row (an update), never a duplicate insert.
    assert len(all_rows) == 1
    assert all_rows[0].status == "disabled"


def test_get_all_returns_all_rows(session):
    LLMModelStatusRepository.upsert(
        session,
        base_name="gpt-4",
        status="enabled",
        admin_id="admin-1",
        admin_name="Admin One",
    )
    LLMModelStatusRepository.upsert(
        session,
        base_name="claude-3",
        status="deprecated",
        admin_id="admin-1",
        admin_name="Admin One",
    )

    all_rows = LLMModelStatusRepository.get_all(session)
    base_names = {row.base_name for row in all_rows}

    assert len(all_rows) == 2
    assert base_names == {"gpt-4", "claude-3"}
