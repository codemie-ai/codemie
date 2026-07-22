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

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.service.activity.activity_models import (
    ActivityEvent,
    ActivityEventCreate,
    ActivityDomain,
    UserManagementEvent,
    ActivityEntityType,
)
from codemie.service.activity.activity_repository import SQLActivityEventRepository


def _repo() -> SQLActivityEventRepository:
    return SQLActivityEventRepository()


def _create_dto(**kwargs) -> ActivityEventCreate:
    defaults = {
        "domain": ActivityDomain.USER_MANAGEMENT,
        "event_type": UserManagementEvent.USER_CREATED,
        "entity_type": ActivityEntityType.USER,
        "entity_id": "user-uuid-1",
        "actor_id": "actor-uuid-1",
    }
    defaults.update(kwargs)
    return ActivityEventCreate(**defaults)


def test_get_domain_mapping_groups_by_domain():
    session = MagicMock()
    session.execute.return_value.all.return_value = [
        ('budget_management', 'budget.created', 'budget'),
        ('budget_management', 'budget.updated', 'project_budget_group'),
        ('user_management', 'user.created', 'user'),
    ]

    result = _repo().get_domain_mapping(session)

    assert set(result['budget_management'].event_types) == {'budget.created', 'budget.updated'}
    assert set(result['budget_management'].entity_types) == {'budget', 'project_budget_group'}
    assert result['user_management'].event_types == ['user.created']
    assert result['user_management'].entity_types == ['user']


def test_insert_adds_and_flushes_event():
    session = MagicMock()
    dto = _create_dto()

    with patch("codemie.service.activity.activity_repository.config") as mock_config:
        mock_config.ACTIVITY_EVENTS_ENABLED = True
        result = _repo().insert(dto, session)

    session.add.assert_called_once()
    session.flush.assert_called_once()
    assert result.domain == ActivityDomain.USER_MANAGEMENT
    assert result.event_type == UserManagementEvent.USER_CREATED
    assert result.entity_type == ActivityEntityType.USER
    assert result.entity_id == "user-uuid-1"
    assert result.actor_id == "actor-uuid-1"


def test_insert_domain_level_event_with_no_entity():
    session = MagicMock()
    dto = _create_dto(entity_type=None, entity_id=None)

    with patch("codemie.service.activity.activity_repository.config") as mock_config:
        mock_config.ACTIVITY_EVENTS_ENABLED = True
        result = _repo().insert(dto, session)

    assert result.entity_type is None
    assert result.entity_id is None


@pytest.mark.asyncio
async def test_async_insert_adds_and_flushes_event():
    session = AsyncMock()
    # begin_nested() must return an async context manager, not a coroutine
    session.begin_nested = MagicMock(return_value=AsyncMock())
    dto = _create_dto()

    with patch("codemie.service.activity.activity_repository.config") as mock_config:
        mock_config.ACTIVITY_EVENTS_ENABLED = True
        result = await _repo().async_insert(dto, session)

    session.add.assert_called_once()
    session.flush.assert_called_once()
    assert result.domain == ActivityDomain.USER_MANAGEMENT


def test_find_by_entity_executes_query():
    session = MagicMock()
    mock_result = MagicMock()
    mock_result.all.return_value = []
    session.exec.return_value = mock_result

    results = _repo().find_by_entity("user", "user-uuid-1", limit=10, offset=0, session=session)

    session.exec.assert_called_once()
    assert results == []


def test_find_by_actor_executes_query():
    session = MagicMock()
    mock_result = MagicMock()
    mock_result.all.return_value = []
    session.exec.return_value = mock_result

    results = _repo().find_by_actor("actor-uuid-1", limit=10, offset=0, session=session)

    session.exec.assert_called_once()
    assert results == []


def test_find_by_domain_executes_query():
    session = MagicMock()
    mock_result = MagicMock()
    mock_result.all.return_value = []
    session.exec.return_value = mock_result

    from_dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
    to_dt = datetime(2026, 12, 31, tzinfo=timezone.utc)

    results = _repo().find_by_domain(
        "user_management", from_dt=from_dt, to_dt=to_dt, limit=20, offset=0, session=session
    )

    session.exec.assert_called_once()
    assert results == []


class TestFindAll:
    def _make_row(self, event_id="evt-1"):
        event = ActivityEvent(
            id=event_id,
            domain="user_management",
            event_type="user.created",
            entity_type="user",
            entity_id="u-1",
            actor_id="a-1",
            created_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )
        return (event, "admin@test.com", "Admin User")

    def test_find_all_returns_enriched_rows_and_count(self):
        session = MagicMock()
        row = self._make_row()
        count_result = MagicMock()
        count_result.scalar_one.return_value = 1
        data_result = MagicMock()
        data_result.all.return_value = [row]
        session.execute.side_effect = [count_result, data_result]

        results, total = _repo().find_all(limit=10, offset=0, session=session)

        assert total == 1
        assert len(results) == 1
        r = results[0]
        assert r.id == "evt-1"
        assert r.actor_email == "admin@test.com"
        assert r.actor_name == "Admin User"

    def test_find_all_with_no_results_returns_empty_list(self):
        session = MagicMock()
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        data_result = MagicMock()
        data_result.all.return_value = []
        session.execute.side_effect = [count_result, data_result]

        results, total = _repo().find_all(limit=10, offset=0, session=session)

        assert total == 0
        assert results == []

    def test_find_all_filters_are_passed_to_query(self):
        session = MagicMock()
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        data_result = MagicMock()
        data_result.all.return_value = []
        session.execute.side_effect = [count_result, data_result]

        _repo().find_all(
            actor_id="a-1",
            domain=["user_management"],
            event_type=["user.created"],
            entity_type=["user"],
            entity_id="u-1",
            limit=5,
            offset=0,
            session=session,
        )

        assert session.execute.call_count == 2

    def test_find_all_actor_email_is_none_when_actor_id_is_none(self):
        event = ActivityEvent(
            id="evt-2",
            domain="budget_management",
            event_type="budget.created",
            created_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )
        session = MagicMock()
        count_result = MagicMock()
        count_result.scalar_one.return_value = 1
        data_result = MagicMock()
        data_result.all.return_value = [(event, None, None)]
        session.execute.side_effect = [count_result, data_result]

        results, _ = _repo().find_all(limit=10, offset=0, session=session)

        assert results[0].actor_email is None
        assert results[0].actor_name is None


class TestGetDistinctValues:
    def test_get_distinct_domains_returns_sorted_strings(self):
        session = MagicMock()
        session.execute.return_value.all.return_value = [
            ("budget_management",),
            ("user_management",),
        ]
        result = _repo().get_distinct_domains(session)
        assert result == ["budget_management", "user_management"]

    def test_get_distinct_event_types_returns_sorted_strings(self):
        session = MagicMock()
        session.execute.return_value.all.return_value = [
            ("budget.created",),
            ("user.created",),
            ("user.login",),
        ]
        result = _repo().get_distinct_event_types(session)
        assert result == ["budget.created", "user.created", "user.login"]

    def test_get_distinct_entity_types_returns_sorted_strings(self):
        session = MagicMock()
        session.execute.return_value.all.return_value = [
            ("budget",),
            ("user",),
        ]
        result = _repo().get_distinct_entity_types(session)
        assert result == ["budget", "user"]

    def test_get_distinct_domains_returns_empty_list_when_table_empty(self):
        session = MagicMock()
        session.execute.return_value.all.return_value = []
        result = _repo().get_distinct_domains(session)
        assert result == []
