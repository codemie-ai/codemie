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
from uuid import UUID

import pytest
from sqlalchemy.exc import IntegrityError

from codemie.service.activity.activity_models import (
    ActivityEvent,
    ActivityEventCreate,
    ActivityDomain,
    AssistantEvent,
    UserManagementEvent,
    ActivityEntityType,
)
from codemie.service.activity.activity_repository import SQLActivityEventRepository

PROJECT_ID = UUID("4d35d8a6-99eb-4a39-8dc5-49ad1c9ee37c")


def _repo() -> SQLActivityEventRepository:
    return SQLActivityEventRepository()


def _create_dto(**kwargs) -> ActivityEventCreate:
    defaults = {
        "domain": ActivityDomain.USER_MANAGEMENT,
        "event_type": UserManagementEvent.USER_CREATED,
        "entity_type": ActivityEntityType.USER,
        "entity_id": "user-uuid-1",
        "actor_id": "actor-uuid-1",
        "actor_email": "actor@example.com",
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


def test_get_distinct_event_types_by_project_returns_event_types():
    session = MagicMock()
    session.execute.return_value.all.return_value = [
        ('budget.created',),
        ('project.updated',),
    ]

    result = _repo().get_distinct_event_types_by_project_instance_id(PROJECT_ID, session)

    assert result == ['budget.created', 'project.updated']


def test_get_distinct_domains_by_project_returns_domains():
    session = MagicMock()
    session.execute.return_value.all.return_value = [
        ('budget_management',),
        ('project_management',),
    ]

    result = _repo().get_distinct_domains_by_project_instance_id(PROJECT_ID, session)

    assert result == ['budget_management', 'project_management']


def test_insert_adds_and_flushes_event():
    session = MagicMock()
    dto = _create_dto()

    with patch("codemie.service.activity.activity_repository.config") as mock_config:
        mock_config.ACTIVITY_EVENTS_ENABLED = True
        result = _repo().insert(dto, session)

    session.add.assert_called_once()
    assert session.flush.call_count == 2
    assert result.domain == ActivityDomain.USER_MANAGEMENT
    assert result.event_type == UserManagementEvent.USER_CREATED
    assert result.entity_type == ActivityEntityType.USER
    assert result.entity_id == "user-uuid-1"
    assert result.actor_id == "actor-uuid-1"


def test_insert_strict_returns_none_and_adds_nothing_when_disabled():
    session = MagicMock()

    with patch("codemie.service.activity.activity_repository.config") as mock_config:
        mock_config.ACTIVITY_EVENTS_ENABLED = False
        result = _repo().insert_strict(_create_dto(), session)

    assert result is None
    session.add.assert_not_called()
    session.flush.assert_not_called()


def test_insert_strict_adds_and_flushes_in_callers_transaction():
    session = MagicMock()
    dto = _create_dto(domain=ActivityDomain.ASSISTANT, event_type=AssistantEvent.GRANT_ADDED)

    with patch("codemie.service.activity.activity_repository.config") as mock_config:
        mock_config.ACTIVITY_EVENTS_ENABLED = True
        result = _repo().insert_strict(dto, session)

    session.add.assert_called_once_with(result)
    session.flush.assert_called_once()
    session.begin_nested.assert_not_called()
    assert result.domain == "assistant"
    assert result.event_type == "assistant.grant.added"


def test_insert_strict_reraises_flush_failure_while_insert_swallows_it():
    session = MagicMock()
    session.flush.side_effect = RuntimeError("db down")

    with patch("codemie.service.activity.activity_repository.config") as mock_config:
        mock_config.ACTIVITY_EVENTS_ENABLED = True
        with pytest.raises(RuntimeError, match="db down"):
            _repo().insert_strict(_create_dto(), session)
        session.flush.side_effect = [None, RuntimeError("db down")]
        assert _repo().insert(_create_dto(), session) is None


def test_insert_disabled_does_not_flush():
    session = MagicMock()

    with patch("codemie.service.activity.activity_repository.config") as mock_config:
        mock_config.ACTIVITY_EVENTS_ENABLED = False
        result = _repo().insert(_create_dto(), session)

    assert result is None
    session.flush.assert_not_called()


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
    assert session.flush.await_count == 2
    assert result.domain == ActivityDomain.USER_MANAGEMENT


@pytest.mark.asyncio
async def test_async_insert_strict_adds_and_flushes_without_savepoint():
    session = AsyncMock()
    session.add = MagicMock()
    session.begin_nested = MagicMock()
    dto = _create_dto()

    with patch("codemie.service.activity.activity_repository.config") as mock_config:
        mock_config.ACTIVITY_EVENTS_ENABLED = True
        result = await _repo().async_insert_strict(dto, session)

    session.begin_nested.assert_not_called()
    session.add.assert_called_once()
    session.flush.assert_awaited_once()
    assert result.domain == ActivityDomain.USER_MANAGEMENT


@pytest.mark.asyncio
async def test_async_insert_strict_propagates_flush_error():
    session = AsyncMock()
    session.add = MagicMock()
    session.flush.side_effect = RuntimeError("flush failed")
    dto = _create_dto()

    with patch("codemie.service.activity.activity_repository.config") as mock_config:
        mock_config.ACTIVITY_EVENTS_ENABLED = True
        with pytest.raises(RuntimeError, match="flush failed"):
            await _repo().async_insert_strict(dto, session)


@pytest.mark.asyncio
async def test_async_insert_strict_returns_none_when_events_disabled():
    session = AsyncMock()
    session.add = MagicMock()
    dto = _create_dto()

    with patch("codemie.service.activity.activity_repository.config") as mock_config:
        mock_config.ACTIVITY_EVENTS_ENABLED = False
        result = await _repo().async_insert_strict(dto, session)

    assert result is None
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


def test_hosted_application_domain_and_entity_type_exist():
    assert ActivityDomain.HOSTED_APPLICATION == "hosted_application"
    assert ActivityEntityType.HOSTED_APPLICATION == "hosted_application"


@pytest.mark.asyncio
async def test_async_insert_propagates_callers_flush_failure():
    session = AsyncMock()
    session.begin_nested = MagicMock(return_value=AsyncMock())
    session.flush.side_effect = IntegrityError("INSERT", {}, Exception("FOREIGN KEY constraint failed"))

    with patch("codemie.service.activity.activity_repository.config") as mock_config:
        mock_config.ACTIVITY_EVENTS_ENABLED = True
        with pytest.raises(IntegrityError):
            await _repo().async_insert(_create_dto(), session)

    session.begin_nested.assert_not_called()


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
            actor_id=["a-1"],
            domain=["user_management"],
            event_type=["user.created"],
            entity_type=["user"],
            entity_id="u-1",
            limit=5,
            offset=0,
            session=session,
        )

        assert session.execute.call_count == 2

    def test_find_all_accepts_multiple_actors_and_system_events(self):
        session = MagicMock()
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        data_result = MagicMock()
        data_result.all.return_value = []
        session.execute.side_effect = [count_result, data_result]

        _repo().find_all(
            actor_id=["a-1", "a-2"],
            include_system=True,
            limit=5,
            offset=0,
            session=session,
        )

        assert session.execute.call_count == 2

    def test_find_all_query_has_no_project_name_filter_or_secondary_sort(self):
        """Regression guard for AC10: find_all's generated SQL must never gain a
        project_name filter or an id tie-break, since it is the original global
        listing query and must stay behaviorally unchanged by this plan."""
        session = MagicMock()
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        data_result = MagicMock()
        data_result.all.return_value = []
        session.execute.side_effect = [count_result, data_result]

        _repo().find_all(
            actor_id=["a-1"],
            domain=["user_management"],
            limit=10,
            offset=0,
            session=session,
        )

        data_stmt = session.execute.call_args_list[1].args[0]
        sql = str(data_stmt)
        where_clause = sql.split("WHERE")[1].split("ORDER BY")[0]
        assert "project_name" not in where_clause

        order_by_clause = sql.split("ORDER BY")[1].split("LIMIT")[0].strip()
        assert order_by_clause == "activity_events.created_at DESC"
        assert "," not in order_by_clause

    def test_find_all_reads_the_live_user_columns_without_the_snapshot(self):
        """The global list reads the live user only, so a deleted actor gets null name and email as before."""
        session = MagicMock()
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        data_result = MagicMock()
        data_result.all.return_value = []
        session.execute.side_effect = [count_result, data_result]

        _repo().find_all(limit=10, offset=0, session=session)

        select_list = str(session.execute.call_args_list[1].args[0]).split("FROM")[0]
        assert "users.email AS actor_email" in select_list
        assert "users.name AS actor_name" in select_list
        assert "coalesce" not in select_list

    def test_find_all_row_mapping_passes_a_null_identity_through_for_a_deleted_user(self):
        """The query returns null for a deleted user, even if the event row still holds a snapshot."""
        event = ActivityEvent(
            id="evt-3",
            domain="user_management",
            event_type="user.created",
            actor_email="snap@test.com",
            actor_display_name="Snapshot Name",
            created_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )
        session = MagicMock()
        count_result = MagicMock()
        count_result.scalar_one.return_value = 1
        data_result = MagicMock()
        data_result.all.return_value = [(event, None, None)]
        session.execute.side_effect = [count_result, data_result]

        results, _ = _repo().find_all(limit=10, offset=0, session=session)

        assert (results[0].actor_id, results[0].actor_email, results[0].actor_name) == (None, None, None)

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


class TestFindByProjectInstanceId:
    def _make_row(self, event_id="evt-1", project_name="acme"):
        event = ActivityEvent(
            id=event_id,
            domain="user_management",
            event_type="user.created",
            entity_type="user",
            entity_id="u-1",
            actor_id="a-1",
            project_name=project_name,
            project_instance_id=PROJECT_ID,
            created_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )
        return (event, "admin@test.com", "Admin User")

    def test_find_by_project_instance_id_returns_enriched_rows_and_count(self):
        session = MagicMock()
        row = self._make_row()
        count_result = MagicMock()
        count_result.scalar_one.return_value = 1
        data_result = MagicMock()
        data_result.all.return_value = [row]
        session.execute.side_effect = [count_result, data_result]

        with patch("codemie.service.activity.activity_repository.config") as mock_config:
            mock_config.ACTIVITY_EVENTS_ENABLED = True
            results, total = _repo().find_by_project_instance_id(PROJECT_ID, limit=10, offset=0, session=session)

        assert total == 1
        assert len(results) == 1
        r = results[0]
        assert r.id == "evt-1"
        assert r.project_name == "acme"
        assert r.actor_email == "admin@test.com"
        assert r.actor_name == "Admin User"

    def test_find_by_project_instance_id_filters_are_passed_to_query(self):
        session = MagicMock()
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        data_result = MagicMock()
        data_result.all.return_value = []
        session.execute.side_effect = [count_result, data_result]

        with patch("codemie.service.activity.activity_repository.config") as mock_config:
            mock_config.ACTIVITY_EVENTS_ENABLED = True
            _repo().find_by_project_instance_id(
                PROJECT_ID,
                domain=["user_management"],
                event_type=["user.created"],
                actor_id="a-1",
                from_dt=datetime(2026, 1, 1, tzinfo=timezone.utc),
                to_dt=datetime(2026, 2, 1, tzinfo=timezone.utc),
                limit=5,
                offset=0,
                session=session,
            )

        assert session.execute.call_count == 2
        for stmt in (call.args[0] for call in session.execute.call_args_list):
            where_clause = str(stmt).split("WHERE")[1].split("ORDER BY")[0]
            assert "activity_events.actor_id = :actor_id_1" in where_clause
            assert "activity_events.created_at >= :created_at_1" in where_clause
            assert "activity_events.created_at <= :created_at_2" in where_clause
            assert "activity_events.domain IN" in where_clause
            assert "activity_events.event_type IN" in where_clause

    def test_find_by_project_instance_id_returns_empty_when_activity_events_disabled(self):
        session = MagicMock()

        with patch("codemie.service.activity.activity_repository.config") as mock_config:
            mock_config.ACTIVITY_EVENTS_ENABLED = False
            results, total = _repo().find_by_project_instance_id(PROJECT_ID, limit=10, offset=0, session=session)

        assert results == []
        assert total == 0
        session.execute.assert_not_called()

    def test_find_by_project_prefers_live_actor_fields_over_snapshot(self):
        session = MagicMock()
        event = ActivityEvent(
            id="evt-3",
            domain="user_management",
            event_type="user.created",
            entity_type="user",
            entity_id="u-1",
            actor_id="a-1",
            project_name="acme",
            project_instance_id=PROJECT_ID,
            created_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )
        count_result = MagicMock()
        count_result.scalar_one.return_value = 1
        data_result = MagicMock()
        data_result.all.return_value = [(event, "live@x.com", "Live Name")]
        session.execute.side_effect = [count_result, data_result]

        with patch("codemie.service.activity.activity_repository.config") as mock_config:
            mock_config.ACTIVITY_EVENTS_ENABLED = True
            results, _ = _repo().find_by_project_instance_id(PROJECT_ID, limit=10, offset=0, session=session)

        assert results[0].actor_email == "live@x.com"
        assert results[0].actor_name == "Live Name"

        # The mocked row cannot fail on SQL, so pin the live-first coalesce order on the statement itself.
        sql = str(session.execute.call_args_list[1].args[0])
        assert "coalesce(users.email, activity_events.actor_email) AS actor_email" in sql
        assert "coalesce(users.name, activity_events.actor_display_name) AS actor_name" in sql

    def test_find_by_project_instance_id_filters_by_id_and_has_secondary_sort(self):
        """Generation reads must filter by opaque ID and order stably for pagination."""
        session = MagicMock()
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        data_result = MagicMock()
        data_result.all.return_value = []
        session.execute.side_effect = [count_result, data_result]

        with patch("codemie.service.activity.activity_repository.config") as mock_config:
            mock_config.ACTIVITY_EVENTS_ENABLED = True
            _repo().find_by_project_instance_id(PROJECT_ID, limit=10, offset=0, session=session)

        data_stmt = session.execute.call_args_list[1].args[0]
        sql = str(data_stmt)
        where_clause = sql.split("WHERE")[1].split("ORDER BY")[0]
        assert "activity_events.project_instance_id = :project_instance_id_1" in where_clause
        assert "activity_events.project_name =" not in where_clause

        order_by_clause = sql.split("ORDER BY")[1].split("LIMIT")[0].strip()
        assert order_by_clause == "activity_events.created_at DESC, activity_events.id DESC"

    def test_find_by_project_instance_id_excludes_legacy_unattributed_rows(self):
        """Events without the generation ID cannot enter a project-scoped result."""
        session = MagicMock()
        acme_row = self._make_row(event_id="evt-acme", project_name="acme")
        count_result = MagicMock()
        count_result.scalar_one.return_value = 1
        data_result = MagicMock()
        # SQL equality excludes NULL-attributed rows; the query condition is
        # asserted below.
        data_result.all.return_value = [acme_row]
        session.execute.side_effect = [count_result, data_result]

        with patch("codemie.service.activity.activity_repository.config") as mock_config:
            mock_config.ACTIVITY_EVENTS_ENABLED = True
            results, total = _repo().find_by_project_instance_id(PROJECT_ID, limit=10, offset=0, session=session)

        assert total == 1
        assert len(results) == 1
        assert results[0].project_name == "acme"
        assert all(r.project_name is not None for r in results)

        data_stmt = session.execute.call_args_list[1].args[0]
        where_clause = str(data_stmt).split("WHERE")[1].split("ORDER BY")[0]
        assert "activity_events.project_instance_id = :project_instance_id_1" in where_clause
        assert "activity_events.project_name =" not in where_clause

    def test_find_by_project_instance_id_category_filter_resolves_to_event_types_via_taxonomy(self):
        """Category filtering must resolve to event_type values via activity_taxonomy's
        CATEGORY_EVENT_TYPES, independent of domain. "Budget" resolves to the
        BudgetManagementEvent event_type set."""
        session = MagicMock()
        row = self._make_row()
        count_result = MagicMock()
        count_result.scalar_one.return_value = 1
        data_result = MagicMock()
        data_result.all.return_value = [row]
        session.execute.side_effect = [count_result, data_result]

        with patch("codemie.service.activity.activity_repository.config") as mock_config:
            mock_config.ACTIVITY_EVENTS_ENABLED = True
            results, total = _repo().find_by_project_instance_id(
                PROJECT_ID, category=["Budget"], limit=10, offset=0, session=session
            )

        assert total == 1
        assert len(results) == 1

        count_stmt = session.execute.call_args_list[0].args[0]
        data_stmt = session.execute.call_args_list[1].args[0]
        for stmt in (count_stmt, data_stmt):
            sql = str(stmt)
            where_clause = sql.split("WHERE")[1].split("ORDER BY")[0] if "ORDER BY" in sql else sql.split("WHERE")[1]
            assert "event_type IN" in where_clause
            params = stmt.compile().params
            resolved_types: set[str] = set()
            for k, v in params.items():
                if not k.startswith("event_type_"):
                    continue
                if isinstance(v, (list, set, tuple)):
                    resolved_types.update(v)
                else:
                    resolved_types.add(v)
            assert "budget.created" in resolved_types
            # AC17: a project-domain event categorized Budget is found by the same category filter.
            assert "project.member_spend_enforcement.updated" in resolved_types

    def test_find_by_project_instance_id_raises_for_unknown_category_instead_of_dropping_it(self):
        session = MagicMock()

        with patch("codemie.service.activity.activity_repository.config") as mock_config:
            mock_config.ACTIVITY_EVENTS_ENABLED = True
            with pytest.raises(ValueError, match="NoSuchCategory"):
                _repo().find_by_project_instance_id(
                    PROJECT_ID, category=["Budget", "NoSuchCategory"], limit=10, offset=0, session=session
                )

        session.execute.assert_not_called()

    def test_find_by_project_paginated_calls_use_consistent_tie_break_ordering(self):
        """Regression guard for AC12: pagination must not gap or repeat rows
        that share a created_at timestamp. Proves the ORDER BY clause
        (created_at, id) is identical across separate paginated calls, i.e. the
        ordering key is stable and not offset-dependent."""

        def _order_by_clause_for(offset: int) -> str:
            session = MagicMock()
            count_result = MagicMock()
            count_result.scalar_one.return_value = 2
            data_result = MagicMock()
            data_result.all.return_value = []
            session.execute.side_effect = [count_result, data_result]

            with patch("codemie.service.activity.activity_repository.config") as mock_config:
                mock_config.ACTIVITY_EVENTS_ENABLED = True
                _repo().find_by_project_instance_id(PROJECT_ID, limit=1, offset=offset, session=session)

            data_stmt = session.execute.call_args_list[1].args[0]
            return str(data_stmt).split("ORDER BY")[1].split("LIMIT")[0].strip()

        first_page_order = _order_by_clause_for(offset=0)
        second_page_order = _order_by_clause_for(offset=1)

        assert first_page_order == second_page_order
        assert first_page_order == "activity_events.created_at DESC, activity_events.id DESC"


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
