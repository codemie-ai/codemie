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

import asyncio

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from codemie.service.analytics.handlers.member_spend_service import (
    MemberSpendService,
    _is_stale,
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
class TestIsStale:
    """Tests for the shared member spend staleness check."""

    def test_missing_timestamp_is_stale(self):
        assert _is_stale(None) is True

    def test_recent_timestamp_is_fresh(self):
        assert _is_stale(datetime.now(timezone.utc) - timedelta(minutes=2)) is False

    def test_old_timestamp_is_stale(self):
        assert _is_stale(datetime.now(timezone.utc) - timedelta(hours=3)) is True

    def test_naive_datetime_is_treated_as_utc(self):
        naive_recent = (datetime.now(timezone.utc) - timedelta(minutes=2)).replace(tzinfo=None)
        assert _is_stale(naive_recent) is False


@pytest.mark.usefixtures("ten_minute_ttl")
class TestEnsureFresh:
    """Tests for MemberSpendService._ensure_fresh."""

    @pytest.mark.asyncio
    async def test_skips_refresh_when_checked_within_ttl(self):
        service = MemberSpendService()
        service._last_checked_at = datetime.now(timezone.utc) - timedelta(minutes=1)

        with (
            patch.object(service, "_refresh_all_member_spend", new=AsyncMock()) as refresh,
            patch.object(MemberSpendService, "_is_litellm_enabled", return_value=True),
        ):
            await service._ensure_fresh(MagicMock())

        refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_refreshes_when_last_check_is_older_than_ttl(self):
        service = MemberSpendService()
        service._last_checked_at = datetime.now(timezone.utc) - timedelta(minutes=10, seconds=1)

        with (
            patch.object(service, "_refresh_all_member_spend", new=AsyncMock()) as refresh,
            patch.object(MemberSpendService, "_is_litellm_enabled", return_value=True),
        ):
            await service._ensure_fresh(MagicMock())

        refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_refresh_when_litellm_disabled(self):
        service = MemberSpendService()
        session = MagicMock()

        with (
            patch.object(service, "_refresh_all_member_spend", new=AsyncMock()) as refresh,
            patch.object(MemberSpendService, "_is_litellm_enabled", return_value=False),
        ):
            await service._ensure_fresh(session)

        refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_first_call_refreshes(self):
        service = MemberSpendService()
        session = MagicMock()

        with (
            patch.object(service, "_refresh_all_member_spend", new=AsyncMock()) as refresh,
            patch.object(MemberSpendService, "_is_litellm_enabled", return_value=True),
        ):
            await service._ensure_fresh(session)

        refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fresh_member_rows_do_not_suppress_global_refresh(self):
        """User-scoped refreshes write fresh rows for one user; that must not mark everyone fresh."""
        service = MemberSpendService()
        fresh = datetime.now(timezone.utc) - timedelta(minutes=1)

        with (
            patch.object(service, "_refresh_all_member_spend", new=AsyncMock()) as refresh,
            patch.object(MemberSpendService, "_is_litellm_enabled", return_value=True),
            patch(
                "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository."
                "get_newest_member_spend_date",
                new=AsyncMock(return_value=fresh),
                create=True,
            ),
        ):
            await service._ensure_fresh(MagicMock())

        refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_provider_failure_does_not_raise(self):
        """LiteLLM being unavailable must degrade to stale data, never fail the request."""
        service = MemberSpendService()
        session = MagicMock()
        session.rollback = AsyncMock()

        with (
            patch.object(
                service,
                "_refresh_all_member_spend",
                new=AsyncMock(side_effect=RuntimeError("litellm down")),
            ),
            patch.object(MemberSpendService, "_is_litellm_enabled", return_value=True),
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
                "get_project_reset_cutoffs_for_projects",
                new=AsyncMock(return_value={}),
            ),
            patch(
                "codemie.repository.project_budget_repository.project_member_budget_assignment_repository."
                "get_active_by_user",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "codemie.repository.application_repository.application_repository.aget_personal_project_names",
                new=AsyncMock(return_value=set()),
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
                "get_project_reset_cutoffs_for_projects",
                new=AsyncMock(return_value={}),
            ),
            patch(
                "codemie.repository.project_budget_repository.project_member_budget_assignment_repository."
                "get_active_by_user",
                new=AsyncMock(return_value=allocations),
            ),
            patch(
                "codemie.repository.application_repository.application_repository.aget_personal_project_names",
                new=AsyncMock(return_value=set()),
            ),
        ):
            _, rows = await service.get_user_project_spend(session, "u-1")

        assert rows[0]["cli"] == 40.0
        assert rows[0]["cli_limit"] == 100.0
        assert rows[0]["platform"] == 0
        assert rows[0]["platform_limit"] is None


class TestGetUserProjectRows:
    """Tests for MemberSpendService.get_user_project_rows in its admin (default) mode."""

    @pytest.mark.asyncio
    async def test_get_user_project_rows_calls_batched_cutoff(self):
        svc = MemberSpendService()
        session = MagicMock()
        memberships = [SimpleNamespace(project_name="proj-a", user_id="u-1")]

        with (
            patch.object(svc, "_ensure_fresh", new=AsyncMock()),
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
                "codemie.repository.project_budget_repository.project_member_budget_assignment_repository."
                "get_active_by_user",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "codemie.repository.application_repository.application_repository.aget_personal_project_names",
                new=AsyncMock(return_value=set()),
            ),
            patch(
                "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository."
                "get_project_reset_cutoffs_for_projects",
                new=AsyncMock(return_value={}),
            ) as batched,
            patch(
                "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository."
                "get_project_reset_cutoffs",
                new=AsyncMock(),
            ) as single,
        ):
            await svc.get_user_project_rows(session, "u-1")

        batched.assert_called_once()
        single.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_user_project_spend_still_returns_columns_and_rows(self):
        """AC 7: the public admin-endpoint contract is unchanged by the extraction."""
        svc = MemberSpendService()
        rows = [{"project_name": "proj-a"}]

        with patch.object(svc, "get_user_project_rows", new=AsyncMock(return_value=rows)):
            columns, got = await svc.get_user_project_spend(MagicMock(), "u-1")

        assert columns == _category_columns("project_name", "Project")
        assert got == rows


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

    @pytest.mark.asyncio
    async def test_rows_are_ordered_by_user_id(self):
        """The endpoint paginates, so rows need a stable order or pages skip and repeat members."""
        service = MemberSpendService()
        members = [SimpleNamespace(user_id=user_id, project_name="atlas-core") for user_id in ("u-3", "u-1", "u-2")]

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
            _, rows = await service.get_project_member_spend(MagicMock(), "atlas-core")

        assert [row["user_id"] for row in rows] == ["u-1", "u-2", "u-3"]


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


def _patch_refresh_deps(snapshots, prev_rows=None, budgets_map=None, insert_mock=None, lock_mock=None, provider=None):
    """Patch the collaborators of _refresh_all_member_spend. Returns a contextlib.ExitStack."""
    import contextlib

    if provider is None:
        provider = MagicMock()
        provider.collect_member_budget_spend = AsyncMock(return_value=snapshots)
    stack = contextlib.ExitStack()
    stack.enter_context(
        patch(
            "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository."
            "lock_all_member_refresh",
            new=lock_mock or AsyncMock(),
        )
    )
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
    @pytest.mark.asyncio
    async def test_takes_exclusive_global_lock_before_reading_provider(self):
        """Personal-page refreshes take the same lock in shared mode, so the two never write together."""
        calls = []
        lock = AsyncMock(side_effect=lambda *_a: calls.append("lock"))
        provider = MagicMock()
        provider.collect_member_budget_spend = AsyncMock(side_effect=lambda: calls.append("collect") or [])

        with _patch_refresh_deps([], lock_mock=lock, provider=provider):
            await MemberSpendService()._refresh_all_member_spend(MagicMock())

        assert calls == ["lock", "collect"]

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

        with (
            patch.object(service, "_refresh_all_member_spend", new=AsyncMock(side_effect=RuntimeError("litellm down"))),
            patch.object(MemberSpendService, "_is_litellm_enabled", return_value=True),
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
                "get_project_reset_cutoffs_for_projects",
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
                "codemie.repository.application_repository.application_repository.aget_personal_project_names",
                new=AsyncMock(return_value={a.name for a in applications if a.project_type == "personal"}),
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
    async def test_enforced_only_keeps_projects_with_enforcement_enabled(self):
        service = MemberSpendService()
        memberships = [
            SimpleNamespace(project_name="atlas-core", user_id="u-1"),
            SimpleNamespace(project_name="beta-lab", user_id="u-1"),
            SimpleNamespace(project_name="me@corp.com", user_id="u-1"),
        ]
        applications = [SimpleNamespace(name="me@corp.com", project_type="personal")]

        with (
            self._patched(service, memberships, applications),
            patch(
                "codemie.service.settings.settings.SettingsService.get_projects_enforcing_member_spend_limits",
                return_value={"beta-lab"},
            ) as enforcing,
        ):
            rows = await service.get_user_project_rows(MagicMock(), "u-1", enforced_only=True)

        assert [r["project_name"] for r in rows] == ["beta-lab"]
        enforcing.assert_called_once_with(["atlas-core", "beta-lab"])

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


def _latch_patches(svc, refresh_mock):
    from codemie.configs.config import config

    stack = ExitStack()
    stack.enter_context(patch.object(svc, "_refresh_all_member_spend", new=refresh_mock))
    stack.enter_context(patch.object(MemberSpendService, "_is_litellm_enabled", return_value=True))
    stack.enter_context(patch.object(config, "BUDGET_MEMBER_SPEND_STALENESS_THRESHOLD_MS", 600000))
    return stack


class TestEnsureFreshLatch:
    """Tests for the per-process freshness latch."""

    @pytest.mark.asyncio
    async def test_second_call_within_ttl_skips_provider(self):
        svc = MemberSpendService()
        session = MagicMock()
        refresh = AsyncMock()
        with _latch_patches(svc, refresh):
            await svc._ensure_fresh(session)
            await svc._ensure_fresh(session)
        refresh.assert_awaited_once()
        assert svc._last_checked_at is not None

    @pytest.mark.asyncio
    async def test_concurrent_requests_coalesce(self):
        svc = MemberSpendService()
        calls = 0

        async def slow_refresh(_session):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)

        from codemie.configs.config import config

        with (
            patch.object(svc, "_refresh_all_member_spend", side_effect=slow_refresh),
            patch.object(MemberSpendService, "_is_litellm_enabled", return_value=True),
            patch.object(config, "BUDGET_MEMBER_SPEND_STALENESS_THRESHOLD_MS", 600000),
        ):
            await asyncio.gather(*[svc._ensure_fresh(MagicMock()) for _ in range(5)])
        assert calls == 1

    @pytest.mark.asyncio
    async def test_no_change_refresh_writes_nothing_but_still_latches(self):
        """AC 10: the latch must not be implemented by touching member rows."""
        svc = MemberSpendService()
        insert_mock = AsyncMock()
        from codemie.configs.config import config

        with (
            patch.object(MemberSpendService, "_is_litellm_enabled", return_value=True),
            patch.object(config, "BUDGET_MEMBER_SPEND_STALENESS_THRESHOLD_MS", 600000),
            _patch_refresh_deps([], insert_mock=insert_mock),
        ):
            await svc._ensure_fresh(MagicMock())
        insert_mock.assert_not_awaited()
        assert svc._last_checked_at is not None


class TestResetDates:
    @staticmethod
    def _alloc(**overrides):
        fields = {
            "project_budget_id": "proj-budget",
            "user_id": "user-1",
            "allocation_mode": "shared",
            "effective_budget_id": None,
            "override_budget_id": None,
            "shared_budget_id": None,
            "budget_reset_at": "allocation-reset",
        }
        fields.update(overrides)
        return SimpleNamespace(**fields)

    async def _reset_dates(self, alloc, budgets: dict):
        get_by_ids = AsyncMock(return_value=budgets)
        with patch("codemie.service.analytics.handlers.member_spend_service.budget_repository.get_by_ids", get_by_ids):
            result = await MemberSpendService._reset_dates(MagicMock(), {("proj", "platform"): alloc})
        return result[("proj", "platform")], get_by_ids.await_args.args[1]

    @pytest.mark.asyncio
    async def test_reads_reset_from_explicit_effective_budget(self):
        alloc = self._alloc(effective_budget_id="eff", shared_budget_id="proj-budget:shared")
        reset, looked_up = await self._reset_dates(alloc, {"eff": SimpleNamespace(budget_reset_at="eff-reset")})
        assert looked_up == ["eff"]
        assert reset == "eff-reset"

    @pytest.mark.asyncio
    async def test_derives_shared_child_budget_when_ids_are_empty(self):
        alloc = self._alloc()
        reset, looked_up = await self._reset_dates(
            alloc, {"proj-budget:shared": SimpleNamespace(budget_reset_at="shared-reset")}
        )
        assert looked_up == ["proj-budget:shared"]
        assert reset == "shared-reset"

    @pytest.mark.asyncio
    async def test_derives_override_child_budget_for_fixed_allocation(self):
        alloc = self._alloc(allocation_mode="fixed")
        _, looked_up = await self._reset_dates(alloc, {})
        assert looked_up == ["proj-budget:user:user-1"]

    @pytest.mark.asyncio
    async def test_falls_back_to_allocation_reset_when_budget_missing(self):
        reset, _ = await self._reset_dates(self._alloc(), {})
        assert reset == "allocation-reset"


def _member_snapshot(spend: str, *, project_name: str = "atlas", budget_id: str = "pb-atlas"):
    from codemie.service.budget.budget_enums import BudgetCategory
    from codemie.service.budget.provider import MemberBudgetSpendSnapshot

    return MemberBudgetSpendSnapshot(
        project_name=project_name,
        budget_category=BudgetCategory.PLATFORM,
        budget_id=budget_id,
        user_id="u-1",
        spend=Decimal(spend),
        provider_subject_id=f"codemie:project:{project_name}:category:platform:user:u-1",
    )


class TestSnapshotToRowAllowZero:
    def test_snapshot_to_row_allow_zero_seeds_first_row(self):
        from codemie.service.analytics.handlers.member_spend_service import _snapshot_to_row

        now = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
        snapshot = _member_snapshot("0")

        assert _snapshot_to_row(snapshot, None, None, now) is None
        row = _snapshot_to_row(snapshot, None, None, now, allow_zero=True)
        assert row is not None
        assert row.daily_spend == Decimal("0")
        assert row.budget_period_spend == Decimal("0")
        assert row.spend_subject_type == "member_budget"


@pytest.mark.usefixtures("ten_minute_ttl")
class TestUserScopedRefresh:
    """Personal page path: refresh only this user's member spend, keyed on their own rows."""

    @staticmethod
    def _allocation(project: str = "atlas") -> SimpleNamespace:
        return SimpleNamespace(
            project_name=project,
            budget_category="platform",
            user_id="u-1",
            project_budget_id=f"pb-{project}",
            allocated_max_budget=10.0,
            budget_reset_at=None,
            provider_metadata={"provider_member_ref": f"codemie:project:{project}:category:platform:user:u-1"},
        )

    @staticmethod
    def _row(spend: str, spend_date: datetime, *, project: str = "atlas", budget_id: str = "pb-atlas"):
        return ProjectSpendTracking(
            id=uuid4(),
            project_name=project,
            spend_date=spend_date,
            daily_spend=Decimal(spend),
            cumulative_spend=Decimal(spend),
            budget_period_spend=Decimal(spend),
            budget_id=budget_id,
            budget_category="platform",
            user_id="u-1",
            spend_subject_type="member_budget",
        )

    @staticmethod
    def _patched(
        service,
        *,
        latest,
        projects=("atlas",),
        enforced=None,
        allocations=None,
        cutoffs=None,
        prev_rows=None,
        snapshots=(),
        provider_error=None,
        lock=True,
        budgets=None,
    ):
        from codemie.service.analytics.handlers import member_spend_service as mss

        pending = list(latest)

        async def _latest_rows(*_args, **_kwargs):
            return pending.pop(0) if len(pending) > 1 else pending[0]

        provider = MagicMock()
        provider.collect_member_budget_spend_for_allocations = AsyncMock(
            return_value=list(snapshots), side_effect=provider_error
        )
        mocks = SimpleNamespace(
            provider=provider,
            ensure_fresh=AsyncMock(),
            insert=AsyncMock(),
            touch=AsyncMock(),
            lock=AsyncMock(return_value=lock),
        )
        if allocations is None:
            allocations = [TestUserScopedRefresh._allocation()]
        memberships = [SimpleNamespace(project_name=p, user_id="u-1") for p in projects]
        tracking = mss.project_spend_tracking_repository
        stack = ExitStack()
        stack.enter_context(patch.object(service, "_ensure_fresh", new=mocks.ensure_fresh))
        stack.enter_context(patch.object(MemberSpendService, "_is_litellm_enabled", return_value=True))
        stack.enter_context(
            patch.object(mss.user_project_repository, "aget_by_user_id", new=AsyncMock(return_value=memberships))
        )
        stack.enter_context(
            patch.object(mss.application_repository, "aget_personal_project_names", new=AsyncMock(return_value=set()))
        )
        if enforced is not None:
            stack.enter_context(
                patch(
                    "codemie.service.settings.settings.SettingsService.get_projects_enforcing_member_spend_limits",
                    return_value=set(enforced),
                )
            )
        stack.enter_context(
            patch.object(
                mss.project_member_budget_assignment_repository,
                "get_active_by_user",
                new=AsyncMock(return_value=allocations),
            )
        )
        stack.enter_context(patch.object(tracking, "get_latest_member_rows_for_user", new=_latest_rows))
        stack.enter_context(
            patch.object(tracking, "get_project_reset_cutoffs_for_projects", new=AsyncMock(return_value=cutoffs or {}))
        )
        stack.enter_context(patch.object(tracking, "try_lock_member_refresh", new=mocks.lock))
        stack.enter_context(
            patch.object(
                tracking, "get_latest_before_by_member_budget_ids", new=AsyncMock(return_value=prev_rows or {})
            )
        )
        stack.enter_context(patch.object(tracking, "insert_member_budget_entries", new=mocks.insert))
        stack.enter_context(patch.object(tracking, "touch_member_spend_dates", new=mocks.touch))
        stack.enter_context(
            patch.object(mss.budget_repository, "get_by_ids", new=AsyncMock(return_value=budgets or {}))
        )
        stack.enter_context(patch.object(mss, "get_active_provider", return_value=provider))
        return stack, mocks

    @staticmethod
    async def _run(service, session=None, **kwargs):
        return await service.get_user_project_rows(session or AsyncMock(), "u-1", user_scoped_refresh=True, **kwargs)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @pytest.mark.asyncio
    async def test_fresh_rows_skip_provider(self):
        service = MemberSpendService()
        fresh = {("atlas", "platform"): self._row("5", self._now() - timedelta(minutes=1))}
        stack, mocks = self._patched(service, latest=[fresh])

        with stack:
            rows = await self._run(service)

        assert [r["project_name"] for r in rows] == ["atlas"]
        mocks.provider.collect_member_budget_spend_for_allocations.assert_not_awaited()
        mocks.lock.assert_not_awaited()
        mocks.ensure_fresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stale_rows_refresh_only_selected_allocations(self):
        service = MemberSpendService()
        atlas, beta = self._allocation("atlas"), self._allocation("beta")
        stack, mocks = self._patched(
            service, latest=[{}], projects=("atlas", "beta"), enforced={"atlas"}, allocations=[atlas, beta]
        )

        with stack:
            await self._run(service, enforced_only=True)

        mocks.provider.collect_member_budget_spend_for_allocations.assert_awaited_once_with([atlas])
        mocks.ensure_fresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unchanged_spend_touches_latest_row(self):
        service = MemberSpendService()
        prev = self._row("5", self._now() - timedelta(hours=1))
        stack, mocks = self._patched(
            service,
            latest=[{("atlas", "platform"): prev}],
            prev_rows={("atlas", "pb-atlas", "u-1"): prev},
            snapshots=[_member_snapshot("5.0000000000")],
        )

        with stack:
            await self._run(service)

        mocks.touch.assert_awaited_once()
        assert mocks.touch.await_args.args[1] == [prev.id]
        mocks.insert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_changed_spend_inserts_row(self):
        service = MemberSpendService()
        prev = self._row("5", self._now() - timedelta(hours=1))
        stack, mocks = self._patched(
            service,
            latest=[{("atlas", "platform"): prev}],
            prev_rows={("atlas", "pb-atlas", "u-1"): prev},
            snapshots=[_member_snapshot("7")],
        )

        with stack:
            await self._run(service)

        inserted = mocks.insert.await_args.args[1]
        assert [r.daily_spend for r in inserted] == [Decimal("2")]
        mocks.touch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reset_to_zero_inserts_row(self):
        service = MemberSpendService()
        prev = self._row("5", self._now() - timedelta(hours=1))
        stack, mocks = self._patched(
            service,
            latest=[{("atlas", "platform"): prev}],
            prev_rows={("atlas", "pb-atlas", "u-1"): prev},
            snapshots=[_member_snapshot("0")],
        )

        with stack:
            await self._run(service)

        inserted = mocks.insert.await_args.args[1]
        assert [r.budget_period_spend for r in inserted] == [Decimal("0")]
        mocks.touch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_zero_spend_new_budget_seeds_zero_row(self):
        service = MemberSpendService()
        stack, mocks = self._patched(service, latest=[{}], snapshots=[_member_snapshot("0")])

        with stack:
            await self._run(service)

        inserted = mocks.insert.await_args.args[1]
        assert [(r.daily_spend, r.budget_id) for r in inserted] == [(Decimal("0"), "pb-atlas")]

    @pytest.mark.asyncio
    async def test_old_budget_row_in_display_map_is_not_touched(self):
        service = MemberSpendService()
        old = self._row("5", self._now() - timedelta(hours=1), budget_id="pb-old")
        stack, mocks = self._patched(
            service, latest=[{("atlas", "platform"): old}], prev_rows={}, snapshots=[_member_snapshot("5")]
        )

        with stack:
            await self._run(service)

        inserted = mocks.insert.await_args.args[1]
        assert [r.budget_id for r in inserted] == ["pb-atlas"]
        mocks.touch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pre_cutoff_unchanged_row_is_neither_touched_nor_inserted(self):
        service = MemberSpendService()
        prev = self._row("5", self._now() - timedelta(days=2))
        stack, mocks = self._patched(
            service,
            latest=[{("atlas", "platform"): prev}],
            cutoffs={"atlas": {"platform": self._now() - timedelta(days=1)}},
            prev_rows={("atlas", "pb-atlas", "u-1"): prev},
            snapshots=[_member_snapshot("5")],
        )

        with stack:
            rows = await self._run(service)

        mocks.touch.assert_not_awaited()
        mocks.insert.assert_not_awaited()
        assert rows[0]["platform"] == 0

    @pytest.mark.asyncio
    async def test_pre_cutoff_unchanged_row_is_not_reinserted_when_budget_table_saw_the_reset(self):
        """The budget table knows the reset, LiteLLM still reports the old value: keep it hidden."""
        service = MemberSpendService()
        now = self._now()
        prev = self._row("5", now - timedelta(days=2))
        from codemie.service.spend_tracking.spend_collector_service import LiteLLMSpendCollectorService

        last_reset = now - timedelta(days=1)
        next_reset = last_reset + LiteLLMSpendCollectorService._parse_budget_duration_to_delta("30d")
        budget = SimpleNamespace(budget_id="pb-atlas", budget_reset_at=next_reset.isoformat(), budget_duration="30d")
        stack, mocks = self._patched(
            service,
            latest=[{("atlas", "platform"): prev}],
            cutoffs={"atlas": {"platform": now - timedelta(days=1, hours=12)}},
            prev_rows={("atlas", "pb-atlas", "u-1"): prev},
            snapshots=[_member_snapshot("5")],
            budgets={"pb-atlas": budget},
        )

        with stack:
            rows = await self._run(service)

        mocks.insert.assert_not_awaited()
        mocks.touch.assert_not_awaited()
        assert rows[0]["platform"] == 0

    @pytest.mark.asyncio
    async def test_equal_spend_across_budget_reset_is_not_touched(self):
        from codemie.service.spend_tracking.spend_collector_service import LiteLLMSpendCollectorService

        service = MemberSpendService()
        prev = self._row("5", self._now() - timedelta(hours=1))
        stack, mocks = self._patched(
            service,
            latest=[{("atlas", "platform"): prev}],
            prev_rows={("atlas", "pb-atlas", "u-1"): prev},
            snapshots=[_member_snapshot("5")],
        )

        with stack, patch.object(LiteLLMSpendCollectorService, "_did_budget_reset", return_value=True):
            await self._run(service)

        mocks.touch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_lock_not_acquired_skips_refresh(self):
        service = MemberSpendService()
        stale = {("atlas", "platform"): self._row("5", self._now() - timedelta(hours=1))}
        stack, mocks = self._patched(service, latest=[stale], lock=False, snapshots=[_member_snapshot("7")])

        with stack:
            rows = await self._run(service)

        mocks.provider.collect_member_budget_spend_for_allocations.assert_not_awaited()
        assert rows[0]["platform"] == 5.0

    @pytest.mark.asyncio
    async def test_fresh_after_lock_recheck_skips_provider(self):
        service = MemberSpendService()
        stale = {("atlas", "platform"): self._row("5", self._now() - timedelta(hours=1))}
        fresh = {("atlas", "platform"): self._row("7", self._now())}
        stack, mocks = self._patched(service, latest=[stale, fresh], snapshots=[_member_snapshot("7")])

        with stack:
            await self._run(service)

        mocks.lock.assert_awaited_once()
        mocks.provider.collect_member_budget_spend_for_allocations.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_provider_failure_keeps_db_rows(self):
        service = MemberSpendService()
        session = AsyncMock()
        stale = {("atlas", "platform"): self._row("5", self._now() - timedelta(hours=1))}
        stack, _ = self._patched(service, latest=[stale], provider_error=RuntimeError("litellm down"))

        with stack:
            rows = await self._run(service, session=session)

        session.rollback.assert_awaited()
        assert [(r["project_name"], r["platform"]) for r in rows] == [("atlas", 5.0)]

    @pytest.mark.asyncio
    async def test_customer_missing_renders_db_rows(self):
        service = MemberSpendService()
        stale = {("atlas", "platform"): self._row("5", self._now() - timedelta(hours=1))}
        stack, mocks = self._patched(service, latest=[stale], snapshots=[])

        with stack:
            rows = await self._run(service)

        mocks.insert.assert_not_awaited()
        mocks.touch.assert_not_awaited()
        assert rows[0]["platform"] == 5.0

    @pytest.mark.asyncio
    async def test_admin_path_still_uses_global_refresh(self):
        service = MemberSpendService()
        stack, mocks = self._patched(service, latest=[{}])

        with stack:
            await service.get_user_project_spend(AsyncMock(), "u-1")

        mocks.ensure_fresh.assert_awaited_once()
        mocks.provider.collect_member_budget_spend_for_allocations.assert_not_awaited()
