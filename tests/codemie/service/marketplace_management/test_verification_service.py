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

"""Unit tests for validator.engine.assign_bucket + related helpers.

Covers the tier × core × polish matrix. Pure-function tests — no engine run.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from codemie.service.marketplace_management.validator.config import VerificationConfig
from codemie.service.marketplace_management.validator.engine import (
    Engine,
    _adoption_tier,
    _core_gate_fails,
    _keep_bucket_detail,
    _polish_gate_fails,
    _review_detail,
    aggregate_verdict,
    assign_bucket,
)
from codemie.service.marketplace_management.validator.models import (
    Bucket,
    CleanupReason,
    GateResult,
    Severity,
    SimilarityPair,
    SimilarityResult,
    UsageTier,
    Verdict,
    VerdictKind,
)

# NOTE: `Bucket` is imported from `validator.models`, NOT from
# `marketplace_management.models`. Those are two SEPARATE enum classes with
# identical members (validator/models.py:39 vs models.py:36) — they compare
# equal by string value but `is` comparisons across them always fail.
# `validator.engine.assign_bucket` returns the `validator.models` one, and the
# deleted `verification_service.py` copy returned the other. That mismatch was
# the only real drift between the two modules. See the report for F-67-drift.


# ---------------------------------------------------------------------------
# Adoption tier
# ---------------------------------------------------------------------------


class TestAdoptionTier:
    def test_hot_at_threshold(self, cfg):
        assert _adoption_tier(cfg.verification.hot_min_users, cfg) == "HOT"

    def test_warm_between_thresholds(self, cfg):
        assert _adoption_tier(cfg.verification.warm_min_users, cfg) == "WARM"

    def test_cold_at_floor(self, cfg):
        assert _adoption_tier(cfg.verification.cold_min_users, cfg) == "COLD"

    def test_dead_below_cold_floor(self, cfg):
        assert _adoption_tier(cfg.verification.cold_min_users - 1, cfg) == "DEAD"

    def test_dead_zero(self, cfg):
        assert _adoption_tier(0, cfg) == "DEAD"


# ---------------------------------------------------------------------------
# Core-gate failures
# ---------------------------------------------------------------------------


class TestCoreGateFails:
    def test_baseline_passes(self, assistant, cfg):
        assert _core_gate_fails(assistant, cfg) == []

    def test_short_description(self, make, cfg):
        a = make(description="short")
        fails = _core_gate_fails(a, cfg)
        assert any(f.startswith("desc<") for f in fails)

    def test_short_prompt(self, make, cfg):
        a = make(system_prompt="too short")
        fails = _core_gate_fails(a, cfg)
        assert any(f.startswith("prompt<") for f in fails)

    def test_empty_shell_no_substance_short_prompt(self, make, cfg):
        a = make(tools=[], mcp_servers=[], skills=[], datasources=[], system_prompt="x" * 400)
        assert "empty_shell" in _core_gate_fails(a, cfg)

    def test_pure_prompt_chatbot_ok_when_long(self, make, cfg):
        a = make(
            tools=[],
            mcp_servers=[],
            skills=[],
            datasources=[],
            system_prompt="x" * (cfg.verification.core_gates.substance_prompt_alt_min + 1),
        )
        assert _core_gate_fails(a, cfg) == []


# ---------------------------------------------------------------------------
# Polish-gate failures
# ---------------------------------------------------------------------------


class TestPolishGateFails:
    def test_baseline_missing_cats(self, assistant, cfg):
        # baseline has 1 category, polish requires 2
        fails = _polish_gate_fails(assistant, cfg)
        assert "cats<2" in fails or any(f.startswith("cats<") for f in fails)

    def test_two_categories_ok(self, make, cfg):
        a = make(
            categories=["a", "b"],
            conversation_starters=["hi", "hello"],
            description="x" * 300,  # exceed polish desc_min=250
        )
        assert _polish_gate_fails(a, cfg) == []

    def test_missing_icon_and_starters(self, make, cfg):
        a = make(icon_url=None, conversation_starters=[], categories=["a", "b"])
        fails = _polish_gate_fails(a, cfg)
        assert "no_icon" in fails
        assert any(f.startswith("starters<") for f in fails)


# ---------------------------------------------------------------------------
# assign_bucket — the main decision matrix
# ---------------------------------------------------------------------------


class TestAssignBucketLegacyCleanup:
    def test_invalid_maps_to_delete(self, assistant, cfg):
        v = Verdict(kind=VerdictKind.CANDIDATE_FOR_CLEANUP, reason=CleanupReason.INVALID)
        b, r = assign_bucket(assistant, v, cfg)
        assert b is Bucket.DELETE and r == "invalid_metadata"

    def test_dead_reason_maps_to_delete(self, assistant, cfg):
        v = Verdict(kind=VerdictKind.CANDIDATE_FOR_CLEANUP, reason=CleanupReason.DEAD)
        b, r = assign_bucket(assistant, v, cfg)
        assert b is Bucket.DELETE and r == "dead_zero_signal"


class TestAssignBucketTiers:
    def test_hot_with_thin_metadata_still_verified(self, make, cfg):
        # HOT bypasses polish gates.
        a = make(unique_users_count=200, categories=["general"], icon_url=None, conversation_starters=[])
        v = Verdict(kind=VerdictKind.KEEP)
        b, r = assign_bucket(a, v, cfg)
        assert b is Bucket.VERIFIED and r == "hot_adoption"

    def test_hot_but_core_fails_goes_to_community(self, make, cfg):
        a = make(unique_users_count=200, description="short")
        v = Verdict(kind=VerdictKind.KEEP)
        b, r = assign_bucket(a, v, cfg)
        assert b is Bucket.COMMUNITY and r.startswith("hot_but_thin")

    def test_warm_qualified_verified(self, make, cfg):
        a = make(unique_users_count=25, categories=["a", "b"], conversation_starters=["hi", "hello"])
        v = Verdict(kind=VerdictKind.KEEP)
        b, _ = assign_bucket(a, v, cfg)
        assert b is Bucket.VERIFIED

    def test_warm_multiple_polish_gaps_goes_community(self, make, cfg):
        a = make(unique_users_count=25, categories=["a"], icon_url=None, conversation_starters=[])
        v = Verdict(kind=VerdictKind.KEEP)
        b, _ = assign_bucket(a, v, cfg)
        assert b is Bucket.COMMUNITY

    def test_cold_full_polish_verified(self, make, cfg):
        a = make(
            unique_users_count=3, categories=["a", "b"], description="x" * 300, conversation_starters=["hi", "hello"]
        )
        v = Verdict(kind=VerdictKind.KEEP)
        b, _ = assign_bucket(a, v, cfg)
        assert b is Bucket.VERIFIED

    def test_cold_missing_polish_community(self, make, cfg):
        a = make(unique_users_count=3, categories=["a"])
        v = Verdict(kind=VerdictKind.KEEP)
        b, _ = assign_bucket(a, v, cfg)
        assert b is Bucket.COMMUNITY

    def test_dead_new_stays_in_community(self, make, cfg):
        # DEAD tier + recent age → COMMUNITY (grace window inside COMMUNITY).
        recent = "2026-08-01T00:00:00Z"
        a = make(unique_users_count=0, updated_date=recent, created_date=recent)
        v = Verdict(kind=VerdictKind.KEEP)
        b, r = assign_bucket(a, v, cfg)
        assert b is Bucket.COMMUNITY and r == "dead_new"

    def test_dead_stale_gets_delete(self, make, cfg):
        a = make(unique_users_count=0, updated_date="2024-01-01T00:00:00Z", created_date="2024-01-01T00:00:00Z")
        v = Verdict(kind=VerdictKind.CANDIDATE_FOR_CLEANUP, reason=CleanupReason.DEAD)
        b, r = assign_bucket(a, v, cfg)
        assert b is Bucket.DELETE and r == "dead_zero_signal"

    def test_grace_fail_cold_at_floor_stale_is_deleted(self, make, cfg):
        # COLD (at the cold_min_users floor), older than grace_days → past its
        # grace period with no further adoption, so it now clears for Delete
        # instead of lingering in Community. The reason string stays
        # 'grace_fail' (unchanged) — the frontend's BARE_SLUG_EXPLANATION
        # tooltip keys off this exact slug.
        a = make(
            unique_users_count=cfg.verification.cold_min_users,
            updated_date="2025-01-01T00:00:00Z",
            categories=["a", "b"],
            conversation_starters=["hi", "hello"],
        )
        v = Verdict(kind=VerdictKind.KEEP)
        b, r = assign_bucket(a, v, cfg)
        assert b is Bucket.DELETE and r == "grace_fail"

    def test_below_cold_floor_stale_stays_community_not_grace_fail(self, make, cfg):
        # Below cold_min_users, assign_bucket alone always returns DEAD's
        # 'dead_new' regardless of staleness — the DELETE-for-stale-DEAD path
        # only fires when the caller has already set
        # VerdictKind.CANDIDATE_FOR_CLEANUP/CleanupReason.DEAD via
        # aggregate_verdict (see test_dead_stale_gets_delete). A bare KEEP
        # verdict below the floor must not fall through to grace_fail.
        a = make(
            unique_users_count=cfg.verification.cold_min_users - 1,
            updated_date="2025-01-01T00:00:00Z",
            categories=["a", "b"],
            conversation_starters=["hi", "hello"],
        )
        v = Verdict(kind=VerdictKind.KEEP)
        b, r = assign_bucket(a, v, cfg)
        assert b is Bucket.COMMUNITY and r == "dead_new"


def _days_ago(days: int) -> str:
    """An ISO timestamp `days` before now. Fixed dates age: a literal that meant "10 days old" when it was
    written crossed the 30-day archive threshold and flipped the verdict."""
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


class TestDeadZeroSignalConsistency:
    """Cross-function invariant: Reason and Decision always agree for DEAD-tier."""

    def _gate(self, assistant_id: str) -> GateResult:
        return GateResult(assistant_id=assistant_id)

    def _sim(self, assistant_id: str) -> SimilarityResult:
        return SimilarityResult(assistant_id=assistant_id)

    def test_in_corridor_gets_candidate_for_cleanup_and_dead_zero_signal(self, make, cfg):
        # 200 days old — well past archive_dead_after_days (30), within 365 days.
        a = make(
            unique_users_count=0,
            updated_date=_days_ago(200),
            created_date=_days_ago(200),
        )
        verdict = aggregate_verdict(
            a,
            self._gate(a.id),
            self._sim(a.id),
            UsageTier.DEAD,
            cfg.verification.archive_dead_after_days,
        )
        assert verdict.kind is VerdictKind.CANDIDATE_FOR_CLEANUP
        assert verdict.reason is CleanupReason.DEAD

        bucket, reason = assign_bucket(a, verdict, cfg)
        assert bucket is Bucket.DELETE
        assert reason == "dead_zero_signal"

    def test_below_threshold_stays_community(self, make, cfg):
        # ~10 days old — below archive_dead_after_days (30).
        a = make(
            unique_users_count=0,
            updated_date=_days_ago(10),
            created_date=_days_ago(10),
        )
        verdict = aggregate_verdict(
            a,
            self._gate(a.id),
            self._sim(a.id),
            UsageTier.DEAD,
            cfg.verification.archive_dead_after_days,
        )
        # Rule 5 must not fire for a young DEAD assistant.
        assert not (verdict.kind is VerdictKind.CANDIDATE_FOR_CLEANUP and verdict.reason is CleanupReason.DEAD)

        bucket, reason = assign_bucket(a, verdict, cfg)
        assert bucket is Bucket.COMMUNITY
        assert reason == "dead_new"

    def test_dead_updated_date_none_uses_created_date_fallback(self, make, cfg):
        # updated_date=None, created_date ~200 days ago.
        # Validates that _age_days(record.updated_date or record.created_date)
        # falls back to created_date when updated_date is absent, so rule 5 still fires.
        a = make(
            unique_users_count=0,
            updated_date=None,
            created_date=_days_ago(200),
        )
        verdict = aggregate_verdict(
            a,
            self._gate(a.id),
            self._sim(a.id),
            UsageTier.DEAD,
            cfg.verification.archive_dead_after_days,
        )
        assert verdict.kind is VerdictKind.CANDIDATE_FOR_CLEANUP
        assert verdict.reason is CleanupReason.DEAD

        bucket, reason = assign_bucket(a, verdict, cfg)
        assert bucket is Bucket.DELETE
        assert reason == "dead_zero_signal"


class TestArchiveDeadAfterDaysGuard:
    """CR-001: archive_dead_after_days must be positive — engine and config both guard it."""

    def _gate(self, assistant_id: str) -> GateResult:
        return GateResult(assistant_id=assistant_id)

    def _sim(self, assistant_id: str) -> SimilarityResult:
        return SimilarityResult(assistant_id=assistant_id)

    def test_zero_raises_in_aggregate_verdict(self, make):
        a = make(unique_users_count=0, updated_date="2024-01-01T00:00:00Z")
        with pytest.raises(ValueError, match="archive_dead_after_days"):
            aggregate_verdict(a, self._gate(a.id), self._sim(a.id), UsageTier.DEAD, 0)

    def test_negative_raises_in_aggregate_verdict(self, make):
        a = make(unique_users_count=0, updated_date="2024-01-01T00:00:00Z")
        with pytest.raises(ValueError, match="archive_dead_after_days"):
            aggregate_verdict(a, self._gate(a.id), self._sim(a.id), UsageTier.DEAD, -1)

    def test_zero_raises_in_verification_config(self):
        with pytest.raises(ValueError, match="archive_dead_after_days"):
            VerificationConfig(archive_dead_after_days=0)

    def test_negative_raises_in_verification_config(self):
        with pytest.raises(ValueError, match="archive_dead_after_days"):
            VerificationConfig(archive_dead_after_days=-10)

    def test_positive_is_accepted(self):
        cfg = VerificationConfig(archive_dead_after_days=1)
        assert cfg.archive_dead_after_days == 1


class TestLookupByIdNoneGuard:
    """CR-002: aggregate_verdict must not crash with AttributeError when lookup_by_id=None."""

    def _gate(self, assistant_id: str) -> GateResult:
        return GateResult(assistant_id=assistant_id)

    def test_critical_pair_with_no_lookup_does_not_crash(self, make):
        """Rule 1: CRITICAL similarity pair + lookup_by_id=None must not raise AttributeError."""
        a = make(unique_users_count=0, updated_date="2024-01-01T00:00:00Z")
        critical_pair = SimilarityPair(
            a_id=a.id,
            b_id="other-id",
            signals={"name": 1.0},
            combined=0.95,
            severity=Severity.CRITICAL,
        )
        sim = SimilarityResult(assistant_id=a.id, pairs=[critical_pair])
        # Must not raise — lookup_by_id defaults to None.
        verdict = aggregate_verdict(a, self._gate(a.id), sim, UsageTier.DEAD, 90)
        # Without a lookup dict, Rule 1 cannot find 'other', so it falls through.
        assert verdict is not None


class TestDuplicateTiebreak:
    """Rule 1: when two low-usage near-duplicates are tied on users, break the
    tie by likes, then by most-recent activity, then by id — so tied pairs
    stop sitting in REVIEW forever with neither side ever winning.
    """

    def _gate(self, assistant_id: str) -> GateResult:
        return GateResult(assistant_id=assistant_id)

    def _critical_sim(self, assistant_id: str, other_id: str) -> SimilarityResult:
        pair = SimilarityPair(
            a_id=assistant_id,
            b_id=other_id,
            signals={"name": 1.0},
            combined=0.9,
            severity=Severity.CRITICAL,
        )
        return SimilarityResult(assistant_id=assistant_id, pairs=[pair])

    def test_equal_users_more_likes_wins(self, make):
        record = make(id="dup-a", unique_users_count=3, unique_likes_count=1)
        other = make(id="dup-b", unique_users_count=3, unique_likes_count=5)
        lookup = {record.id: record, other.id: other}

        verdict = aggregate_verdict(
            record,
            self._gate(record.id),
            self._critical_sim(record.id, other.id),
            UsageTier.COLD,
            90,
            lookup_by_id=lookup,
        )

        assert verdict.kind is VerdictKind.CANDIDATE_FOR_CLEANUP
        assert verdict.reason is CleanupReason.DUPLICATE
        assert verdict.canonical_id == other.id
        assert verdict.detail == f"Near-duplicate of {other.name}."

    def test_equal_users_and_likes_more_recent_update_wins(self, make):
        record = make(
            id="dup-a",
            unique_users_count=3,
            unique_likes_count=2,
            updated_date="2026-01-01T00:00:00Z",
        )
        other = make(
            id="dup-b",
            unique_users_count=3,
            unique_likes_count=2,
            updated_date="2026-06-01T00:00:00Z",
        )
        lookup = {record.id: record, other.id: other}

        verdict = aggregate_verdict(
            record,
            self._gate(record.id),
            self._critical_sim(record.id, other.id),
            UsageTier.COLD,
            90,
            lookup_by_id=lookup,
        )

        assert verdict.kind is VerdictKind.CANDIDATE_FOR_CLEANUP
        assert verdict.canonical_id == other.id

    def test_equal_users_and_likes_missing_updated_date_falls_back_to_created(self, make):
        record = make(
            id="dup-a",
            unique_users_count=3,
            unique_likes_count=2,
            updated_date=None,
            created_date="2026-01-01T00:00:00Z",
        )
        other = make(
            id="dup-b",
            unique_users_count=3,
            unique_likes_count=2,
            updated_date=None,
            created_date="2026-06-01T00:00:00Z",
        )
        lookup = {record.id: record, other.id: other}

        verdict = aggregate_verdict(
            record,
            self._gate(record.id),
            self._critical_sim(record.id, other.id),
            UsageTier.COLD,
            90,
            lookup_by_id=lookup,
        )

        assert verdict.kind is VerdictKind.CANDIDATE_FOR_CLEANUP
        assert verdict.canonical_id == other.id

    def test_full_tie_breaks_by_id(self, make):
        record = make(id="dup-b", unique_users_count=3, unique_likes_count=2)
        other = make(id="dup-a", unique_users_count=3, unique_likes_count=2)
        lookup = {record.id: record, other.id: other}

        verdict = aggregate_verdict(
            record,
            self._gate(record.id),
            self._critical_sim(record.id, other.id),
            UsageTier.COLD,
            90,
            lookup_by_id=lookup,
        )

        # "dup-a" < "dup-b" lexicographically, so dup-a (the other side) wins.
        assert verdict.kind is VerdictKind.CANDIDATE_FOR_CLEANUP
        assert verdict.canonical_id == other.id

    def test_canonical_side_of_a_tie_stays_in_review(self, make):
        """The winner of the tiebreak must not also flag itself for cleanup."""
        record = make(id="dup-a", unique_users_count=3, unique_likes_count=5)
        other = make(id="dup-b", unique_users_count=3, unique_likes_count=1)
        lookup = {record.id: record, other.id: other}

        verdict = aggregate_verdict(
            record,
            self._gate(record.id),
            self._critical_sim(record.id, other.id),
            UsageTier.COLD,
            90,
            lookup_by_id=lookup,
        )

        assert verdict.kind is not VerdictKind.CANDIDATE_FOR_CLEANUP

    def test_strictly_more_users_still_wins_unchanged(self, make):
        """Regression: the pre-existing non-tied path keeps working."""
        record = make(id="dup-a", unique_users_count=3, unique_likes_count=100)
        other = make(id="dup-b", unique_users_count=10, unique_likes_count=0)
        lookup = {record.id: record, other.id: other}

        verdict = aggregate_verdict(
            record,
            self._gate(record.id),
            self._critical_sim(record.id, other.id),
            UsageTier.COLD,
            90,
            lookup_by_id=lookup,
        )

        assert verdict.kind is VerdictKind.CANDIDATE_FOR_CLEANUP
        assert verdict.canonical_id == other.id


class TestDuplicateScansAllCriticalMatches:
    """Rule 1 must not stop at the single closest match.

    A record's closest match (`top_pair()`) is often an equally low-usage
    sibling that isn't itself canonical — that shouldn't hide a further,
    genuinely-more-canonical match further down the (score-sorted) pairs
    list. Reproduces the real gap: a Cold assistant's nearest neighbour is
    its Cold twin, but it is *also* a critical-severity match of a Warm
    original — the twin relationship must not block the real one.
    """

    def _gate(self, assistant_id: str) -> GateResult:
        return GateResult(assistant_id=assistant_id)

    def _pair(self, assistant_id: str, other_id: str, combined: float, severity: Severity) -> SimilarityPair:
        return SimilarityPair(
            a_id=assistant_id,
            b_id=other_id,
            signals={"name": 1.0},
            combined=combined,
            severity=severity,
        )

    def test_more_canonical_match_is_found_past_the_closest_one(self, make):
        record = make(id="honorarios", unique_users_count=3, unique_likes_count=0)
        sibling = make(id="varejo", unique_users_count=3, unique_likes_count=0)
        canonical = make(id="canal-aa", unique_users_count=15, unique_likes_count=0)
        lookup = {r.id: r for r in (record, sibling, canonical)}

        # Sorted by score descending, exactly as run_all_similarity produces:
        # the sibling is the CLOSEST match but is not more canonical (tied,
        # and record wins the tiebreak by id: "honorarios" < "varejo").
        # canonical is a weaker but still CRITICAL match further down.
        sim = SimilarityResult(
            assistant_id=record.id,
            pairs=[
                self._pair(record.id, sibling.id, 0.95, Severity.CRITICAL),
                self._pair(record.id, canonical.id, 0.70, Severity.CRITICAL),
            ],
        )

        verdict = aggregate_verdict(record, self._gate(record.id), sim, UsageTier.COLD, 90, lookup_by_id=lookup)

        assert verdict.kind is VerdictKind.CANDIDATE_FOR_CLEANUP
        assert verdict.reason is CleanupReason.DUPLICATE
        assert verdict.canonical_id == canonical.id
        assert verdict.match_score == pytest.approx(0.70)

    def test_still_picks_the_closest_match_when_it_is_already_canonical(self, make):
        record = make(id="dup-a", unique_users_count=3, unique_likes_count=0)
        canonical = make(id="dup-b", unique_users_count=15, unique_likes_count=0)
        lookup = {r.id: r for r in (record, canonical)}

        sim = SimilarityResult(
            assistant_id=record.id,
            pairs=[self._pair(record.id, canonical.id, 0.9, Severity.CRITICAL)],
        )

        verdict = aggregate_verdict(record, self._gate(record.id), sim, UsageTier.COLD, 90, lookup_by_id=lookup)

        assert verdict.kind is VerdictKind.CANDIDATE_FOR_CLEANUP
        assert verdict.canonical_id == canonical.id

    def test_non_critical_match_does_not_count_even_if_more_canonical(self, make):
        record = make(id="dup-a", unique_users_count=3, unique_likes_count=0)
        sibling = make(id="dup-b", unique_users_count=3, unique_likes_count=0)
        bigger_but_optional = make(id="dup-c", unique_users_count=50, unique_likes_count=0)
        lookup = {r.id: r for r in (record, sibling, bigger_but_optional)}

        sim = SimilarityResult(
            assistant_id=record.id,
            pairs=[
                self._pair(record.id, sibling.id, 0.95, Severity.CRITICAL),
                self._pair(record.id, bigger_but_optional.id, 0.80, Severity.OPTIONAL),
            ],
        )

        verdict = aggregate_verdict(record, self._gate(record.id), sim, UsageTier.COLD, 90, lookup_by_id=lookup)

        assert verdict.kind is not VerdictKind.CANDIDATE_FOR_CLEANUP

    def test_picks_the_most_canonical_among_several_qualifying_matches(self, make):
        record = make(id="dup-a", unique_users_count=3, unique_likes_count=0)
        okay_canonical = make(id="dup-b", unique_users_count=10, unique_likes_count=0)
        best_canonical = make(id="dup-c", unique_users_count=20, unique_likes_count=0)
        lookup = {r.id: r for r in (record, okay_canonical, best_canonical)}

        # The weaker canonical scores HIGHER (and would be top_pair()), but
        # the stronger one must still win the canonical selection.
        sim = SimilarityResult(
            assistant_id=record.id,
            pairs=[
                self._pair(record.id, okay_canonical.id, 0.9, Severity.CRITICAL),
                self._pair(record.id, best_canonical.id, 0.75, Severity.CRITICAL),
            ],
        )

        verdict = aggregate_verdict(record, self._gate(record.id), sim, UsageTier.COLD, 90, lookup_by_id=lookup)

        assert verdict.kind is VerdictKind.CANDIDATE_FOR_CLEANUP
        assert verdict.canonical_id == best_canonical.id


class TestDeadBothDatesAbsent:
    """CR-003: DEAD assistant with both dates absent must not escape archiving silently."""

    def _gate(self, assistant_id: str) -> GateResult:
        return GateResult(assistant_id=assistant_id)

    def _sim(self, assistant_id: str) -> SimilarityResult:
        return SimilarityResult(assistant_id=assistant_id)

    def test_both_dates_none_emits_candidate_for_cleanup(self, make, cfg):
        a = make(unique_users_count=0, updated_date=None, created_date=None)
        verdict = aggregate_verdict(
            a,
            self._gate(a.id),
            self._sim(a.id),
            UsageTier.DEAD,
            cfg.verification.archive_dead_after_days,
        )
        assert verdict.kind is VerdictKind.CANDIDATE_FOR_CLEANUP
        assert verdict.reason is CleanupReason.DEAD

    def test_both_dates_none_bucket_is_delete_dead_zero_signal(self, make, cfg):
        a = make(unique_users_count=0, updated_date=None, created_date=None)
        verdict = aggregate_verdict(
            a,
            self._gate(a.id),
            self._sim(a.id),
            UsageTier.DEAD,
            cfg.verification.archive_dead_after_days,
        )
        bucket, reason = assign_bucket(a, verdict, cfg)
        assert bucket is Bucket.DELETE
        assert reason == "dead_zero_signal"


class TestReviewDetail:
    """Unit tests for _review_detail — the human-readable REVIEW verdict explanation."""

    def _gate(self, assistant_id: str = "a1", *, critical: int = 0, optional: int = 0) -> GateResult:
        from codemie.service.marketplace_management.validator.models import Finding

        findings = [Finding(rule="r", severity=Severity.CRITICAL, message="c") for _ in range(critical)] + [
            Finding(rule="r", severity=Severity.OPTIONAL, message="o") for _ in range(optional)
        ]
        return GateResult(assistant_id=assistant_id, findings=findings)

    def _sim(self, combined: float, severity: Severity = Severity.OPTIONAL):
        return SimilarityPair(a_id="a1", b_id="a2", signals={}, combined=combined, severity=severity)

    def test_critical_findings_only(self):
        detail = _review_detail(self._gate(critical=2), None, UsageTier.COLD)
        assert detail == "Has 2 critical quality issues."

    def test_critical_singular(self):
        detail = _review_detail(self._gate(critical=1), None, UsageTier.COLD)
        assert detail == "Has 1 critical quality issue."

    def test_optional_findings_only(self):
        detail = _review_detail(self._gate(optional=3), None, UsageTier.COLD)
        assert detail == "Has 3 optional quality issues."

    def test_similarity_match_present_with_findings(self):
        detail = _review_detail(self._gate(critical=2), self._sim(0.81), UsageTier.COLD)
        assert detail == "Has 2 critical quality issues and is 81% similar to another assistant."

    def test_similarity_match_present_no_findings(self):
        detail = _review_detail(self._gate(), self._sim(0.75), UsageTier.COLD)
        assert detail == "Is 75% similar to another assistant."

    def test_grandfathered_hot(self):
        detail = _review_detail(self._gate(critical=2), self._sim(0.81), UsageTier.HOT)
        assert detail == (
            "Has 2 critical quality issues and is 81% similar to another assistant; "
            "flagged for review rather than cleanup because it's actively used (Hot tier)."
        )

    def test_grandfathered_warm(self):
        detail = _review_detail(self._gate(optional=1), None, UsageTier.WARM)
        assert detail == (
            "Has 1 optional quality issue; flagged for review rather than cleanup because"
            " it's actively used (Warm tier)."
        )

    def test_no_bits_fallback(self):
        # No findings, no similarity — unreachable in practice but the fallback must be readable.
        detail = _review_detail(self._gate(), None, UsageTier.COLD)
        assert detail == "Needs review."

    def test_combined_critical_and_optional(self):
        detail = _review_detail(self._gate(critical=1, optional=2), None, UsageTier.COLD)
        assert detail == "Has 1 critical and 2 optional quality issues."


class TestKeepBucketDetail:
    """Keep+non-Verified gets a bucket-aware detail string; Keep+Verified stays plain."""

    def _gate(self, assistant_id: str = "a1") -> GateResult:
        return GateResult(assistant_id=assistant_id)

    def _sim(self, assistant_id: str = "a1") -> SimilarityResult:
        return SimilarityResult(assistant_id=assistant_id)

    def test_keep_community_polish_gap_sets_detail(self, make, cfg):
        """Keep + Community via polish gap produces the expected plain-English detail."""
        # categories=["a"] (1 cat), icon_url=None, conversation_starters=[] →
        # 3 polish gaps → WARM goes to COMMUNITY, but passes core gates → KEEP.
        a = make(
            unique_users_count=25,
            categories=["a"],
            icon_url=None,
            conversation_starters=[],
        )
        v = aggregate_verdict(a, self._gate(a.id), self._sim(a.id), UsageTier.WARM, 90)
        assert v.kind is VerdictKind.KEEP
        bucket, reason = assign_bucket(a, v, cfg)
        assert bucket is Bucket.COMMUNITY
        detail = _keep_bucket_detail(reason)
        assert "Passed core quality checks" in detail
        assert "polish" in detail

    def test_keep_verified_detail_unchanged(self, make, cfg):
        """Keep + Verified must not be overwritten — detail stays as the plain KEEP text."""
        a = make(
            unique_users_count=50,
            description="A" * 300,
            system_prompt="B" * 300,
            categories=["cat1", "cat2"],
            updated_date="2026-09-01T00:00:00Z",
        )
        v = aggregate_verdict(a, self._gate(a.id), self._sim(a.id), UsageTier.WARM, 90)
        assert v.kind is VerdictKind.KEEP
        bucket, _ = assign_bucket(a, v, cfg)
        if bucket is Bucket.VERIFIED:
            assert v.detail == ""

    def test_predicate_labels_in_detail(self):
        """Predicate labels translate correctly in _keep_bucket_detail."""
        detail = _keep_bucket_detail("warm_polish_gaps:no_icon,starters<2")
        assert "no icon set" in detail
        assert "fewer than 2 conversation starters" in detail
        assert "Passed core quality checks" in detail

    def test_bare_slug_no_colon(self):
        """A bare non-surplus bucket reason without a colon stays readable."""
        detail = _keep_bucket_detail("cold_full_polish")
        assert "Passed core quality checks" in detail
        assert "cold full polish" in detail

    def test_dead_new_detail_is_not_jargon(self):
        """dead_new must not read as '...not fully qualified (dead new).' — that
        parenthetical is jargon; explain the actual reason (the Dead grace period
        window) instead."""
        detail = _keep_bucket_detail("dead_new")
        assert "(dead new)" not in detail
        assert "(Dead grace period)" in detail

    def test_grace_fail_detail_is_not_jargon(self):
        """grace_fail must not read as '...not fully qualified (grace fail).' —
        explain the actual reason (past the Cold grace period, no further adoption)."""
        detail = _keep_bucket_detail("grace_fail")
        assert "(grace fail)" not in detail
        assert "(Cold grace period)" in detail

    def test_review_bucket_detail_no_existing_text(self):
        """REVIEW verdict with empty detail (edge case) falls back to clause alone."""
        clause = _keep_bucket_detail("warm_polish_gaps:desc<250")
        # Empty detail path in decide()
        combined = clause
        assert combined == clause
        assert "Passed core quality checks" in combined

    def test_review_bucket_detail_join_strips_trailing_sentence_punctuation(self):
        """A sentence-ending detail (e.g. '...another assistant.') must not collide
        with the '; ' join separator into a stray '.;' — F-<this ticket>."""
        existing = "Is 81% similar to another assistant."
        clause = _keep_bucket_detail("warm_polish_gaps:no_icon")
        combined = f"{existing.rstrip('.;, ')}; {clause}" if existing else clause
        assert ".;" not in combined
        assert (
            combined
            == "Is 81% similar to another assistant; Passed core quality checks, but missing polish for full qualification — no icon set."
        )


class TestConfirmedCanonicalCollapse:
    """`Engine.decide()`'s reverse-index step: a REVIEW verdict driven only by
    a similarity match must not re-flag a pairing that a low-usage sibling's
    own CANDIDATE_FOR_CLEANUP verdict already resolved by naming this record
    canonical (`canonical_id`).
    """

    def _decide(self, cfg, records, gate_results, similarity_results, usage_tiers):
        return Engine(cfg).decide(
            records,
            gate_results=gate_results,
            similarity_results=similarity_results,
            usage_tiers=usage_tiers,
            purpose_neighbors={},
        )

    def test_collapses_to_keep_when_the_top_match_is_the_resolved_duplicate(self, make, cfg):
        winner = make(
            id="ci-winner",
            name="Client Intelligence",
            unique_users_count=25,
            categories=["a", "b"],
            conversation_starters=["hi", "hello"],
        )
        loser = make(id="ci-redux", name="Client Intelligence Redux", unique_users_count=2)

        gate_results = {
            winner.id: GateResult(assistant_id=winner.id, findings=[]),
            loser.id: GateResult(assistant_id=loser.id, findings=[]),
        }
        similarity_results = {
            winner.id: SimilarityResult(
                winner.id,
                pairs=[
                    SimilarityPair(a_id=winner.id, b_id=loser.id, signals={}, combined=0.81, severity=Severity.OPTIONAL)
                ],
            ),
            loser.id: SimilarityResult(
                loser.id,
                pairs=[
                    SimilarityPair(a_id=loser.id, b_id=winner.id, signals={}, combined=0.95, severity=Severity.CRITICAL)
                ],
            ),
        }
        usage_tiers = {winner.id: UsageTier.WARM, loser.id: UsageTier.COLD}

        verdicts, _, _ = self._decide(cfg, [loser, winner], gate_results, similarity_results, usage_tiers)

        assert verdicts[loser.id].kind is VerdictKind.CANDIDATE_FOR_CLEANUP
        assert verdicts[loser.id].canonical_id == winner.id

        v = verdicts[winner.id]
        assert v.kind is VerdictKind.KEEP
        assert v.detail == (
            "Confirmed canonical for 1 duplicate already flagged for cleanup (Client Intelligence Redux)."
        )

    def test_appends_instead_of_replacing_when_the_top_match_is_someone_else(self, make, cfg):
        winner = make(
            id="ci-winner",
            name="Client Intelligence",
            unique_users_count=25,
            categories=["a", "b"],
            conversation_starters=["hi", "hello"],
        )
        loser = make(id="ci-redux", name="Client Intelligence Redux", unique_users_count=2)

        gate_results = {
            winner.id: GateResult(assistant_id=winner.id, findings=[]),
            loser.id: GateResult(assistant_id=loser.id, findings=[]),
        }
        similarity_results = {
            # Winner's own top pair is to a THIRD, unrelated assistant — not
            # the loser that already resolved to CANDIDATE_FOR_CLEANUP.
            winner.id: SimilarityResult(
                winner.id,
                pairs=[
                    SimilarityPair(
                        a_id=winner.id, b_id="unrelated-1", signals={}, combined=0.81, severity=Severity.OPTIONAL
                    )
                ],
            ),
            loser.id: SimilarityResult(
                loser.id,
                pairs=[
                    SimilarityPair(a_id=loser.id, b_id=winner.id, signals={}, combined=0.95, severity=Severity.CRITICAL)
                ],
            ),
        }
        usage_tiers = {winner.id: UsageTier.WARM, loser.id: UsageTier.COLD}

        verdicts, _, _ = self._decide(cfg, [loser, winner], gate_results, similarity_results, usage_tiers)

        v = verdicts[winner.id]
        assert v.kind is VerdictKind.REVIEW
        assert v.detail == (
            "Is 81% similar to another assistant; flagged for review rather than cleanup because"
            " it's actively used (Warm tier); already confirmed canonical for 1 duplicate flagged"
            " for cleanup (Client Intelligence Redux)."
        )


class TestNoDuplicateImplementationModule:
    """F-67: `verification_service.py` was a verbatim copy of the bucket-assignment
    half of `validator/engine.py`. Production only ever imports `validator.engine`,
    so the copy could drift silently while these tests kept passing against it.

    The copy must stay deleted — if it reappears, the tests above are testing dead
    code again.
    """

    def test_the_dead_copy_of_the_bucket_assignment_module_is_gone(self):
        import importlib.util

        assert importlib.util.find_spec("codemie.service.marketplace_management.verification_service") is None, (
            "codemie/src/codemie/service/marketplace_management/verification_service.py "
            "is back. It duplicates assign_bucket/"
            "_adoption_tier/_core_gate_fails/_polish_gate_fails from "
            "validator/engine.py, which is the copy production actually runs. "
            "Delete it and import from validator.engine instead."
        )
