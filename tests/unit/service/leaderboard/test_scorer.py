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

"""Unit tests for the leaderboard scoring engine."""

from __future__ import annotations

import pytest

from codemie.service.leaderboard.collector import RawUserMetrics
from codemie.service.leaderboard.config import LeaderboardSettings
from codemie.service.leaderboard.scorer import (
    DimensionScore,
    LeaderboardScorer,
    _percentile_rank,
    _tiered,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _make_user(
    user_id: str = "u1",
    user_name: str = "Test User",
    usage: dict | None = None,
    creation: dict | None = None,
    workflow_usage: dict | None = None,
    workflow_creation: dict | None = None,
    cli: dict | None = None,
    impact: dict | None = None,
    litellm_spend: dict | None = None,
) -> RawUserMetrics:
    """Create a minimal RawUserMetrics with only the fields needed."""
    return RawUserMetrics(
        user_id=user_id,
        user_name=user_name,
        usage=usage or {},
        creation=creation or {},
        workflow_usage=workflow_usage or {},
        workflow_creation=workflow_creation or {},
        cli=cli or {},
        impact=impact or {},
        litellm_spend=litellm_spend or {},
    )


@pytest.fixture
def settings() -> LeaderboardSettings:
    return LeaderboardSettings()


@pytest.fixture
def scorer(settings: LeaderboardSettings) -> LeaderboardScorer:
    return LeaderboardScorer(settings=settings)


# ── _tiered tests ────────────────────────────────────────────────────


class TestTiered:
    def test_value_below_all_thresholds_returns_zero(self):
        assert _tiered(0, [1, 3, 6, 10]) == 0.0

    def test_value_between_thresholds_returns_correct_bucket(self):
        # value=4 with thresholds [1,3,6,10]: bisect_right finds idx=2 -> 2/4 = 0.5
        assert _tiered(4, [1, 3, 6, 10]) == 0.5

    def test_value_above_all_thresholds_returns_one(self):
        assert _tiered(100, [1, 3, 6, 10]) == 1.0

    def test_value_exactly_on_threshold_advances_bucket(self):
        # value=3, thresholds [1,3,6,10]: bisect_right of 3 in [1,3,6,10] = idx 2 -> 0.5
        assert _tiered(3, [1, 3, 6, 10]) == 0.5

    def test_empty_thresholds_returns_zero(self):
        assert _tiered(5, []) == 0.0


# ── _percentile_rank tests ───────────────────────────────────────────


class TestPercentileRank:
    def test_empty_list_returns_zero(self):
        assert _percentile_rank(10.0, []) == 0.0

    def test_highest_value_returns_one(self):
        assert _percentile_rank(50.0, [10.0, 20.0, 30.0, 40.0, 50.0]) == 1.0

    def test_median_value_returns_expected_percentile(self):
        # value=30 in [10,20,30,40,50]: bisect_right -> idx 3 -> 3/5 = 0.6
        assert _percentile_rank(30.0, [10.0, 20.0, 30.0, 40.0, 50.0]) == 0.6


# ── score_all integration ───────────────────────────────────────────


class TestScoreAll:
    def test_filters_zero_score_users_and_assigns_ranks(self, scorer: LeaderboardScorer):
        """An active user gets ranked; a zero-activity user scored alone is filtered out."""
        # Score zero user alone so percentile ranking doesn't inflate from cohort peers
        zero_results = scorer.score_all([_make_user(user_id="zero", user_name="Zero User")])
        assert len(zero_results) == 0

        active_user = _make_user(
            user_id="active",
            user_name="Active User",
            usage={"active_days": 15, "web_conversations": 20, "avg_messages_per_conversation": 5.0},
            creation={"assistants_created": 3, "skills_created": 2, "datasources_created": 1},
        )
        active_results = scorer.score_all([active_user])
        assert len(active_results) == 1
        assert active_results[0].user_id == "active"
        assert active_results[0].rank == 1
        assert active_results[0].total_score > 0

    def test_results_sorted_descending_by_score(self, scorer: LeaderboardScorer):
        user_a = _make_user(
            user_id="a",
            user_name="A",
            usage={"active_days": 20, "web_conversations": 50, "avg_messages_per_conversation": 8.0},
            creation={"assistants_created": 10, "skills_created": 5, "datasources_created": 3},
            cli={"cli_sessions": 30, "cli_repos": 5, "cli_lines_added": 5000, "cli_files_changed": 100},
        )
        user_b = _make_user(
            user_id="b",
            user_name="B",
            usage={"active_days": 2, "web_conversations": 3},
        )

        results = scorer.score_all([user_b, user_a])

        assert len(results) == 2
        assert results[0].user_id == "a"
        assert results[1].user_id == "b"
        assert results[0].total_score > results[1].total_score
        assert results[0].rank == 1
        assert results[1].rank == 2


# ── _classify_intent ─────────────────────────────────────────────────


class TestClassifyIntent:
    @staticmethod
    def _dims(**scores: float) -> list[DimensionScore]:
        defaults = {"d1": 0.0, "d2": 0.0, "d3": 0.0, "d4": 0.0, "d5": 0.0, "d6": 0.0}
        defaults.update(scores)
        return [
            DimensionScore(id=dim_id, label=dim_id.upper(), weight=0.0, score=score)
            for dim_id, score in defaults.items()
        ]

    def test_explorer_when_all_scores_near_zero(self):
        dims = self._dims(d1=0.01, d2=0.0, d3=0.0, d4=0.0, d5=0.0, d6=0.0)
        assert LeaderboardScorer._classify_intent(dims) == "explorer"

    def test_developer_when_cli_dominant(self):
        dims = self._dims(d1=0.1, d2=0.1, d3=0.0, d4=0.0, d5=0.6, d6=0.0)
        assert LeaderboardScorer._classify_intent(dims) == "developer"

    def test_sdlc_unicorn_when_strong_across_multiple_dims(self):
        # Need >=3 of [d1,d2,d3,d5] >= 0.3, d5 >= 0.3, and (d2 >= 0.3 or d4 >= 0.3)
        dims = self._dims(d1=0.5, d2=0.4, d3=0.4, d4=0.0, d5=0.5, d6=0.1)
        assert LeaderboardScorer._classify_intent(dims) == "sdlc_unicorn"

    def test_platform_builder_when_creation_dominant(self):
        dims = self._dims(d1=0.2, d2=0.5, d3=0.0, d4=0.0, d5=0.1, d6=0.0)
        assert LeaderboardScorer._classify_intent(dims) == "platform_builder"

    def test_ai_user_when_usage_moderate(self):
        dims = self._dims(d1=0.25, d2=0.1, d3=0.0, d4=0.0, d5=0.1, d6=0.0)
        assert LeaderboardScorer._classify_intent(dims) == "ai_user"


# ── Dimension spot-checks ────────────────────────────────────────────


class TestDimensionScoring:
    def test_d1_active_days_and_conversations_contribute(self, scorer: LeaderboardScorer):
        """D1 with active_days=10 and conversations should produce non-trivial score."""
        user = _make_user(usage={"active_days": 10, "web_conversations": 15, "avg_messages_per_conversation": 4.0})
        cohort = scorer._build_cohort_stats([user])
        dim_def = scorer._settings.dimensions[0]  # d1

        result = scorer._score_d1(user, cohort, dim_def)

        assert result.id == "d1"
        assert result.weight == 0.20
        # active_days=10 in thresholds [1,3,6,10,15,20] => idx=4 => 4/6 ~0.667
        # This user is the only one so percentile rank for conversations = 1.0
        assert result.score > 0.5

    def test_d5_zero_cli_sessions_scores_zero(self, scorer: LeaderboardScorer):
        """D5 returns 0 when the user has no CLI sessions."""
        user = _make_user(cli={})
        cohort = scorer._build_cohort_stats([user])
        dim_def = scorer._settings.dimensions[4]  # d5

        result = scorer._score_d5(user, cohort, dim_def)

        assert result.id == "d5"
        assert result.score == 0.0


# ── _build_summary_metrics ───────────────────────────────────────────


class TestBuildSummaryMetrics:
    def test_maps_fields_correctly(self):
        user = _make_user(
            usage={"active_days": 5, "platform_active_days": 3, "web_conversations": 10, "assistants_used": 2},
            creation={"assistants_created": 1, "skills_created": 2, "datasources_created": 3},
            workflow_creation={"workflows_created": 4},
            workflow_usage={"workflow_executions": 7},
            cli={"cli_sessions": 8, "cli_repos": 2, "cli_lines_added": 500, "cli_files_changed": 20},
            impact={"shared_conversations": 3, "shared_conversation_access": 15, "kata_completed": 1},
            litellm_spend={"total_spend": 12.5, "cli_spend": 8.0},
        )

        summary = LeaderboardScorer._build_summary_metrics(user)

        assert summary["active_days"] == 5
        assert summary["web_conversations"] == 10
        assert summary["assistants_created"] == 1
        assert summary["workflows_created"] == 4
        assert summary["workflow_executions"] == 7
        assert summary["cli_sessions"] == 8
        assert summary["total_lines_added"] == 500
        assert summary["shared_conversations"] == 3
        assert summary["kata_completed"] == 1
        assert summary["total_spend"] == 12.5
        assert summary["cli_spend"] == 8.0
