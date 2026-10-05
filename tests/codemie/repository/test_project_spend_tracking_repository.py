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
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import sqlite as sqlite_dialect

from codemie.repository.project_spend_tracking_repository import ProjectSpendTrackingRepository
from codemie.service.spend_tracking.spend_models import ProjectSpendTracking


@pytest.mark.asyncio
async def test_get_latest_key_spending_for_project_returns_first_matching_row():
    repository = ProjectSpendTrackingRepository()
    row = SimpleNamespace(project_name="proj-a", cumulative_spend=12.5)
    execute_result = MagicMock()
    execute_result.scalars.return_value.first.return_value = row
    session = AsyncMock()
    session.execute.return_value = execute_result

    result = await repository.get_latest_key_spending_for_project(session=session, project_name="proj-a")

    assert result is row
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_latest_key_spending_for_project_returns_none_when_no_rows():
    repository = ProjectSpendTrackingRepository()
    execute_result = MagicMock()
    execute_result.scalars.return_value.first.return_value = None
    session = AsyncMock()
    session.execute.return_value = execute_result

    result = await repository.get_latest_key_spending_for_project(session=session, project_name="proj-a")

    assert result is None


@pytest.mark.asyncio
async def test_get_latest_budget_rows_for_project_returns_all_budget_rows():
    repository = ProjectSpendTrackingRepository()
    rows = [SimpleNamespace(budget_id="budget-1"), SimpleNamespace(budget_id="budget-2")]
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = rows
    session = AsyncMock()
    session.execute.return_value = execute_result

    result = await repository.get_latest_budget_rows_for_project(
        session=session,
        project_name="proj-a",
        rows_limit=10,
    )

    assert result == rows
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_latest_spending_by_project_returns_empty_for_empty_input():
    repository = ProjectSpendTrackingRepository()
    session = AsyncMock()

    result = await repository.get_latest_spending_by_project(session=session, project_names=[])

    assert result == []
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_get_latest_spending_by_project_returns_rows_for_requested_projects():
    repository = ProjectSpendTrackingRepository()
    rows = [SimpleNamespace(project_name="proj-a"), SimpleNamespace(project_name="proj-b")]
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = rows
    session = AsyncMock()
    session.execute.return_value = execute_result

    result = await repository.get_latest_spending_by_project(
        session=session,
        project_names=["proj-a", "proj-b"],
        spend_subject_type="budget",
    )

    assert result == rows
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_latest_before_by_project_budget_ids_returns_empty_for_empty_input():
    repository = ProjectSpendTrackingRepository()
    session = AsyncMock()

    result = await repository.get_latest_before_by_project_budget_ids(session, [], datetime.now())

    assert result == {}
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_get_latest_before_by_project_budget_ids_groups_rows_by_pair():
    repository = ProjectSpendTrackingRepository()
    session = AsyncMock()
    row = SimpleNamespace(project_name="proj-a", budget_id="budget-1")
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [row]
    session.execute.return_value = execute_result

    result = await repository.get_latest_before_by_project_budget_ids(
        session,
        [("proj-a", "budget-1")],
        datetime.now(),
        spend_subject_type="project_budget",
    )

    assert result == {("proj-a", "budget-1"): row}


@pytest.mark.asyncio
async def test_get_latest_before_by_budget_category_ids_returns_empty_for_empty_input():
    repository = ProjectSpendTrackingRepository()
    session = AsyncMock()

    result = await repository.get_latest_before_by_budget_category_ids(session, [], datetime.now())

    assert result == {}
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_get_latest_before_by_budget_category_ids_groups_rows_by_pair():
    repository = ProjectSpendTrackingRepository()
    session = AsyncMock()
    row = SimpleNamespace(project_name="proj-a", budget_id="budget-1", budget_category="cli")
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [row]
    session.execute.return_value = execute_result

    result = await repository.get_latest_before_by_budget_category_ids(
        session,
        [("proj-a", "cli")],
        datetime.now(),
    )

    assert result == {("proj-a", "cli"): row}


@pytest.mark.asyncio
async def test_get_latest_before_by_member_budget_ids_returns_empty_for_empty_input():
    repository = ProjectSpendTrackingRepository()
    session = AsyncMock()

    result = await repository.get_latest_before_by_member_budget_ids(session, [], datetime.now())

    assert result == {}
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_get_latest_before_by_member_budget_ids_groups_rows_by_member_triple():
    repository = ProjectSpendTrackingRepository()
    session = AsyncMock()
    row = SimpleNamespace(project_name="proj-a", budget_id="budget-1", user_id="user-1")
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [row]
    session.execute.return_value = execute_result

    result = await repository.get_latest_before_by_member_budget_ids(
        session,
        [("proj-a", "budget-1", "user-1")],
        datetime.now(),
    )

    assert result == {("proj-a", "budget-1", "user-1"): row}


@pytest.mark.asyncio
async def test_insert_project_budget_entries_returns_early_for_empty_rows():
    repository = ProjectSpendTrackingRepository()
    session = AsyncMock()

    await repository.insert_project_budget_entries(session, [])

    session.execute.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_insert_project_budget_entries_executes_upsert_and_commit():
    repository = ProjectSpendTrackingRepository()
    session = AsyncMock()
    row = SimpleNamespace(
        id="row-1",
        project_name="proj-a",
        cost_center_id=None,
        cost_center_name=None,
        spend_date=datetime.now(),
        daily_spend=1.0,
        cumulative_spend=2.0,
        budget_period_spend=3.0,
        budget_id="budget-1",
        budget_category="cli",
        user_id=None,
        provider_subject_id="provider-1",
    )

    await repository.insert_project_budget_entries(session, [row])

    session.execute.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_insert_member_budget_entries_executes_upsert_and_commit():
    repository = ProjectSpendTrackingRepository()
    session = AsyncMock()
    row = SimpleNamespace(
        id="row-1",
        project_name="proj-a",
        cost_center_id=None,
        cost_center_name=None,
        spend_date=datetime.now(),
        daily_spend=1.0,
        cumulative_spend=2.0,
        budget_period_spend=3.0,
        budget_id="budget-1",
        budget_category="cli",
        user_id="user-1",
        provider_subject_id="provider-1",
    )

    await repository.insert_member_budget_entries(session, [row])

    session.execute.assert_awaited_once()
    session.commit.assert_awaited_once()


class TestGetSpendForPeriod:
    @pytest.mark.asyncio
    async def test_returns_total_spend_row_count_and_rows(self):
        repository = ProjectSpendTrackingRepository()
        row = SimpleNamespace(
            project_name="proj-a",
            spend_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            daily_spend=Decimal("5.5"),
            cumulative_spend=Decimal("10.0"),
            budget_period_spend=Decimal("5.5"),
            budget_id="budget-1",
        )
        agg_result = MagicMock()
        agg_result.one.return_value = SimpleNamespace(total_spend=Decimal("5.5"), total_count=1)
        data_result = MagicMock()
        data_result.scalars.return_value.all.return_value = [row]
        session = AsyncMock()
        session.execute.side_effect = [agg_result, data_result]

        total_spend, total_count, rows = await repository.get_spend_for_period(
            session=session,
            project_name="proj-a",
            period_from_dt=datetime(2026, 1, 1, tzinfo=timezone.utc),
            period_to_dt=datetime(2026, 2, 1, tzinfo=timezone.utc),
            page=0,
            per_page=20,
        )

        assert total_spend == 5.5
        assert isinstance(total_spend, float)
        assert total_count == 1
        assert rows == [row]
        assert session.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_returns_zero_total_when_no_rows(self):
        repository = ProjectSpendTrackingRepository()
        agg_result = MagicMock()
        agg_result.one.return_value = SimpleNamespace(total_spend=Decimal("0"), total_count=0)
        session = AsyncMock()
        session.execute.side_effect = [agg_result]

        total_spend, total_count, rows = await repository.get_spend_for_period(
            session=session,
            project_name="proj-empty",
            period_from_dt=datetime(2026, 1, 1, tzinfo=timezone.utc),
            period_to_dt=datetime(2026, 2, 1, tzinfo=timezone.utc),
            page=0,
            per_page=20,
        )

        assert total_spend == 0.0
        assert total_count == 0
        assert rows == []

    @pytest.mark.asyncio
    async def test_issues_two_queries_for_any_page(self):
        """Two execute calls are always issued: one aggregation, one paginated data fetch."""
        repository = ProjectSpendTrackingRepository()
        agg_result = MagicMock()
        agg_result.one.return_value = SimpleNamespace(total_spend=Decimal("100.0"), total_count=25)
        data_result = MagicMock()
        data_result.scalars.return_value.all.return_value = []
        session = AsyncMock()
        session.execute.side_effect = [agg_result, data_result]

        total_spend, total_count, rows = await repository.get_spend_for_period(
            session=session,
            project_name="proj-a",
            period_from_dt=datetime(2026, 1, 1, tzinfo=timezone.utc),
            period_to_dt=datetime(2026, 2, 1, tzinfo=timezone.utc),
            page=1,
            per_page=20,
        )

        assert total_spend == 100.0
        assert total_count == 25
        assert session.execute.await_count == 2


def _compile_sql(stmt) -> str:
    """Return literal-bound SQL string for assertion."""
    return str(stmt.compile(dialect=sqlite_dialect.dialect(), compile_kwargs={"literal_binds": True}))


def _make_repo_mocks(total_spend=Decimal("0"), total_count=0, rows=None):
    """Return (repository, session) with pre-wired execute responses."""
    repo = ProjectSpendTrackingRepository()
    agg_result = MagicMock()
    agg_result.one.return_value = SimpleNamespace(total_spend=total_spend, total_count=total_count)
    data_result = MagicMock()
    data_result.scalars.return_value.all.return_value = rows or []
    session = AsyncMock()
    session.execute.side_effect = [agg_result, data_result]
    return repo, session


_FROM = datetime(2026, 1, 1, tzinfo=timezone.utc)
_TO = datetime(2026, 2, 1, tzinfo=timezone.utc)


class TestGetSpendForPeriodFilters:
    @pytest.mark.asyncio
    async def test_get_spend_for_period_no_filter_excludes_project_budget(self):
        repo, session = _make_repo_mocks(total_count=1)

        await repo.get_spend_for_period(
            session=session,
            project_name="proj-a",
            period_from_dt=_FROM,
            period_to_dt=_TO,
            page=0,
            per_page=20,
        )

        assert session.execute.await_count == 2
        agg_sql = _compile_sql(session.execute.await_args_list[0].args[0])
        data_sql = _compile_sql(session.execute.await_args_list[1].args[0])
        assert "NOT IN" in agg_sql.upper()
        assert "project_budget" in agg_sql
        assert "NOT IN" in data_sql.upper()
        assert "project_budget" in data_sql

    @pytest.mark.asyncio
    async def test_get_spend_for_period_spend_subject_type_filter_applies_exact_match(self):
        repo, session = _make_repo_mocks(total_count=1)

        await repo.get_spend_for_period(
            session=session,
            project_name="proj-a",
            period_from_dt=_FROM,
            period_to_dt=_TO,
            page=0,
            per_page=20,
            spend_subject_type="member_budget",
        )

        agg_sql = _compile_sql(session.execute.await_args_list[0].args[0])
        data_sql = _compile_sql(session.execute.await_args_list[1].args[0])
        assert "member_budget" in agg_sql
        assert "NOT IN" not in agg_sql.upper()
        assert "member_budget" in data_sql
        assert "NOT IN" not in data_sql.upper()

    @pytest.mark.asyncio
    async def test_get_spend_for_period_budget_category_filter_applies_to_both_statements(self):
        repo, session = _make_repo_mocks(total_count=1)

        await repo.get_spend_for_period(
            session=session,
            project_name="proj-a",
            period_from_dt=_FROM,
            period_to_dt=_TO,
            page=0,
            per_page=20,
            budget_category="cli",
        )

        agg_sql = _compile_sql(session.execute.await_args_list[0].args[0])
        data_sql = _compile_sql(session.execute.await_args_list[1].args[0])
        assert "budget_category = 'cli'" in agg_sql
        assert "budget_category = 'cli'" in data_sql

    @pytest.mark.asyncio
    async def test_get_spend_for_period_both_filters_applied_together(self):
        repo, session = _make_repo_mocks(total_count=1)

        await repo.get_spend_for_period(
            session=session,
            project_name="proj-a",
            period_from_dt=_FROM,
            period_to_dt=_TO,
            page=0,
            per_page=20,
            budget_category="platform",
            spend_subject_type="budget",
        )

        agg_sql = _compile_sql(session.execute.await_args_list[0].args[0])
        data_sql = _compile_sql(session.execute.await_args_list[1].args[0])
        assert "budget_category = 'platform'" in agg_sql
        assert "spend_subject_type = 'budget'" in agg_sql
        assert "NOT IN" not in agg_sql.upper()
        assert "budget_category = 'platform'" in data_sql
        assert "spend_subject_type = 'budget'" in data_sql


@pytest.mark.asyncio
async def test_get_latest_before_by_project_budget_ids_filters_spend_subject_type_in_outer_query():
    """Outer SELECT must carry spend_subject_type filter so member_budget rows cannot collide."""
    repository = ProjectSpendTrackingRepository()
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = []
    session = AsyncMock()
    session.execute.return_value = execute_result

    await repository.get_latest_before_by_project_budget_ids(
        session,
        [("proj-a", "budget-1")],
        datetime(2026, 6, 29, tzinfo=timezone.utc),
        spend_subject_type="project_budget",
    )

    stmt = session.execute.call_args[0][0]
    compiled = stmt.compile(dialect=sqlite_dialect.dialect(), compile_kwargs={"literal_binds": True})
    sql = str(compiled)
    # "project_budget" must appear at least twice: once in the subquery WHERE
    # and once in the outer WHERE added by the fix.
    assert (
        sql.count("project_budget") >= 2
    ), f"Expected spend_subject_type filter in both subquery and outer query, got SQL:\n{sql}"


class TestGetLatestMemberRowsForUser:
    """Tests for get_latest_member_rows_for_user."""

    @pytest.mark.asyncio
    async def test_returns_empty_dict_when_no_rows(self):
        repo = ProjectSpendTrackingRepository()
        session = MagicMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=result_mock)

        result = await repo.get_latest_member_rows_for_user(session, "u-1")

        assert result == {}

    @pytest.mark.asyncio
    async def test_keys_rows_by_project_and_category(self):
        repo = ProjectSpendTrackingRepository()
        row = ProjectSpendTracking(
            id=uuid4(),
            project_name="atlas-core",
            spend_date=datetime(2026, 8, 13, tzinfo=timezone.utc),
            daily_spend=Decimal("1.0"),
            cumulative_spend=Decimal("5.0"),
            budget_period_spend=Decimal("4.0"),
            budget_id="b-1",
            budget_category="cli",
            user_id="u-1",
            spend_subject_type="member_budget",
        )
        session = MagicMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [row]
        session.execute = AsyncMock(return_value=result_mock)

        result = await repo.get_latest_member_rows_for_user(session, "u-1")

        assert result == {("atlas-core", "cli"): row}


class TestGetLatestMemberRowsForProject:
    """Tests for get_latest_member_rows_for_project."""

    @pytest.mark.asyncio
    async def test_keys_rows_by_user_and_category(self):
        repo = ProjectSpendTrackingRepository()
        row = ProjectSpendTracking(
            id=uuid4(),
            project_name="atlas-core",
            spend_date=datetime(2026, 8, 13, tzinfo=timezone.utc),
            daily_spend=Decimal("1.0"),
            cumulative_spend=Decimal("5.0"),
            budget_period_spend=Decimal("4.0"),
            budget_id="b-1",
            budget_category="platform",
            user_id="u-7",
            spend_subject_type="member_budget",
        )
        session = MagicMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [row]
        session.execute = AsyncMock(return_value=result_mock)

        result = await repo.get_latest_member_rows_for_project(session, "atlas-core")

        assert result == {("u-7", "platform"): row}


class TestGetNewestMemberSpendDate:
    """Tests for get_newest_member_spend_date."""

    @pytest.mark.asyncio
    async def test_returns_none_when_table_empty(self):
        repo = ProjectSpendTrackingRepository()
        session = MagicMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)

        assert await repo.get_newest_member_spend_date(session) is None

    @pytest.mark.asyncio
    async def test_returns_max_spend_date(self):
        expected = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
        repo = ProjectSpendTrackingRepository()
        session = MagicMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = expected
        session.execute = AsyncMock(return_value=result_mock)

        assert await repo.get_newest_member_spend_date(session) == expected

    @pytest.mark.asyncio
    async def test_scopes_to_member_budget_and_is_not_slice_scoped(self):
        """Freshness marker is table-wide over member_budget rows only.

        Per-slice scoping would read as permanently stale for members with no
        recent spend, since the collector never writes zero-delta rows.
        """
        repo = ProjectSpendTrackingRepository()
        session = MagicMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)

        await repo.get_newest_member_spend_date(session)

        sql = _compile_sql(session.execute.call_args[0][0])
        assert "member_budget" in sql
        assert "max(project_spend_tracking.spend_date)" in sql.lower()
        # No slice scoping: a user_id or project_name predicate would defeat the marker.
        assert "user_id" not in sql
        assert "project_name" not in sql


@pytest.mark.asyncio
async def test_get_latest_member_rows_for_user_filters_subject_type_in_both_queries():
    """spend_subject_type must appear in subquery AND outer WHERE, or the join can match other types."""
    repo = ProjectSpendTrackingRepository()
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = []
    session = AsyncMock()
    session.execute.return_value = result_mock

    await repo.get_latest_member_rows_for_user(session, "u-1")

    sql = _compile_sql(session.execute.call_args[0][0])
    assert (
        sql.count("member_budget") >= 2
    ), f"Expected spend_subject_type filter in both subquery and outer query, got SQL:\n{sql}"
    assert sql.count("'u-1'") >= 2, f"Expected user scoping in both subquery and outer query, got SQL:\n{sql}"
    assert (
        "max(project_spend_tracking.created_at)" in sql.lower()
    ), f"Expected MAX(created_at) tiebreaker so concurrent refreshes resolve deterministically, got SQL:\n{sql}"


@pytest.mark.asyncio
async def test_get_latest_member_rows_for_project_filters_subject_type_in_both_queries():
    """spend_subject_type must appear in subquery AND outer WHERE, or the join can match other types."""
    repo = ProjectSpendTrackingRepository()
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = []
    session = AsyncMock()
    session.execute.return_value = result_mock

    await repo.get_latest_member_rows_for_project(session, "atlas-core")

    sql = _compile_sql(session.execute.call_args[0][0])
    assert (
        sql.count("member_budget") >= 2
    ), f"Expected spend_subject_type filter in both subquery and outer query, got SQL:\n{sql}"
    assert sql.count("'atlas-core'") >= 2, f"Expected project scoping in both subquery and outer query, got SQL:\n{sql}"
    assert (
        "max(project_spend_tracking.created_at)" in sql.lower()
    ), f"Expected MAX(created_at) tiebreaker so concurrent refreshes resolve deterministically, got SQL:\n{sql}"


class TestGetProjectResetCutoffs:
    """The last pre-reset project_budget observation per category."""

    @staticmethod
    def _row(category: str, period_spend: str, spend_date: datetime):
        return SimpleNamespace(
            budget_category=category,
            budget_period_spend=Decimal(period_spend),
            spend_date=spend_date,
        )

    @staticmethod
    def _session(rows):
        result = MagicMock()
        result.all.return_value = rows
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result)
        return session

    @pytest.mark.asyncio
    async def test_cutoff_is_the_row_before_the_last_drop(self):
        repo = ProjectSpendTrackingRepository()
        rows = [
            self._row("platform", "3115.26", datetime(2026, 7, 29, 23, tzinfo=timezone.utc)),
            self._row("platform", "3221.46", datetime(2026, 7, 31, 23, tzinfo=timezone.utc)),
            self._row("platform", "38.13", datetime(2026, 8, 2, 23, tzinfo=timezone.utc)),
            self._row("platform", "161.86", datetime(2026, 8, 3, 23, tzinfo=timezone.utc)),
        ]
        got = await repo.get_project_reset_cutoffs(self._session(rows), "epm-aisa")
        assert got == {"platform": datetime(2026, 7, 31, 23, tzinfo=timezone.utc)}

    @pytest.mark.asyncio
    async def test_uses_the_most_recent_drop_when_there_are_several(self):
        repo = ProjectSpendTrackingRepository()
        rows = [
            self._row("platform", "3419.71", datetime(2026, 6, 30, 23, tzinfo=timezone.utc)),
            self._row("platform", "135.57", datetime(2026, 7, 1, 23, tzinfo=timezone.utc)),
            self._row("platform", "3221.46", datetime(2026, 7, 31, 23, tzinfo=timezone.utc)),
            self._row("platform", "38.13", datetime(2026, 8, 2, 23, tzinfo=timezone.utc)),
        ]
        got = await repo.get_project_reset_cutoffs(self._session(rows), "epm-aisa")
        assert got == {"platform": datetime(2026, 7, 31, 23, tzinfo=timezone.utc)}

    @pytest.mark.asyncio
    async def test_categories_are_independent(self):
        repo = ProjectSpendTrackingRepository()
        rows = [
            self._row("platform", "3221.46", datetime(2026, 7, 31, 23, tzinfo=timezone.utc)),
            self._row("platform", "38.13", datetime(2026, 8, 2, 23, tzinfo=timezone.utc)),
            self._row("cli", "10.00", datetime(2026, 7, 31, 23, tzinfo=timezone.utc)),
            self._row("cli", "12.00", datetime(2026, 8, 2, 23, tzinfo=timezone.utc)),
        ]
        got = await repo.get_project_reset_cutoffs(self._session(rows), "epm-aisa")
        assert got == {"platform": datetime(2026, 7, 31, 23, tzinfo=timezone.utc)}

    @pytest.mark.asyncio
    async def test_no_drop_means_no_cutoff(self):
        repo = ProjectSpendTrackingRepository()
        rows = [
            self._row("platform", "100.00", datetime(2026, 8, 1, 23, tzinfo=timezone.utc)),
            self._row("platform", "150.00", datetime(2026, 8, 2, 23, tzinfo=timezone.utc)),
        ]
        got = await repo.get_project_reset_cutoffs(self._session(rows), "epm-aisa")
        assert got == {}

    @pytest.mark.asyncio
    async def test_no_rows_means_no_cutoff(self):
        repo = ProjectSpendTrackingRepository()
        got = await repo.get_project_reset_cutoffs(self._session([]), "epm-aisa")
        assert got == {}

    @pytest.mark.asyncio
    async def test_query_filters_to_project_budget_rows_of_the_project(self):
        repo = ProjectSpendTrackingRepository()
        session = self._session([])
        await repo.get_project_reset_cutoffs(session, "epm-aisa")
        sql = _compile_sql(session.execute.await_args.args[0])
        assert "project_budget" in sql
        assert "epm-aisa" in sql
        assert "ORDER BY" in sql.upper()


class TestLatestProjectBudgetRowsByCategory:
    """Latest project_budget row per project and category, whatever its budget_id."""

    @pytest.mark.asyncio
    async def test_returns_empty_for_no_projects(self):
        session = AsyncMock()

        result = await ProjectSpendTrackingRepository().get_latest_project_budget_rows_by_category(session, [])

        assert result == []
        session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_partitions_by_category_not_budget_id(self):
        rows = [SimpleNamespace(project_name="project-3", budget_category="premium_models")]
        execute_result = MagicMock()
        execute_result.scalars.return_value.all.return_value = rows
        session = AsyncMock()
        session.execute = AsyncMock(return_value=execute_result)

        result = await ProjectSpendTrackingRepository().get_latest_project_budget_rows_by_category(
            session, ["project-3"]
        )

        assert result == rows
        sql = _compile_sql(session.execute.await_args.args[0])
        assert (
            "PARTITION BY project_spend_tracking.project_name, project_spend_tracking.budget_category ORDER BY" in sql
        )
        assert "'project_budget'" in sql
        assert "'project-3'" in sql
        assert (
            "ORDER BY project_spend_tracking.spend_date DESC, project_spend_tracking.created_at DESC) AS row_rank"
            in sql
        )
        assert sql.rstrip().endswith("WHERE anon_1.row_rank = 1")
