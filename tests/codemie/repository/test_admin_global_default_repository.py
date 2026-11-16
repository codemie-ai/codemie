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

from unittest.mock import MagicMock

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from codemie.repository.admin_global_default_repository import AdminGlobalDefaultRepository
from codemie.rest_api.models.admin_global_default import AdminGlobalDefault


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    AdminGlobalDefault.__table__.create(bind=engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


def test_set_replace_read_and_clear_single_choice(session):
    assert AdminGlobalDefaultRepository.get(session) is None

    first = AdminGlobalDefaultRepository.replace(session, "gpt-4", "admin-1", "Admin One")
    assert first.base_name == "gpt-4"
    assert first.updated_by_id == "admin-1"
    assert first.updated_by_name == "Admin One"
    assert first.created_at is not None
    assert first.updated_at is not None

    second = AdminGlobalDefaultRepository.replace(session, "claude-3", "admin-2", "Admin Two")
    assert second.id == first.id == 1
    assert second.base_name == "claude-3"
    assert second.updated_by_id == "admin-2"
    assert second.updated_by_name == "Admin Two"
    assert second.updated_at is not None
    assert len(session.query(AdminGlobalDefault).all()) == 1
    assert AdminGlobalDefaultRepository.get(session).base_name == "claude-3"

    AdminGlobalDefaultRepository.clear(session)
    assert AdminGlobalDefaultRepository.get(session) is None
    assert session.query(AdminGlobalDefault).count() == 0


def test_concurrent_first_write_retries_as_replace(session):
    AdminGlobalDefaultRepository.replace(session, "gpt-4", "admin-1", "Admin One")

    real_exec = session.exec
    stale_read = MagicMock()
    stale_read.first.return_value = None
    call_count = 0

    def first_read_stale(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return stale_read
        return real_exec(*args, **kwargs)

    session.exec = first_read_stale
    result = AdminGlobalDefaultRepository.replace(session, "claude-3", "admin-2", "Admin Two")
    session.exec = real_exec

    assert result.base_name == "claude-3"
    assert result.updated_by_id == "admin-2"
    assert session.query(AdminGlobalDefault).count() == 1


def test_read_error_is_not_hidden(session):
    session.exec = MagicMock(side_effect=RuntimeError("database unavailable"))

    with pytest.raises(RuntimeError, match="database unavailable"):
        AdminGlobalDefaultRepository.get(session)
