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

"""Unit tests for member_spend_service helpers and MemberSpendService."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from codemie.service.analytics.handlers.member_spend_service import (
    MemberSpendService,
    _build_row,
    _category_columns,
    _limit_value,
    _spend_value,
)
from codemie.service.spend_tracking.spend_models import ProjectSpendTracking


def _member_row(project_name: str, category: str, user_id: str, spend: str, spend_date: datetime):
    return ProjectSpendTracking(
        id=uuid4(),
        project_name=project_name,
        spend_date=spend_date,
        daily_spend=Decimal("0"),
        cumulative_spend=Decimal(spend),
        budget_period_spend=Decimal(spend),
        budget_id="b-1",
        budget_category=category,
        user_id=user_id,
        spend_subject_type="member_budget",
    )


class TestCategoryColumns:
    """Tests for _category_columns."""

    def test_first_column_is_the_key_column(self):
        columns = _category_columns("project_name", "Project")
        assert columns[0] == {
            "id": "project_name",
            "label": "Project",
            "type": "string",
            "format": None,
        }

    def test_emits_one_currency_column_per_budget_category(self):
        columns = _category_columns("user_id", "User")
        ids = [c["id"] for c in columns[1:]]
        assert ids == ["platform", "cli", "premium_models"]
        assert all(c["type"] == "number" for c in columns[1:])
        assert all(c["format"] == "currency" for c in columns[1:])

    def test_no_total_column(self):
        assert "total" not in [c["id"] for c in _category_columns("user_id", "User")]


class TestSpendValue:
    """Tests for _spend_value."""

    def test_missing_row_is_zero_not_none(self):
        assert _spend_value(None) == 0

    def test_reads_budget_period_spend_as_float(self):
        row = _member_row("p", "cli", "u", "12.50", datetime.now(timezone.utc))
        assert _spend_value(row) == 12.5


class TestLimitValue:
    """Tests for _limit_value."""

    def test_missing_allocation_is_none_not_zero(self):
        assert _limit_value(None) is None

    def test_reads_allocated_max_budget(self):
        assert _limit_value(SimpleNamespace(allocated_max_budget=500.0)) == 500.0

    def test_zero_limit_is_preserved_as_zero(self):
        assert _limit_value(SimpleNamespace(allocated_max_budget=0.0)) == 0.0


class TestBuildRow:
    """Tests for _build_row."""

    def test_includes_key_values_verbatim(self):
        row = _build_row({"project_name": "atlas-core"}, {}, {})
        assert row["project_name"] == "atlas-core"

    def test_absent_categories_render_as_zero_spend_and_null_limit(self):
        row = _build_row({"user_id": "u-1"}, {}, {})
        assert row["platform"] == 0
        assert row["cli"] == 0
        assert row["premium_models"] == 0
        assert row["platform_limit"] is None
        assert row["cli_limit"] is None
        assert row["premium_models_limit"] is None

    def test_populates_spend_and_limit_per_category(self):
        spend = {"cli": _member_row("atlas-core", "cli", "u-1", "40.00", datetime.now(timezone.utc))}
        allocations = {"cli": SimpleNamespace(allocated_max_budget=100.0)}
        row = _build_row({"user_id": "u-1"}, spend, allocations)
        assert row["cli"] == 40.0
        assert row["cli_limit"] == 100.0
        assert row["platform"] == 0
        assert row["platform_limit"] is None

    def test_no_total_key(self):
        assert "total" not in _build_row({"user_id": "u-1"}, {}, {})


@pytest.fixture
def ten_minute_ttl():
    """Pin the staleness threshold to its 10-minute default.

    Local .env files often lower this to ~1s for UI iteration; these tests assert
    against the shipped default, not whatever the developer's environment holds.
    """
    from codemie.configs.config import config

    with patch.object(config, "BUDGET_MEMBER_SPEND_STALENESS_THRESHOLD_MS", 600000):
        yield


@pytest.mark.usefixtures("ten_minute_ttl")
class TestNeedsRefresh:
    """Tests for MemberSpendService._needs_refresh."""

    def test_empty_table_needs_refresh(self):
        assert MemberSpendService()._needs_refresh(None) is True

    def test_recent_snapshot_does_not_need_refresh(self):
        recent = datetime.now(timezone.utc) - timedelta(minutes=2)
        assert MemberSpendService()._needs_refresh(recent) is False

    def test_old_snapshot_needs_refresh(self):
        old = datetime.now(timezone.utc) - timedelta(hours=3)
        assert MemberSpendService()._needs_refresh(old) is True

    def test_naive_datetime_is_treated_as_utc(self):
        naive_recent = (datetime.now(timezone.utc) - timedelta(minutes=2)).replace(tzinfo=None)
        assert MemberSpendService()._needs_refresh(naive_recent) is False

    def test_quiet_member_does_not_force_refresh_when_table_is_fresh(self):
        """Table-wide freshness, not per-slice: sparse rows must not defeat the TTL."""
        table_fresh = datetime.now(timezone.utc) - timedelta(minutes=1)
        assert MemberSpendService()._needs_refresh(table_fresh) is False


@pytest.mark.usefixtures("ten_minute_ttl")
class TestEnsureFresh:
    """Tests for MemberSpendService._ensure_fresh."""

    @pytest.mark.asyncio
    async def test_skips_refresh_when_data_is_fresh(self):
        service = MemberSpendService()
        session = MagicMock()
        fresh = datetime.now(timezone.utc) - timedelta(minutes=1)

        with (
            patch.object(service, "_refresh_all_member_spend", new=AsyncMock()) as refresh,
            patch(
                "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository."
                "get_newest_member_spend_date",
                new=AsyncMock(return_value=fresh),
            ),
        ):
            await service._ensure_fresh(session)

        refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_refresh_when_litellm_disabled(self):
        service = MemberSpendService()
        session = MagicMock()

        with (
            patch.object(service, "_refresh_all_member_spend", new=AsyncMock()) as refresh,
            patch.object(MemberSpendService, "_is_litellm_enabled", return_value=False),
            patch(
                "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository."
                "get_newest_member_spend_date",
                new=AsyncMock(return_value=None),
            ),
        ):
            await service._ensure_fresh(session)

        refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_refreshes_when_stale_and_litellm_enabled(self):
        service = MemberSpendService()
        session = MagicMock()
        stale = datetime.now(timezone.utc) - timedelta(hours=3)

        with (
            patch.object(service, "_refresh_all_member_spend", new=AsyncMock()) as refresh,
            patch.object(MemberSpendService, "_is_litellm_enabled", return_value=True),
            patch(
                "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository."
                "get_newest_member_spend_date",
                new=AsyncMock(return_value=stale),
            ),
        ):
            await service._ensure_fresh(session)

        refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_provider_failure_does_not_raise(self):
        """LiteLLM being unavailable must degrade to stale data, never fail the request."""
        service = MemberSpendService()
        session = MagicMock()
        session.rollback = AsyncMock()
        stale = datetime.now(timezone.utc) - timedelta(hours=3)

        with (
            patch.object(
                service,
                "_refresh_all_member_spend",
                new=AsyncMock(side_effect=RuntimeError("litellm down")),
            ),
            patch.object(MemberSpendService, "_is_litellm_enabled", return_value=True),
            patch(
                "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository."
                "get_newest_member_spend_date",
                new=AsyncMock(return_value=stale),
            ),
        ):
            await service._ensure_fresh(session)  # must not raise


class TestGetUserProjectSpend:
    """Tests for MemberSpendService.get_user_project_spend."""

    @pytest.mark.asyncio
    async def test_returns_empty_rows_when_user_has_no_projects(self):
        service = MemberSpendService()
        session = MagicMock()

        with (
            patch.object(service, "_ensure_fresh", new=AsyncMock()),
            patch(
                "codemie.repository.user_project_repository.user_project_repository.aget_by_user_id",
                new=AsyncMock(return_value=[]),
            ),
        ):
            columns, rows = await service.get_user_project_spend(session, "u-1")

        assert rows == []
        assert columns[0]["id"] == "project_name"

    @pytest.mark.asyncio
    async def test_project_with_no_spend_still_returns_a_row_of_zeros(self):
        """Membership drives rows; sparse spend must never remove a project."""
        service = MemberSpendService()
        session = MagicMock()
        memberships = [SimpleNamespace(project_name="atlas-core", user_id="u-1")]

        with (
            patch.object(service, "_ensure_fresh", new=AsyncMock()),
            patch(
                "codemie.repository.user_project_repository.user_project_repository.aget_by_user_id",
                new=AsyncMock(return_value=memberships),
            ),
            patch(
                "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository."
                "get_latest_member_rows_for_user",
                new=AsyncMock(return_value={}),
            ),
            patch(
                "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository."
                "get_project_reset_cutoffs",
                new=AsyncMock(return_value={}),
            ),
            patch(
                "codemie.repository.project_budget_repository.project_member_budget_assignment_repository."
                "get_active_by_user",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "codemie.repository.application_repository.application_repository.aget_all_non_deleted",
                new=AsyncMock(return_value=[]),
            ),
        ):
            _, rows = await service.get_user_project_spend(session, "u-1")

        assert len(rows) == 1
        assert rows[0]["project_name"] == "atlas-core"
        assert rows[0]["platform"] == 0
        assert rows[0]["cli"] == 0
        assert rows[0]["platform_limit"] is None
        # Every row key must be described by columns[], which is authoritative.
        assert "display_name" not in rows[0]

    @pytest.mark.asyncio
    async def test_joins_spend_and_limits_onto_membership_rows(self):
        service = MemberSpendService()
        session = MagicMock()
        memberships = [SimpleNamespace(project_name="atlas-core", user_id="u-1")]
        spend = {("atlas-core", "cli"): _member_row("atlas-core", "cli", "u-1", "40.00", datetime.now(timezone.utc))}
        allocations = [
            SimpleNamespace(project_name="atlas-core", budget_category="cli", user_id="u-1", allocated_max_budget=100.0)
        ]

        with (
            patch.object(service, "_ensure_fresh", new=AsyncMock()),
            patch(
                "codemie.repository.user_project_repository.user_project_repository.aget_by_user_id",
                new=AsyncMock(return_value=memberships),
            ),
            patch(
                "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository."
                "get_latest_member_rows_for_user",
                new=AsyncMock(return_value=spend),
            ),
            patch(
                "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository."
                "get_project_reset_cutoffs",
                new=AsyncMock(return_value={}),
            ),
            patch(
                "codemie.repository.project_budget_repository.project_member_budget_assignment_repository."
                "get_active_by_user",
                new=AsyncMock(return_value=allocations),
            ),
            patch(
                "codemie.repository.application_repository.application_repository.aget_all_non_deleted",
                new=AsyncMock(return_value=[]),
            ),
        ):
            _, rows = await service.get_user_project_spend(session, "u-1")

        assert rows[0]["cli"] == 40.0
        assert rows[0]["cli_limit"] == 100.0
        assert rows[0]["platform"] == 0
        assert rows[0]["platform_limit"] is None


class TestGetProjectMemberSpend:
    """Tests for MemberSpendService.get_project_member_spend."""

    @pytest.mark.asyncio
    async def test_returns_empty_rows_for_project_with_no_members(self):
        service = MemberSpendService()
        session = MagicMock()

        with (
            patch.object(service, "_ensure_fresh", new=AsyncMock()),
            patch(
                "codemie.repository.user_project_repository.user_project_repository.aget_by_project_name",
                new=AsyncMock(return_value=[]),
            ),
        ):
            columns, rows = await service.get_project_member_spend(session, "atlas-core")

        assert rows == []
        assert columns[0]["id"] == "user_id"

    @pytest.mark.asyncio
    async def test_member_with_no_spend_still_returns_a_row(self):
        service = MemberSpendService()
        session = MagicMock()
        members = [SimpleNamespace(user_id="u-7", project_name="atlas-core")]

        with (
            patch.object(service, "_ensure_fresh", new=AsyncMock()),
            patch(
                "codemie.repository.user_project_repository.user_project_repository.aget_by_project_name",
                new=AsyncMock(return_value=members),
            ),
            patch(
                "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository."
                "get_latest_member_rows_for_project",
                new=AsyncMock(return_value={}),
            ),
            patch(
                "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository."
                "get_project_reset_cutoffs",
                new=AsyncMock(return_value={}),
            ),
            patch(
                "codemie.repository.project_budget_repository.project_member_budget_assignment_repository."
                "get_active_by_project",
                new=AsyncMock(return_value=[]),
            ),
        ):
            _, rows = await service.get_project_member_spend(session, "atlas-core")

        assert len(rows) == 1
        assert rows[0]["user_id"] == "u-7"
        assert rows[0]["premium_models"] == 0
        assert rows[0]["premium_models_limit"] is None


def _snapshot(project_name="atlas-core", user_id="u-1", budget_id="b-1", spend="100", cumulative=None):
    from codemie.service.budget.budget_enums import BudgetCategory

    return SimpleNamespace(
        project_name=project_name,
        budget_category=BudgetCategory.CLI,
        budget_id=budget_id,
        user_id=user_id,
        spend=Decimal(spend),
        cumulative_spend=Decimal(cumulative) if cumulative is not None else None,
        provider_subject_id=f"codemie:project:{project_name}:category:cli:user:{user_id}",
    )


def _prev_row(period="100", cumulative="900", spend_date=None):
    return ProjectSpendTracking(
        id=uuid4(),
        project_name="atlas-core",
        spend_date=spend_date or (datetime.now(timezone.utc) - timedelta(days=1)),
        daily_spend=Decimal("10"),
        cumulative_spend=Decimal(cumulative),
        budget_period_spend=Decimal(period),
        budget_id="b-1",
        budget_category="cli",
        user_id="u-1",
        spend_subject_type="member_budget",
    )


def _patch_refresh_deps(snapshots, prev_rows=None, budgets_map=None, insert_mock=None):
    """Patch the collaborators of _refresh_all_member_spend. Returns a contextlib.ExitStack."""
    import contextlib

    provider = MagicMock()
    provider.collect_member_budget_spend = AsyncMock(return_value=snapshots)
    stack = contextlib.ExitStack()
    stack.enter_context(
        patch(
            "codemie.service.analytics.handlers.member_spend_service.get_active_provider",
            return_value=provider,
        )
    )
    stack.enter_context(
        patch(
            "codemie.service.analytics.handlers.member_spend_service.budget_repository.get_all_keyed_by_id",
            new=AsyncMock(return_value=budgets_map or {}),
        )
    )
    stack.enter_context(
        patch(
            "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository."
            "get_latest_before_by_member_budget_ids",
            new=AsyncMock(return_value=prev_rows or {}),
        )
    )
    stack.enter_context(
        patch(
            "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository."
            "insert_member_budget_entries",
            new=insert_mock or AsyncMock(),
        )
    )
    return stack


class TestRefreshAllMemberSpend:
    """Tests for the refresh body: reset detection, error isolation, zero-delta skipping."""

    @pytest.mark.asyncio
    async def test_passes_budget_to_delta_so_reset_is_detected(self):
        """A post-reset snapshot must count the whole new period as the delta, not the difference.

        Without the Budget row, _did_budget_reset short-circuits to False and daily_spend
        would be understated by the pre-reset amount, permanently, in the time series.
        """
        now = datetime.now(timezone.utc)
        budget = SimpleNamespace(
            budget_id="b-1",
            budget_reset_at=(now + timedelta(days=30)).isoformat(),
            budget_duration="30d",
        )
        prev = _prev_row(period="100", cumulative="900", spend_date=now - timedelta(days=2))
        insert_mock = AsyncMock()
        service = MemberSpendService()
        session = MagicMock()

        with _patch_refresh_deps(
            [_snapshot(spend="120")],
            prev_rows={("atlas-core", "b-1", "u-1"): prev},
            budgets_map={"b-1": budget},
            insert_mock=insert_mock,
        ):
            await service._refresh_all_member_spend(session)

        rows = insert_mock.await_args[0][1]
        assert len(rows) == 1
        # Reset detected: the full 120 is new spend, not 120 - 100.
        assert rows[0].daily_spend == Decimal("120.000000000")
        assert rows[0].cumulative_spend == Decimal("1020.000000000")

    @pytest.mark.asyncio
    async def test_computes_plain_delta_when_no_reset_occurred(self):
        """Mid-cycle snapshot: the last reset predates prev_row, so only the increment counts."""
        now = datetime.now(timezone.utc)
        # Next reset in 20 days on a 30d cycle => last reset was 10 days ago,
        # which is before prev_row's spend_date, so no reset falls in the window.
        budget = SimpleNamespace(
            budget_id="b-1",
            budget_reset_at=(now + timedelta(days=20)).isoformat(),
            budget_duration="30d",
        )
        prev = _prev_row(period="100", cumulative="900", spend_date=now - timedelta(hours=1))
        insert_mock = AsyncMock()
        service = MemberSpendService()

        with _patch_refresh_deps(
            [_snapshot(spend="120")],
            prev_rows={("atlas-core", "b-1", "u-1"): prev},
            budgets_map={"b-1": budget},
            insert_mock=insert_mock,
        ):
            await service._refresh_all_member_spend(MagicMock())

        rows = insert_mock.await_args[0][1]
        assert rows[0].daily_spend == Decimal("20.000000000")

    @pytest.mark.asyncio
    async def test_one_invalid_snapshot_does_not_discard_the_others(self):
        """A single poisoned row must not abort the global refresh and stall the lazy path."""
        from codemie.service.spend_tracking.spend_collector_service import InvalidSpendSnapshotError

        insert_mock = AsyncMock()
        service = MemberSpendService()
        snapshots = [_snapshot(user_id="bad"), _snapshot(user_id="good")]

        def _delta(fresh, prev, budget, now):
            raise InvalidSpendSnapshotError("cumulative regression")

        with _patch_refresh_deps(snapshots, insert_mock=insert_mock):
            with patch(
                "codemie.service.analytics.handlers.member_spend_service._compute_spend_delta",
                side_effect=[InvalidSpendSnapshotError("cumulative regression"), (Decimal("5"), Decimal("5"))],
            ):
                await service._refresh_all_member_spend(MagicMock())

        rows = insert_mock.await_args[0][1]
        assert [r.user_id for r in rows] == ["good"]

    @pytest.mark.asyncio
    async def test_skips_zero_delta_rows_like_the_collector(self):
        prev = _prev_row(period="100", cumulative="900")
        insert_mock = AsyncMock()
        service = MemberSpendService()

        with _patch_refresh_deps(
            [_snapshot(spend="100")],
            prev_rows={("atlas-core", "b-1", "u-1"): prev},
            insert_mock=insert_mock,
        ):
            await service._refresh_all_member_spend(MagicMock())

        insert_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_records_a_reset_even_though_its_delta_is_zero(self):
        """A counter that dropped to 0 must be written, or the pre-reset row stays newest.

        The delta is zero for both a quiet member and a member whose spend was just
        reset. Skipping both leaves the stale pre-reset value as the latest row, so the
        read path keeps serving it until real spend moves the counter again.
        """
        prev = _prev_row(period="100", cumulative="900")
        insert_mock = AsyncMock()
        service = MemberSpendService()

        with _patch_refresh_deps(
            [_snapshot(spend="0")],
            prev_rows={("atlas-core", "b-1", "u-1"): prev},
            insert_mock=insert_mock,
        ):
            await service._refresh_all_member_spend(MagicMock())

        rows = insert_mock.await_args[0][1]
        assert len(rows) == 1
        assert rows[0].budget_period_spend == Decimal("0.000000000")
        assert rows[0].daily_spend == Decimal("0.000000000")
        assert rows[0].cumulative_spend == Decimal("900.000000000")

    @pytest.mark.asyncio
    async def test_no_snapshots_writes_nothing(self):
        insert_mock = AsyncMock()
        service = MemberSpendService()

        with _patch_refresh_deps([], insert_mock=insert_mock):
            await service._refresh_all_member_spend(MagicMock())

        insert_mock.assert_not_awaited()


class TestEnsureFreshRollbackFailure:
    """The degradation requirement holds even when the rollback itself fails."""

    @pytest.mark.asyncio
    async def test_rollback_failure_does_not_propagate(self):
        service = MemberSpendService()
        session = MagicMock()
        session.rollback = AsyncMock(side_effect=RuntimeError("connection is closed"))
        stale = datetime.now(timezone.utc) - timedelta(hours=3)

        with (
            patch.object(service, "_refresh_all_member_spend", new=AsyncMock(side_effect=RuntimeError("litellm down"))),
            patch.object(MemberSpendService, "_is_litellm_enabled", return_value=True),
            patch(
                "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository."
                "get_newest_member_spend_date",
                new=AsyncMock(return_value=stale),
            ),
        ):
            await service._ensure_fresh(session)  # must not raise

        session.rollback.assert_awaited_once()


class TestPersonalProjectExclusion:
    """Personal projects are excluded: their spend is metered against the user, not the project."""

    @staticmethod
    def _patched(service, memberships, applications):
        import contextlib

        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(service, "_ensure_fresh", new=AsyncMock()))
        stack.enter_context(
            patch(
                "codemie.repository.user_project_repository.user_project_repository.aget_by_user_id",
                new=AsyncMock(return_value=memberships),
            )
        )
        stack.enter_context(
            patch(
                "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository."
                "get_latest_member_rows_for_user",
                new=AsyncMock(return_value={}),
            )
        )
        stack.enter_context(
            patch(
                "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository."
                "get_project_reset_cutoffs",
                new=AsyncMock(return_value={}),
            )
        )
        stack.enter_context(
            patch(
                "codemie.repository.project_budget_repository.project_member_budget_assignment_repository."
                "get_active_by_user",
                new=AsyncMock(return_value=[]),
            )
        )
        stack.enter_context(
            patch(
                "codemie.repository.application_repository.application_repository.aget_all_non_deleted",
                new=AsyncMock(return_value=applications),
            )
        )
        return stack

    @pytest.mark.asyncio
    async def test_personal_project_is_omitted(self):
        service = MemberSpendService()
        memberships = [
            SimpleNamespace(project_name="atlas-core", user_id="u-1"),
            SimpleNamespace(project_name="me@corp.com", user_id="u-1"),
        ]
        applications = [
            SimpleNamespace(name="atlas-core", project_type="shared"),
            SimpleNamespace(name="me@corp.com", project_type="personal"),
        ]

        with self._patched(service, memberships, applications):
            _, rows = await service.get_user_project_spend(MagicMock(), "u-1")

        assert [r["project_name"] for r in rows] == ["atlas-core"]

    @pytest.mark.asyncio
    async def test_no_project_type_field_is_emitted(self):
        """The field existed only to let clients suppress these rows; the backend does it now."""
        service = MemberSpendService()
        memberships = [SimpleNamespace(project_name="atlas-core", user_id="u-1")]
        applications = [SimpleNamespace(name="atlas-core", project_type="shared")]

        with self._patched(service, memberships, applications):
            columns, rows = await service.get_user_project_spend(MagicMock(), "u-1")

        assert "project_type" not in rows[0]
        assert "project_type" not in [c["id"] for c in columns]

    @pytest.mark.asyncio
    async def test_user_with_only_a_personal_project_gets_empty_rows(self):
        service = MemberSpendService()
        memberships = [SimpleNamespace(project_name="me@corp.com", user_id="u-1")]
        applications = [SimpleNamespace(name="me@corp.com", project_type="personal")]

        with self._patched(service, memberships, applications):
            columns, rows = await service.get_user_project_spend(MagicMock(), "u-1")

        assert rows == []
        assert columns[0]["id"] == "project_name"

    @pytest.mark.asyncio
    async def test_unknown_project_is_kept_not_dropped(self):
        """Absence from applications must not silently remove a project the user belongs to."""
        service = MemberSpendService()
        memberships = [SimpleNamespace(project_name="ghost", user_id="u-1")]

        with self._patched(service, memberships, []):
            _, rows = await service.get_user_project_spend(MagicMock(), "u-1")

        assert [r["project_name"] for r in rows] == ["ghost"]


class TestDropRowsBeforeCutoff:
    """Rows on or before the category's last pre-reset observation are ignored."""

    @staticmethod
    def _rows():
        return {
            ("u-stale", "platform"): _member_row(
                "epm-aisa", "platform", "u-stale", "68.39", datetime(2026, 7, 14, tzinfo=timezone.utc)
            ),
            ("u-live", "platform"): _member_row(
                "epm-aisa", "platform", "u-live", "94.66", datetime(2026, 8, 24, tzinfo=timezone.utc)
            ),
            ("u-stale", "cli"): _member_row(
                "epm-aisa", "cli", "u-stale", "5.00", datetime(2026, 7, 14, tzinfo=timezone.utc)
            ),
        }

    def test_drops_rows_on_or_before_the_cutoff(self):
        from codemie.service.analytics.handlers.member_spend_service import _drop_rows_before_cutoff

        cutoffs = {"platform": datetime(2026, 7, 31, 23, tzinfo=timezone.utc)}
        kept = _drop_rows_before_cutoff(self._rows(), lambda key: cutoffs.get(key[1]))
        assert set(kept) == {("u-live", "platform"), ("u-stale", "cli")}

    def test_row_exactly_at_the_cutoff_is_dropped(self):
        from codemie.service.analytics.handlers.member_spend_service import _drop_rows_before_cutoff

        at = datetime(2026, 7, 31, 23, tzinfo=timezone.utc)
        rows = {("u-1", "platform"): _member_row("epm-aisa", "platform", "u-1", "1.00", at)}
        assert _drop_rows_before_cutoff(rows, lambda key: {"platform": at}.get(key[1])) == {}

    def test_category_without_a_cutoff_keeps_everything(self):
        from codemie.service.analytics.handlers.member_spend_service import _drop_rows_before_cutoff

        kept = _drop_rows_before_cutoff(self._rows(), lambda key: None)
        assert set(kept) == set(self._rows())

    def test_naive_spend_date_is_treated_as_utc(self):
        from codemie.service.analytics.handlers.member_spend_service import _drop_rows_before_cutoff

        rows = {("u-1", "platform"): _member_row("epm-aisa", "platform", "u-1", "1.00", datetime(2026, 7, 14))}
        cutoffs = {"platform": datetime(2026, 7, 31, 23, tzinfo=timezone.utc)}
        assert _drop_rows_before_cutoff(rows, lambda key: cutoffs.get(key[1])) == {}


class TestProjectResetCutoffInReadPath:
    """A stale pre-reset row must render as 0 on the project member table."""

    @pytest.mark.asyncio
    async def test_project_member_spend_zeroes_a_pre_reset_row(self):
        service = MemberSpendService()
        session = MagicMock()
        members = [SimpleNamespace(user_id="u-1", project_name="epm-aisa")]
        stale = _member_row("epm-aisa", "platform", "u-1", "68.39", datetime(2026, 7, 14, tzinfo=timezone.utc))
        allocations = [
            SimpleNamespace(
                project_name="epm-aisa", budget_category="platform", user_id="u-1", allocated_max_budget=100.0
            )
        ]
        cutoffs = {"platform": datetime(2026, 7, 31, 23, tzinfo=timezone.utc)}

        with (
            patch.object(service, "_ensure_fresh", new=AsyncMock()),
            patch(
                "codemie.repository.user_project_repository.user_project_repository.aget_by_project_name",
                new=AsyncMock(return_value=members),
            ),
            patch(
                "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository."
                "get_latest_member_rows_for_project",
                new=AsyncMock(return_value={("u-1", "platform"): stale}),
            ),
            patch(
                "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository."
                "get_project_reset_cutoffs",
                new=AsyncMock(return_value=cutoffs),
            ),
            patch(
                "codemie.repository.project_budget_repository.project_member_budget_assignment_repository."
                "get_active_by_project",
                new=AsyncMock(return_value=allocations),
            ),
        ):
            _, rows = await service.get_project_member_spend(session, "epm-aisa")

        assert rows[0]["platform"] == 0
        assert rows[0]["platform_limit"] == 100.0
