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

"""Unit tests for LeaderboardHandler.

Covers snapshot resolution, summary/entries/detail formatting,
trigger_computation guard, and helper methods (_entry_to_row,
_rank_delta, _score_delta, _enrich_dimensions).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.service.analytics.handlers.leaderboard_handler import LeaderboardHandler

# Patch target for functions imported locally inside handler methods.
# Because the handler does ``from codemie.service.leaderboard.framework_metadata import ...``
# inside each method body, the name is resolved from the *source* module every call.
_FM_MODULE = "codemie.service.leaderboard.framework_metadata"
_HANDLER_MODULE = "codemie.service.analytics.handlers.leaderboard_handler"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_snapshot(**overrides) -> MagicMock:
    defaults = {
        "id": "snap-1",
        "snapshot_type": "rolling_live",
        "season_key": None,
        "period_label": "Last 30 days",
        "period_start": datetime(2026, 3, 1, tzinfo=timezone.utc),
        "period_end": datetime(2026, 3, 31, tzinfo=timezone.utc),
        "period_days": 30,
        "is_final": False,
        "status": "completed",
        "completed_at": datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc),
        "comparison_snapshot_id": None,
        "total_users": 10,
        "source_run_type": "scheduled",
        "date": datetime(2026, 3, 31, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    snap = MagicMock()
    for k, v in defaults.items():
        setattr(snap, k, v)
    return snap


def _make_entry(**overrides) -> MagicMock:
    defaults = {
        "snapshot_id": "snap-1",
        "user_id": "user-1",
        "user_name": "Alice",
        "user_email": "alice@test.com",
        "rank": 1,
        "total_score": 85.0,
        "tier_name": "pioneer",
        "tier_level": 5,
        "usage_intent": "hybrid",
        "dimensions": [
            {"id": "d1", "label": "D1", "score": 0.9, "components": []},
            {"id": "d5", "label": "D5", "score": 0.7, "components": []},
        ],
        "summary_metrics": {"total_spend": 42.5},
        "projects": ["proj-a"],
    }
    defaults.update(overrides)
    entry = MagicMock()
    for k, v in defaults.items():
        setattr(entry, k, v)
    return entry


@asynccontextmanager
async def _fake_session():
    yield AsyncMock()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_repo():
    return MagicMock()


@pytest.fixture()
def mock_user():
    return MagicMock()


@pytest.fixture()
def handler(mock_repo, mock_user):
    with patch(f"{_HANDLER_MODULE}.get_async_session", side_effect=_fake_session):
        h = LeaderboardHandler(mock_user)
        h._repository = mock_repo
        yield h


# ---------------------------------------------------------------------------
# _resolve_snapshot
# ---------------------------------------------------------------------------


class TestResolveSnapshot:
    @pytest.mark.asyncio
    async def test_resolve_by_snapshot_id(self, handler, mock_repo):
        """When snapshot_id is provided, fetch directly by id."""
        expected = _make_snapshot()
        mock_repo.get_snapshot_by_id = AsyncMock(return_value=expected)

        result = await handler._resolve_snapshot(AsyncMock(), snapshot_id="snap-1", view=None, season_key=None)

        assert result is expected
        mock_repo.get_snapshot_by_id.assert_called_once()

    @pytest.mark.asyncio
    async def test_resolve_by_view_and_season_key(self, handler, mock_repo):
        """Monthly view + season_key should resolve via type+key lookup."""
        expected = _make_snapshot(snapshot_type="season_month", season_key="2026-03")
        mock_repo.get_snapshot_by_type_and_key = AsyncMock(return_value=expected)

        result = await handler._resolve_snapshot(AsyncMock(), snapshot_id=None, view="monthly", season_key="2026-03")

        assert result is expected
        mock_repo.get_snapshot_by_type_and_key.assert_called_once()
        call_kwargs = mock_repo.get_snapshot_by_type_and_key.call_args
        # Positional args: session, snapshot_type, season_key
        assert call_kwargs[0][1] == "season_month"
        assert call_kwargs[0][2] == "2026-03"
        assert call_kwargs[1] == {"status": "completed", "final_only": True}

    @pytest.mark.asyncio
    async def test_resolve_latest_for_current_view(self, handler, mock_repo):
        """No snapshot_id + current view falls back to latest rolling snapshot."""
        expected = _make_snapshot()
        mock_repo.get_latest_snapshot_by_type = AsyncMock(return_value=expected)

        result = await handler._resolve_snapshot(AsyncMock(), snapshot_id=None, view="current", season_key=None)

        assert result is expected
        mock_repo.get_latest_snapshot_by_type.assert_called_once()
        call_kwargs = mock_repo.get_latest_snapshot_by_type.call_args
        assert call_kwargs[0][1] == "rolling_live"
        assert call_kwargs[1] == {"status": "completed", "is_final": False}

    @pytest.mark.asyncio
    async def test_resolve_latest_for_quarterly_view_requires_final_snapshot(self, handler, mock_repo):
        expected = _make_snapshot(snapshot_type="season_quarter")
        mock_repo.get_latest_snapshot_by_type = AsyncMock(return_value=expected)

        result = await handler._resolve_snapshot(AsyncMock(), snapshot_id=None, view="quarterly", season_key=None)

        assert result is expected
        call_kwargs = mock_repo.get_latest_snapshot_by_type.call_args
        assert call_kwargs[1] == {"status": "completed", "is_final": True}


# ---------------------------------------------------------------------------
# get_leaderboard_summary
# ---------------------------------------------------------------------------


class TestGetLeaderboardSummary:
    @pytest.mark.asyncio
    async def test_returns_empty_metrics_when_no_snapshot(self, handler, mock_repo):
        """No snapshot found should yield empty metrics list."""
        mock_repo.get_snapshot_by_id = AsyncMock(return_value=None)
        mock_repo.get_latest_snapshot_by_type = AsyncMock(return_value=None)

        with patch(f"{_HANDLER_MODULE}.get_async_session", side_effect=_fake_session):
            result = await handler.get_leaderboard_summary()

        assert result["data"]["metrics"] == []
        assert "metadata" in result

    @pytest.mark.asyncio
    async def test_builds_metrics_with_snapshot(self, handler, mock_repo):
        """With a valid snapshot, summary should contain expected metric ids."""
        snapshot = _make_snapshot()
        mock_repo.get_latest_snapshot_by_type = AsyncMock(return_value=snapshot)
        mock_repo.get_tier_distribution = AsyncMock(return_value=[{"tier_name": "pioneer", "user_count": 3}])
        top_entry = _make_entry(total_score=95.0)
        mock_repo.get_top_entries = AsyncMock(return_value=[top_entry])
        mock_repo.get_average_score = AsyncMock(return_value=62.5)

        with (
            patch(f"{_HANDLER_MODULE}.get_async_session", side_effect=_fake_session),
            patch(
                f"{_FM_MODULE}.get_framework_metadata",
                return_value={
                    "tiers": [
                        {"name": "pioneer", "label": "Pioneer", "plural_label": "Pioneers"},
                    ]
                },
            ),
        ):
            result = await handler.get_leaderboard_summary()

        metric_ids = [m["id"] for m in result["data"]["metrics"]]
        assert "total_users" in metric_ids
        assert "avg_score" in metric_ids
        assert "top_score" in metric_ids
        assert "pioneer_count" in metric_ids

        avg_metric = next(m for m in result["data"]["metrics"] if m["id"] == "avg_score")
        assert avg_metric["value"] == 62.5


# ---------------------------------------------------------------------------
# get_leaderboard_entries
# ---------------------------------------------------------------------------


class TestGetLeaderboardEntries:
    @pytest.mark.asyncio
    async def test_empty_response_when_no_snapshot(self, handler, mock_repo):
        mock_repo.get_latest_snapshot_by_type = AsyncMock(return_value=None)

        with patch(f"{_HANDLER_MODULE}.get_async_session", side_effect=_fake_session):
            result = await handler.get_leaderboard_entries()

        assert result["data"]["rows"] == []
        assert result["data"]["columns"] == []


# ---------------------------------------------------------------------------
# get_leaderboard_user_detail
# ---------------------------------------------------------------------------


class TestGetLeaderboardUserDetail:
    @pytest.mark.asyncio
    async def test_returns_empty_data_when_entry_not_found(self, handler, mock_repo):
        snapshot = _make_snapshot()
        mock_repo.get_latest_snapshot_by_type = AsyncMock(return_value=snapshot)
        mock_repo.get_entry_by_user = AsyncMock(return_value=None)

        with patch(f"{_HANDLER_MODULE}.get_async_session", side_effect=_fake_session):
            result = await handler.get_leaderboard_user_detail("user-unknown")

        assert result["data"] == {}
        assert result["metadata"]["snapshot"]["snapshot_id"] == "snap-1"

    @pytest.mark.asyncio
    async def test_returns_enriched_detail_payload_for_entry(self, handler, mock_repo):
        snapshot = _make_snapshot(comparison_snapshot_id="snap-0")
        entry = _make_entry(
            dimensions=[{"id": "d1", "label": "D1", "score": 0.5, "weight": 0.2, "components": []}],
            projects=["proj-a"],
            summary_metrics={"total_spend": 10},
        )
        comparison_entry = _make_entry(snapshot_id="snap-0", rank=3, total_score=70.0)
        mock_repo.get_latest_snapshot_by_type = AsyncMock(return_value=snapshot)
        mock_repo.get_entry_by_user = AsyncMock(side_effect=[entry, comparison_entry])

        with patch(f"{_HANDLER_MODULE}.get_async_session", side_effect=_fake_session):
            with (
                patch(f"{_FM_MODULE}.get_dimension_metadata", return_value={"name": "Usage", "components": {}}),
                patch(f"{_FM_MODULE}.get_tier_by_name", return_value={"label": "Pioneer", "color": "#fff"}),
                patch(f"{_FM_MODULE}.get_intent_by_id", return_value={"label": "Hybrid", "emoji": "H"}),
            ):
                result = await handler.get_leaderboard_user_detail("user-1")

        assert result["data"]["user_id"] == "user-1"
        assert result["data"]["comparison"]["previous_rank"] == 3
        assert result["data"]["tier"]["label"] == "Pioneer"
        assert result["data"]["intent"]["label"] == "Hybrid"
        assert result["data"]["dimensions"][0]["name"] == "Usage"


class TestLeaderboardDistributionEndpoints:
    @pytest.mark.asyncio
    async def test_tier_distribution_formats_rows(self, handler, mock_repo):
        snapshot = _make_snapshot()
        mock_repo.get_latest_snapshot_by_type = AsyncMock(return_value=snapshot)
        mock_repo.get_tier_distribution = AsyncMock(
            return_value=[
                {"tier_name": "expert", "tier_level": 4, "user_count": 2},
                {"tier_name": "advanced", "tier_level": 3, "user_count": 3},
            ]
        )

        with patch(f"{_FM_MODULE}.get_tier_by_name", side_effect=lambda name: {"color": f"color-{name}"}):
            result = await handler.get_leaderboard_tier_distribution()

        assert result["data"]["rows"][0]["percentage"] == 40.0
        assert result["data"]["rows"][1]["percentage"] == 60.0
        assert result["data"]["rows"][0]["color"] == "color-expert"

    @pytest.mark.asyncio
    async def test_score_distribution_returns_bins(self, handler, mock_repo):
        snapshot = _make_snapshot()
        mock_repo.get_latest_snapshot_by_type = AsyncMock(return_value=snapshot)
        mock_repo.get_score_distribution = AsyncMock(return_value=[{"range": "10-20", "count": 4}])

        result = await handler.get_leaderboard_score_distribution()

        assert result["data"]["rows"] == [{"range": "10-20", "count": 4}]

    @pytest.mark.asyncio
    async def test_dimension_breakdown_returns_all_dimensions(self, handler, mock_repo):
        snapshot = _make_snapshot()
        mock_repo.get_latest_snapshot_by_type = AsyncMock(return_value=snapshot)
        mock_repo.get_dimension_averages = AsyncMock(return_value={"d1": 0.75})

        result = await handler.get_leaderboard_dimension_breakdown()

        assert any(row["dimension_id"] == "d1" and row["avg_score"] == 0.75 for row in result["data"]["rows"])

    @pytest.mark.asyncio
    async def test_top_performers_formats_comparison_rows(self, handler, mock_repo):
        snapshot = _make_snapshot(comparison_snapshot_id="snap-0")
        entry = _make_entry(rank=1, total_score=80.0, dimensions=[{"id": "d1", "score": 0.4}], summary_metrics={"a": 1})
        comparison = _make_entry(rank=2, total_score=70.0)
        mock_repo.get_latest_snapshot_by_type = AsyncMock(return_value=snapshot)
        mock_repo.get_top_entries = AsyncMock(return_value=[entry])
        mock_repo.get_entries_by_users = AsyncMock(return_value={"user-1": comparison})

        result = await handler.get_leaderboard_top_performers(limit=1)

        row = result["data"]["rows"][0]
        assert row["previous_rank"] == 2
        assert row["score_delta"] == 10.0
        assert row["dimensions"] == [{"id": "d1", "score": 0.4}]


class TestSnapshotListingEndpoints:
    @pytest.mark.asyncio
    async def test_get_leaderboard_snapshots_returns_snapshot_rows(self, handler, mock_repo):
        snapshot = _make_snapshot()
        mock_repo.list_snapshots = AsyncMock(return_value=([snapshot], 1))

        result = await handler.get_leaderboard_snapshots(view="current", page=0, per_page=10)

        assert result["data"]["columns"]
        assert result["data"]["rows"][0]["id"] == "snap-1"
        assert result["pagination"]["total_count"] == 1

    @pytest.mark.asyncio
    async def test_get_leaderboard_seasons_raises_for_invalid_view(self, handler):
        with pytest.raises(Exception, match="Invalid leaderboard view"):
            await handler.get_leaderboard_seasons("bad-view")

    @pytest.mark.asyncio
    async def test_get_leaderboard_seasons_returns_rows(self, handler, mock_repo):
        snapshot = _make_snapshot(season_key="2026-03")
        mock_repo.list_snapshots = AsyncMock(return_value=([snapshot], 1))

        result = await handler.get_leaderboard_seasons("monthly")

        assert result["data"]["columns"]
        assert result["data"]["rows"][0]["snapshot_id"] == "snap-1"
        assert result["pagination"]["total_count"] == 1


class TestFrameworkAndHelperMethods:
    def test_get_framework_metadata_wraps_data(self, handler):
        with patch(f"{_FM_MODULE}.get_framework_metadata", return_value={"tiers": []}):
            result = handler.get_framework_metadata()

        assert result["data"] == {"tiers": []}
        assert "metadata" in result

    @pytest.mark.asyncio
    async def test_get_comparison_entries_returns_empty_without_comparison_snapshot(self, handler):
        result = await handler._get_comparison_entries(AsyncMock(), _make_snapshot(comparison_snapshot_id=None), ["u1"])

        assert result == {}

    @pytest.mark.asyncio
    async def test_get_comparison_entries_fetches_entries_when_snapshot_present(self, handler, mock_repo):
        mock_repo.get_entries_by_users = AsyncMock(return_value={"u1": _make_entry()})

        result = await handler._get_comparison_entries(
            AsyncMock(), _make_snapshot(comparison_snapshot_id="snap-0"), ["u1"]
        )

        assert "u1" in result
        mock_repo.get_entries_by_users.assert_awaited_once()

    def test_comparison_payload_and_row_helpers(self):
        entry = _make_entry()
        previous = _make_entry(snapshot_id="snap-0", rank=3, total_score=80.0)

        payload = LeaderboardHandler._comparison_payload(entry, previous)
        snapshot_row = LeaderboardHandler._snapshot_to_row(_make_snapshot())
        season_row = LeaderboardHandler._season_to_row(_make_snapshot(season_key="2026-03"))
        empty = LeaderboardHandler._empty_tabular(0.0, filters={"view": "current"})

        assert payload["comparison_snapshot_id"] == "snap-0"
        assert snapshot_row["id"] == "snap-1"
        assert season_row["snapshot_id"] == "snap-1"
        assert empty["data"] == {"columns": [], "rows": []}

    @pytest.mark.asyncio
    async def test_run_computation_creates_service_and_returns_snapshot_id(self):
        fake_service = AsyncMock()
        fake_service.compute_for_view.return_value = "snap-123"

        @asynccontextmanager
        async def fake_session():
            yield AsyncMock()

        with (
            patch(f"{_HANDLER_MODULE}.get_async_session", side_effect=fake_session),
            patch("codemie.service.leaderboard.leaderboard_service.LeaderboardService", return_value=fake_service),
            patch("codemie.repository.metrics_elastic_repository.MetricsElasticRepository"),
        ):
            result = await LeaderboardHandler._run_computation(30, "current", None)

        assert result == "snap-123"
        fake_service.compute_for_view.assert_awaited_once_with(view="current", period_days=30, season_key=None)

    def test_computation_done_callback_logs_success_and_error(self):
        success_task = MagicMock()
        success_task.exception.return_value = None
        success_task.result.return_value = "snap-1"

        error_task = MagicMock()
        error_task.exception.return_value = RuntimeError("boom")

        with patch(f"{_HANDLER_MODULE}.logger") as mock_logger:
            LeaderboardHandler._computation_done_callback(success_task)
            LeaderboardHandler._computation_done_callback(error_task)

        mock_logger.info.assert_called_once()
        mock_logger.error.assert_called_once()


# ---------------------------------------------------------------------------
# trigger_computation
# ---------------------------------------------------------------------------


class TestTriggerComputation:
    @pytest.mark.asyncio
    async def test_rejects_when_already_running(self, handler, mock_repo):
        """Should raise an exception if a computation is already running."""
        running_snap = _make_snapshot(status="running")
        mock_repo.get_latest_snapshot = AsyncMock(return_value=running_snap)

        with (
            patch(f"{_HANDLER_MODULE}.get_async_session", side_effect=_fake_session),
            pytest.raises(Exception, match="already running"),
        ):
            await handler.trigger_computation()


# ---------------------------------------------------------------------------
# Static / class-method helpers
# ---------------------------------------------------------------------------


class TestEntryToRow:
    def test_builds_row_with_dimension_scores_and_comparison(self):
        entry = _make_entry(rank=2, total_score=75.0)
        comparison = _make_entry(rank=5, total_score=60.0)

        with patch(
            f"{_FM_MODULE}.get_intent_by_id",
            return_value={"label": "Hybrid", "emoji": ""},
        ):
            row = LeaderboardHandler._entry_to_row(entry, comparison)

        assert row["rank"] == 2
        assert row["previous_rank"] == 5
        assert row["rank_delta"] == 3  # improved from 5 to 2
        assert row["score_delta"] == 15.0
        assert row["d1_score"] == 90.0  # 0.9 * 100
        assert row["d5_score"] == 70.0  # 0.7 * 100
        assert row["total_spend"] == 42.5

    def test_builds_row_without_comparison(self):
        entry = _make_entry()

        with patch(
            f"{_FM_MODULE}.get_intent_by_id",
            return_value={"label": "Hybrid", "emoji": ""},
        ):
            row = LeaderboardHandler._entry_to_row(entry, None)

        assert row["previous_rank"] is None
        assert row["rank_delta"] is None
        assert row["score_delta"] is None


class TestRankDelta:
    def test_positive_delta_when_rank_improved(self):
        assert LeaderboardHandler._rank_delta(2, 5) == 3

    def test_none_when_no_previous_rank(self):
        assert LeaderboardHandler._rank_delta(1, None) is None

    def test_negative_delta_when_rank_dropped(self):
        assert LeaderboardHandler._rank_delta(5, 2) == -3


class TestScoreDelta:
    def test_positive_delta(self):
        assert LeaderboardHandler._score_delta(80.0, 70.0) == 10.0

    def test_none_when_no_previous_score(self):
        assert LeaderboardHandler._score_delta(80.0, None) is None

    def test_rounds_to_two_decimals(self):
        # 80.555 - 70.3 = 10.255, rounded to 10.26
        assert LeaderboardHandler._score_delta(80.555, 70.3) == 10.26


class TestEnrichDimensions:
    def test_adds_metadata_and_component_details(self):
        raw = [
            {
                "id": "d1",
                "label": "D1",
                "score": 0.85,
                "weight": 0.2,
                "components": [{"key": "chat_count", "value": 10, "score": 0.8}],
            }
        ]

        def fake_get_dim_meta(dim_id):
            if dim_id == "d1":
                return {
                    "name": "Core Platform Usage",
                    "label": "D1",
                    "weight": 0.2,
                    "color": "#6366f1",
                    "icon": "chart",
                    "description": "Measures core usage",
                    "components": {
                        "chat_count": {
                            "what": "Number of chats",
                            "calc": "count(chats)",
                            "evidence": "Based on chat logs",
                        }
                    },
                }
            return {}

        enriched = LeaderboardHandler._enrich_dimensions(raw, fake_get_dim_meta)

        assert len(enriched) == 1
        dim = enriched[0]
        assert dim["id"] == "d1"
        assert dim["color"] == "#6366f1"
        assert dim["description"] == "Measures core usage"
        assert dim["score"] == 0.85

        comp = dim["components"][0]
        assert comp["what"] == "Number of chats"
        assert comp["calc"] == "count(chats)"
        assert comp["key"] == "chat_count"

    def test_handles_empty_dimensions(self):
        result = LeaderboardHandler._enrich_dimensions([], lambda _: {})
        assert result == []
