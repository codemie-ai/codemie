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

"""Engine — orchestrates gates + similarity and produces AuditResult."""

from __future__ import annotations

import re
import time
from typing import Callable, Iterable, Optional

from .config import Config
from . import gates as gates_mod
from . import similarity as sim_mod
from .models import (
    AssistantRecord,
    AuditResult,
    Bucket,
    CleanupReason,
    GateResult,
    Severity,
    SimilarityPair,
    UsageTier,
    Verdict,
    VerdictKind,
)


def _now_iso_year_months_diff(iso: Optional[str]) -> Optional[float]:
    """Return how many months ago the iso date was (or None)."""
    if not iso:
        return None
    from datetime import datetime, timezone

    try:
        s = iso.rstrip("Z")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    now = datetime.now(timezone.utc)
    delta = now - dt
    return delta.days / 30.4375


def compute_usage_tiers(
    records: list[AssistantRecord],
    cfg: Config,
) -> dict[str, UsageTier]:
    tiers: dict[str, UsageTier] = {}
    for r in records:
        u = max(0, r.unique_users_count or 0)
        tiers[r.id] = UsageTier[_adoption_tier(u, cfg)]
    return tiers


def _is_more_canonical(a: AssistantRecord, b: AssistantRecord) -> bool:
    """True if `a` should be treated as the canonical side of a near-duplicate pair.

    Order: more users, then more likes, then more recently active (updated_date,
    falling back to created_date), then smaller id — the last step guarantees
    exactly one side wins even on a full tie, so a tied pair never leaves both
    sides stuck in REVIEW forever.
    """
    if a.unique_users_count != b.unique_users_count:
        return a.unique_users_count > b.unique_users_count
    if a.unique_likes_count != b.unique_likes_count:
        return a.unique_likes_count > b.unique_likes_count
    a_active = a.updated_date or a.created_date or ""
    b_active = b.updated_date or b.created_date or ""
    if a_active != b_active:
        return a_active > b_active
    return a.id < b.id


def _weighted_duplicate_verdict(
    record: AssistantRecord,
    similarity: sim_mod.SimilarityResult,
    lookup_by_id: dict[str, AssistantRecord] | None,
) -> Verdict | None:
    """Step 1: weighted-score CRITICAL duplicate.

    Scans every CRITICAL-severity match, not just the closest one (`top`). A
    record's closest match is often an equally low-usage sibling that isn't
    itself canonical, which would otherwise hide a further, genuinely-more-canonical
    match (e.g. a Cold record whose nearest neighbour is its Cold twin, but which
    is also a critical match of the Warm original the twin itself duplicates).
    """
    best_other: Optional[AssistantRecord] = None
    best_pair: Optional[SimilarityPair] = None
    for pair in similarity.pairs:
        if pair.severity is not Severity.CRITICAL:
            continue
        other = (lookup_by_id or {}).get(pair.b_id)
        if (
            other
            and _is_more_canonical(other, record)
            and (best_other is None or _is_more_canonical(other, best_other))
        ):
            best_other = other
            best_pair = pair
    if not (best_other and best_pair):
        return None
    return Verdict(
        kind=VerdictKind.CANDIDATE_FOR_CLEANUP,
        reason=CleanupReason.DUPLICATE,
        detail=f"Near-duplicate of {best_other.name}.",
        canonical_id=best_other.id,
        canonical_name=best_other.name,
        canonical_users=best_other.unique_users_count,
        match_score=round(best_pair.combined, 4),
    )


def _purpose_duplicate_verdict(
    record: AssistantRecord,
    purpose_neighbors: dict[str, list[dict]] | None,
    purpose_critical_threshold: float,
) -> Verdict | None:
    """Step 2: purpose-cosine near-duplicate of a much-higher-usage canonical.

    Threshold mirrors the "Critical threshold" similarity setting.
    """
    for pn in (purpose_neighbors or {}).get(record.id, []):
        if pn["cosine"] >= purpose_critical_threshold and pn["other_users"] >= 3 * max(1, record.unique_users_count):
            return Verdict(
                kind=VerdictKind.CANDIDATE_FOR_CLEANUP,
                reason=CleanupReason.PURPOSE_DUPLICATE,
                detail=(
                    f"Purpose-clone of {pn['other_name']} "
                    f"(cosine {pn['cosine']:.2f}, "
                    f"{pn['other_users']}u vs {record.unique_users_count}u)."
                ),
                canonical_id=pn["other_id"],
                canonical_name=pn["other_name"],
                canonical_users=pn["other_users"],
                match_score=round(pn["cosine"], 4),
            )
    return None


def _check_duplicate_verdict(
    record: AssistantRecord,
    similarity: sim_mod.SimilarityResult,
    lookup_by_id: dict[str, AssistantRecord] | None,
    purpose_neighbors: dict[str, list[dict]] | None,  # NOSONAR: S1172 - rule is switched off, see below
    purpose_critical_threshold: float,  # NOSONAR: S1172 - rule is switched off, see below
) -> Verdict | None:
    """Check duplicate rules (steps 1–2 of aggregate_verdict). Returns a Verdict or None."""
    # _purpose_duplicate_verdict is off on purpose; the function and its arguments stay for now.
    # cosine >= 0.70 flagged ~85% of COLD/DEAD assistants, mostly just relabelling low-usage
    # cleanups. To be recalibrated (add structural overlap, tune threshold).
    return _weighted_duplicate_verdict(record, similarity, lookup_by_id)


def aggregate_verdict(
    record: AssistantRecord,
    gate_result: GateResult,
    similarity: sim_mod.SimilarityResult,
    usage_tier: UsageTier,
    archive_dead_after_days: int,
    lookup_by_id: dict[str, AssistantRecord] | None = None,
    purpose_neighbors: dict[str, list[dict]] | None = None,
    purpose_critical_threshold: float = 0.85,
) -> Verdict:
    """Aggregate a per-assistant verdict.

    Grandfathering rule (per EPMCDME-14231): existing published assistants are
    NOT retroactively unpublished. So actively-used items (HOT/WARM) never
    auto-recommend as CLEANUP — they surface as REVIEW so an admin decides.
    CLEANUP recommendations are reserved for low-usage (COLD/DEAD) assistants.

    Priority (first-match-wins):
        1. Weighted-score CRITICAL duplicate  → CLEANUP:duplicate
        2. Purpose-cosine >=purpose_critical_threshold near-duplicate → CLEANUP:purpose_duplicate
        3. >=2 CRITICAL gate failures           → CLEANUP:invalid
        4. DEAD + stale (>archive_dead_after_days days) → CLEANUP:dead
        5. Any findings                         → REVIEW
        6. Otherwise                            → KEEP
    """
    if archive_dead_after_days <= 0:
        raise ValueError(f"archive_dead_after_days must be a positive integer; got {archive_dead_after_days}")
    top = similarity.top_pair()
    is_low_usage = usage_tier in (UsageTier.COLD, UsageTier.DEAD)

    if is_low_usage:
        dup_verdict = _check_duplicate_verdict(
            record,
            similarity,
            lookup_by_id,
            purpose_neighbors,
            purpose_critical_threshold,
        )
        if dup_verdict:
            return dup_verdict

    # 3. Multiple CRITICAL gate failures — CLEANUP only if COLD/DEAD.
    if gate_result.critical_count >= 2 and is_low_usage:
        return Verdict(
            kind=VerdictKind.CANDIDATE_FOR_CLEANUP,
            reason=CleanupReason.INVALID,
            detail=f"{gate_result.critical_count} critical quality issues (low usage).",
        )

    # 5. Dead + stale (beyond configurable archive threshold).
    # When both dates are absent the record age is unknowable; treat it as
    # infinitely old so zero-signal assistants with missing metadata are not
    # silently exempted from the cleanup policy.
    stale_days = _age_days(record.updated_date or record.created_date)
    if usage_tier is UsageTier.DEAD and (stale_days is None or stale_days > archive_dead_after_days):
        return _dead_stale_verdict(stale_days)

    # 6. Anything else with findings → REVIEW.
    if gate_result.findings or (top and top.severity in (Severity.OPTIONAL, Severity.CRITICAL)):
        return Verdict(
            kind=VerdictKind.REVIEW,
            detail=_review_detail(gate_result, top, usage_tier),
        )

    return Verdict(kind=VerdictKind.KEEP)


def _dead_stale_verdict(stale_days: Optional[int]) -> Verdict:
    detail = (
        "Zero/low usage and date metadata absent (treated as stale)."
        if stale_days is None
        else f"Zero/low usage and stale ({stale_days} days old)."
    )
    return Verdict(kind=VerdictKind.CANDIDATE_FOR_CLEANUP, reason=CleanupReason.DEAD, detail=detail)


def _quality_findings_phrase(critical: int, optional: int) -> Optional[str]:
    if critical and optional:
        n = critical + optional
        return f"Has {critical} critical and {optional} optional quality {'issue' if n == 1 else 'issues'}"
    if critical:
        return f"Has {critical} critical quality {'issue' if critical == 1 else 'issues'}"
    if optional:
        return f"Has {optional} optional quality {'issue' if optional == 1 else 'issues'}"
    return None


def _review_detail(gate_result: GateResult, top, usage_tier: UsageTier) -> str:
    findings = _quality_findings_phrase(gate_result.critical_count, gate_result.optional_count)

    if top and top.severity:
        pct = round(top.combined * 100)
        if findings:
            main = f"{findings} and is {pct}% similar to another assistant"
        else:
            main = f"Is {pct}% similar to another assistant"
    elif findings:
        main = findings
    else:
        return "Needs review."

    if usage_tier in (UsageTier.HOT, UsageTier.WARM):
        tier_label = usage_tier.value.capitalize()
        return f"{main}; flagged for review rather than cleanup because it's actively used ({tier_label} tier)."

    return f"{main}."


_PREDICATE_LABEL: dict[str, str] = {
    "no_icon": "no icon set",
    "empty_shell": "no tools/connections and a short prompt",
    "low_use": "very low usage",
}


_COLD_BROKEN_PREFIX = "cold_broken:"


def _predicate_label(predicate: str) -> str:
    m = re.match(r"^(desc|prompt|cats|starters)<(\d+)$", predicate)
    if m:
        field, n = m.groups()
        phrase = {
            "desc": f"description is under {n} characters",
            "prompt": f"system prompt is under {n} characters",
            "cats": f"fewer than {n} categories",
            "starters": f"fewer than {n} conversation starters",
        }
        return phrase[field]
    return _PREDICATE_LABEL.get(predicate, predicate.replace("_", " "))


def _keep_bucket_detail(
    bucket_reason: str,
    cold_min_users: int = 2,
    warm_min_users: int = 6,
    grace_days: int = 90,
    archive_dead_after_days: int = 30,
    age_days: Optional[int] = None,
) -> str:
    """Explain a bucket outcome other than VERIFIED. Called before `decide()`
    resolves the final verdict kind, so `bucket_reason == "cold_broken"` is
    reachable here even though it ends up as CANDIDATE_FOR_CLEANUP, not KEEP.
    """
    if bucket_reason == "dead_new":
        return (
            f"Passed core quality checks, but usage hasn't reached {cold_min_users} users"
            f" (Cold tier min.) yet — still inside the {archive_dead_after_days}-day"
            " (Dead grace period) window."
        )
    if bucket_reason == "grace_fail":
        return (
            f"Usage never grew past {warm_min_users} users (Warm tier min.)"
            f" during the {grace_days}-day (Cold grace period) — flagged for cleanup."
        )
    if bucket_reason.startswith(_COLD_BROKEN_PREFIX):
        predicates = bucket_reason[len(_COLD_BROKEN_PREFIX) :].split(",")
        labels = [_predicate_label(p) for p in predicates]
        n = len(predicates)
        return (
            f"{n} core gate failure{'s' if n != 1 else ''} — {', '.join(labels)}; low usage —"
            f" under {warm_min_users} users (Warm tier min.)."
        )
    if bucket_reason.startswith("hot_but_thin:") or bucket_reason.startswith("warm_broken:"):
        predicates = bucket_reason[bucket_reason.index(":") + 1 :].split(",")
        labels = [_predicate_label(p) for p in predicates]
        n = len(predicates)
        return f"{n} core gate failure{'s' if n != 1 else ''} — {', '.join(labels)}."
    colon_idx = bucket_reason.find(":")
    if colon_idx == -1:
        return f"Passed core quality checks; not fully qualified ({bucket_reason.replace('_', ' ')})."
    predicates = bucket_reason[colon_idx + 1 :].split(",")
    labels = [_predicate_label(p) for p in predicates]
    detail = f"Passed core quality checks, but missing polish for full qualification — {', '.join(labels)}."
    # `cold_thin` is the one Keep/Community outcome with a clock silently
    # running underneath it: assign_bucket's own grace check (engine.py, the
    # `age > grace_days` branch above `cold_thin`) is what stands between this
    # record and an automatic CANDIDATE_FOR_CLEANUP once the window closes, but
    # nothing in the polish-gap wording above says so. Surface the countdown
    # explicitly so "Keep" doesn't read as a permanent, uncontested status.
    if bucket_reason.startswith("cold_thin") and age_days is not None:
        remaining = max(0, grace_days - age_days)
        detail += (
            f" Still inside the {grace_days}-day (Cold grace period) window ({remaining} day"
            f"{'s' if remaining != 1 else ''} left) — will flip to a cleanup candidate"
            " on the next audit run if usage doesn't grow before then."
        )
    return detail


_QUALIFIED_BUCKET_DETAIL: dict[str, Callable[[int, int, int], str]] = {
    "hot_adoption": lambda hot, warm, cold: (
        f"Fully qualified — high adoption {hot}+ users (Hot tier), with all core checks passed."
    ),
    "warm_qualified": lambda hot, warm, cold: (
        f"Fully qualified — active usage {warm}–{hot} users (Warm tier), with at most one" " minor polish gap."
    ),
    "cold_full_polish": lambda hot, warm, cold: (
        f"Fully qualified — low usage {cold}–{warm} users (Cold tier), with all core and" " polish checks passed."
    ),
}


def _qualified_bucket_detail(
    bucket_reason: str,
    hot_min_users: int = 50,
    warm_min_users: int = 6,
    cold_min_users: int = 2,
    grace_days: int = 0,
    age_days: Optional[int] = None,
) -> Optional[str]:
    """Explain a KEEP verdict whose bucket is VERIFIED (Qualified).

    `_keep_bucket_detail` only covers outcomes other than VERIFIED (see its
    caller's `bucket is not Bucket.VERIFIED` guard), so a Qualified assistant
    surfaced no Decision-column tooltip at all — "Keep" looked identical
    whether or not there was anything to explain.
    """
    build = _QUALIFIED_BUCKET_DETAIL.get(bucket_reason)
    if not build:
        return None
    detail = build(hot_min_users, warm_min_users, cold_min_users)
    if bucket_reason == "cold_full_polish" and age_days is not None:
        remaining = max(0, grace_days - age_days)
        detail += (
            f" Still inside the {grace_days}-day (Cold grace period) window ({remaining} day"
            f"{'s' if remaining != 1 else ''} left) — will flip to a cleanup candidate"
            " on the next audit run if usage doesn't grow before then."
        )
    return detail


# ---------------------------------------------------------------------------
# Adoption-aware bucket assignment (verification layer)
# ---------------------------------------------------------------------------


def _adoption_tier(users: int, cfg: Config) -> str:
    """Absolute-count tier — separate from the percentile-based UsageTier.

    Returns one of: 'HOT', 'WARM', 'COLD', 'DEAD'. Fully adjustable via
    cfg.verification.hot_min_users / warm_min_users / cold_min_users:

        DEAD:  users <  cold_min_users
        COLD:  cold_min_users <= users <  warm_min_users
        WARM:  warm_min_users <= users <  hot_min_users
        HOT:   users >= hot_min_users
    """
    v = cfg.verification
    if users >= v.hot_min_users:
        return "HOT"
    if users >= v.warm_min_users:
        return "WARM"
    if users >= v.cold_min_users:
        return "COLD"
    return "DEAD"


def _age_days(iso: Optional[str]) -> Optional[int]:
    if not iso:
        return None
    from datetime import datetime, timezone

    try:
        s = iso.rstrip("Z")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - dt).days


def _core_gate_fails(record: AssistantRecord, cfg: Config) -> list[str]:
    """Return short slugs for core-gate failures. Empty list = passes core."""
    v = cfg.verification.core_gates
    fails: list[str] = []
    if len((record.description or "").strip()) < v.description_min:
        fails.append(f"desc<{v.description_min}")
    prompt_len = len((record.system_prompt or "").strip())
    if prompt_len < v.system_prompt_min:
        fails.append(f"prompt<{v.system_prompt_min}")
    has_substance = bool(record.tools or record.mcp_servers or record.skills or record.datasources)
    if not has_substance and prompt_len < v.substance_prompt_alt_min:
        fails.append("empty_shell")
    return fails


def _polish_gate_fails(record: AssistantRecord, cfg: Config) -> list[str]:
    """Return short slugs for polish-gate failures. Empty list = fully polished."""
    v = cfg.verification.polish_gates
    fails: list[str] = []
    if len((record.description or "").strip()) < v.description_min:
        fails.append(f"desc<{v.description_min}")
    if len(record.categories or []) < v.categories_min:
        fails.append(f"cats<{v.categories_min}")
    if v.require_icon and not (record.icon_url or ""):
        fails.append("no_icon")
    if len(record.conversation_starters or []) < v.conversation_starters_min:
        fails.append(f"starters<{v.conversation_starters_min}")
    return fails


_CLEANUP_BUCKET_REASONS: dict[CleanupReason, str] = {
    CleanupReason.INVALID: "invalid_metadata",
    CleanupReason.DEAD: "dead_zero_signal",
}


def _cleanup_bucket_reason(reason: Optional[CleanupReason]) -> str:
    """Sub-reason of a DELETE bucket for a legacy CLEANUP verdict."""
    if reason is None:
        return "cleanup"
    return _CLEANUP_BUCKET_REASONS.get(reason, reason.value)  # e.g. "duplicate"


def _core_broken_bucket(tier: str, core: list[str]) -> tuple[Bucket, str]:
    """Bucket for a live-tier record that fails the core gates."""
    joined = ",".join(core)
    if tier == "HOT":
        return Bucket.COMMUNITY, "hot_but_thin:" + joined
    if tier == "COLD":
        return Bucket.DELETE, _COLD_BROKEN_PREFIX + joined
    return Bucket.COMMUNITY, "warm_broken:" + joined


def assign_bucket(
    record: AssistantRecord,
    verdict: Verdict,
    cfg: Config,
) -> tuple[Bucket, str]:
    """Adoption-aware surface bucket. Pure function — no side effects.

    Three buckets only — VERIFIED / COMMUNITY / DELETE. The sub-type of a
    delete lives in `bucket_reason` (invalid_metadata,
    purpose_duplicate, duplicate, dead_zero_signal,
    cold_broken, grace_fail). Brand-new assistants below cold_min_users
    inside the grace window go to COMMUNITY, not DELETE — only `dead_new`
    gets that protection; a COLD assistant (at or above cold_min_users but
    below warm_min_users) that stops growing, past its own grace window,
    does not.

    Layered precedence (first match wins):
      1. Legacy CLEANUP verdicts (invalid / dup / purpose_dup / dead) → DELETE.
      2. DEAD tier (below cold_min_users) + stale beyond archive window → DELETE;
         still within window → COMMUNITY.
      3. Live tiers: apply core gates.
         - HOT with broken core stays in COMMUNITY (visible but not featured).
         - COLD with broken core → DELETE (broken shell, no adoption).
         - WARM with broken core → COMMUNITY.
      4. Passes core: HOT → VERIFIED; WARM → VERIFIED if ≤1 polish gap, else COMMUNITY;
         COLD → VERIFIED if fully polished + ≥2 users; DELETE if stale past the grace
         window (bucket_reason=grace_fail); else COMMUNITY.

    This is the only implementation. A duplicate copy once lived at
    marketplace_management/verification_service.py; it was deleted because
    nothing in production imported it and it returned the wrong `Bucket` enum.
    """
    users = record.unique_users_count or 0
    tier = _adoption_tier(users, cfg)
    age = _age_days(record.updated_date or record.created_date)

    # (1) Legacy CLEANUP verdicts → DELETE, sub-reason preserved.
    if verdict.kind is VerdictKind.CANDIDATE_FOR_CLEANUP:
        return Bucket.DELETE, _cleanup_bucket_reason(verdict.reason)

    # (2) DEAD tier — assistants below archive threshold stay in community.
    if tier == "DEAD":
        return Bucket.COMMUNITY, "dead_new"

    # Core-gate check
    core = _core_gate_fails(record, cfg)
    if core:
        return _core_broken_bucket(tier, core)

    return _passed_core_bucket(record, cfg, tier, users, age)


def _passed_core_bucket(
    record: AssistantRecord, cfg: Config, tier: str, users: int, age: Optional[int]
) -> tuple[Bucket, str]:
    """(4) Passes core — tier + polish."""
    polish = _polish_gate_fails(record, cfg)
    if tier == "HOT":
        return Bucket.VERIFIED, "hot_adoption"
    if tier == "WARM":
        if len(polish) <= 1:
            return Bucket.VERIFIED, "warm_qualified"
        return Bucket.COMMUNITY, "warm_polish_gaps:" + ",".join(polish)
    # COLD, and stale past the grace window — no further adoption came, so it
    # clears for Delete rather than lingering in Community indefinitely. Tier
    # already guarantees users >= cold_min_users here (DEAD absorbs anyone
    # below that floor), so there is no separate user-count guard to apply.
    # Reason string is unchanged ('grace_fail'): the frontend's
    # BARE_SLUG_EXPLANATION tooltip keys off this exact slug regardless of
    # which bucket it resolves to.
    if age is not None and age > cfg.verification.grace_days:
        return Bucket.DELETE, "grace_fail"
    if not polish and users >= 2:
        return Bucket.VERIFIED, "cold_full_polish"
    return Bucket.COMMUNITY, "cold_thin:" + (",".join(polish) if polish else "low_use")


def _canonical_reverse_index(
    records: list[AssistantRecord], verdicts: dict[str, Verdict]
) -> dict[str, list[AssistantRecord]]:
    """Who already named ME as their canonical (rules 1-2's `canonical_id`)?

    My own verdict doesn't know that decision happened.
    """
    canonical_of: dict[str, list[AssistantRecord]] = {}
    for r in records:
        v = verdicts[r.id]
        if v.kind is VerdictKind.CANDIDATE_FOR_CLEANUP and v.canonical_id:
            canonical_of.setdefault(v.canonical_id, []).append(r)
    return canonical_of


def _apply_canonical_note(v: Verdict, losers: list[AssistantRecord], top, *, has_findings: bool) -> None:
    """Already someone's confirmed canonical — collapse to KEEP if that's the
    only reason this record is REVIEW, else append it."""
    names = sorted(lo.name for lo in losers)
    n = len(names)
    plural = "s" if n != 1 else ""
    dupes = f"{n} duplicate{plural}"
    flagged = f"flagged for cleanup ({', '.join(names)})."
    loser_ids = {lo.id for lo in losers}
    if not has_findings and top and top.severity is not None and top.b_id in loser_ids:
        v.detail = f"Confirmed canonical for {dupes} already {flagged}"
        v.kind = VerdictKind.KEEP
    elif v.detail:
        v.detail = f"{v.detail.rstrip('.;, ')}; already confirmed canonical for {dupes} {flagged}"
    else:
        v.detail = f"Already confirmed canonical for {dupes} {flagged}"


class Engine:
    """Public entry point. Runs the full audit and returns an AuditResult."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def audit(
        self,
        records: list[AssistantRecord],
        clone_edges: "Iterable[tuple[str, str]] | None" = None,
    ) -> AuditResult:
        """Run gates, similarity, verdicts and bucketing over *records*.

        `clone_edges` are recorded (source_assistant_id, cloned_assistant_id)
        pairs from `assistant_clone_event`. They are recorded duplicate
        provenance, so they are passed in rather than inferred — the engine
        stays a pure function of its inputs and the database read lives in the
        adapter (see `AssistantAdapter.list_clone_edges`).
        """
        t0 = time.perf_counter()

        # Gates (single-record work)
        gate_results: dict[str, GateResult] = {}
        per_rule_hits: dict[str, list[str]] = {}
        for r in records:
            findings = gates_mod.run_all(r, self.cfg)
            gr = GateResult(assistant_id=r.id, findings=findings)
            gate_results[r.id] = gr
            for f in findings:
                per_rule_hits.setdefault(f.rule, []).append(r.id)

        # Similarity (pairwise): structural signals, exact name, clone edges.
        similarity_results, _ = sim_mod.run_all_similarity(records, self.cfg, clone_edges=clone_edges)

        # Purpose neighbours and purpose clusters used to be computed here from
        # an in-memory embedding matrix. No backend ever produced one, so the
        # whole block was dead (F-71) and is gone. The maps stay in the result
        # shape because `AuditCache.run_audit` fills `purpose_neighbors` from the
        # Elasticsearch KNN augmentation after the engine returns, and
        # `_build_clusters_from_neighbors` derives the cluster maps from those
        # edges. Empty here means "the engine did not infer these"; whether
        # anything fills them is the ES augmentation's business.
        #
        # And when it does fill them it must call `decide()` again — see its
        # docstring. `purpose_duplicate` is a function of `purpose_neighbors`, so a
        # verdict derived while it is empty can never carry it. The three cluster
        # maps only feed the Overview's Top clusters table and the cluster filter.
        purpose_neighbors: dict[str, list[dict]] = {}
        purpose_cluster_id: dict[str, int] = {}
        cluster_canonical: dict[int, str] = {}
        cluster_size: dict[int, int] = {}

        # Usage tiers
        usage_tiers = compute_usage_tiers(records, self.cfg)

        verdicts, bucket_distribution, bucket_usage_retention = self.decide(
            records,
            gate_results=gate_results,
            similarity_results=similarity_results,
            usage_tiers=usage_tiers,
            purpose_neighbors=purpose_neighbors,
        )

        # Clusters at OPTIONAL threshold
        clusters = sim_mod.build_clusters(similarity_results, self.cfg.similarity.thresholds.optional)

        return AuditResult(
            total=len(records),
            gate_results=gate_results,
            similarity_results=similarity_results,
            verdicts=verdicts,
            usage_tiers=usage_tiers,
            clusters=clusters,
            per_rule_hits=per_rule_hits,
            took_seconds=round(time.perf_counter() - t0, 2),
            bucket_distribution=bucket_distribution,
            bucket_usage_retention=bucket_usage_retention,
            purpose_neighbors=purpose_neighbors,
            purpose_cluster_id=purpose_cluster_id,
            cluster_canonical=cluster_canonical,
            cluster_size=cluster_size,
        )

    def decide(
        self,
        records: list[AssistantRecord],
        *,
        gate_results: dict[str, GateResult],
        similarity_results: dict,
        usage_tiers: dict[str, UsageTier],
        purpose_neighbors: dict[str, list[dict]],
    ) -> tuple[dict[str, Verdict], dict[str, int], dict[str, float]]:
        """Verdicts and buckets from evidence. Pure — safe to run more than once.

        Extracted from `audit()` so it can be run a **second** time, once the
        purpose maps actually contain something (`reaggregate`). Everything it
        reads is an argument and everything it writes is returned, so running it
        again on richer evidence produces the verdicts that evidence implies
        rather than a patch on top of the previous ones.
        """
        lookup = {r.id: r for r in records}

        verdicts: dict[str, Verdict] = {}
        for r in records:
            verdicts[r.id] = aggregate_verdict(
                r,
                gate_results[r.id],
                similarity_results.get(r.id, sim_mod.SimilarityResult(r.id, [])),
                usage_tiers.get(r.id, UsageTier.COLD),
                self.cfg.verification.archive_dead_after_days,
                lookup,
                purpose_neighbors=purpose_neighbors,
                purpose_critical_threshold=self.cfg.similarity.thresholds.critical,
            )

        canonical_of = _canonical_reverse_index(records, verdicts)

        # Bucket assignment — second pass, adoption-aware.
        bucket_distribution: dict[str, int] = {b.value: 0 for b in Bucket}
        bucket_usage_totals: dict[str, int] = {b.value: 0 for b in Bucket}
        total_users = 0
        for r in records:
            bucket = self._finalise_record(
                r,
                verdicts[r.id],
                canonical_of.get(r.id),
                top=similarity_results.get(r.id, sim_mod.SimilarityResult(r.id, [])).top_pair(),
                has_findings=bool(gate_results[r.id].findings),
            )
            bucket_distribution[bucket.value] += 1
            u = r.unique_users_count or 0
            bucket_usage_totals[bucket.value] += u
            total_users += u

        if total_users > 0:
            bucket_usage_retention = {k: round(100.0 * v / total_users, 2) for k, v in bucket_usage_totals.items()}
        else:
            bucket_usage_retention = {k: 0.0 for k in bucket_usage_totals}

        return verdicts, bucket_distribution, bucket_usage_retention

    def _finalise_record(
        self,
        r: AssistantRecord,
        v: Verdict,
        losers: Optional[list[AssistantRecord]],
        *,
        top,
        has_findings: bool,
    ) -> Bucket:
        """Assign the bucket and reconcile the verdict with it. Returns the bucket."""
        bucket, reason = assign_bucket(r, v, self.cfg)
        v.bucket = bucket
        v.bucket_reason = reason
        self._apply_bucket_detail(r, v, bucket, reason)
        # grace_fail and cold_broken mean the second, adoption-aware pass
        # independently decided this assistant belongs in the DELETE
        # bucket — that outweighs a first-pass KEEP/REVIEW verdict that
        # only looked at gates/similarity, so the surfaced decision must
        # agree with it.
        if reason == "grace_fail" or reason.startswith("cold_broken"):
            v.kind = VerdictKind.CANDIDATE_FOR_CLEANUP
        if losers and v.kind in (VerdictKind.KEEP, VerdictKind.REVIEW):
            _apply_canonical_note(v, losers, top, has_findings=has_findings)
        return bucket

    def _apply_bucket_detail(self, r: AssistantRecord, v: Verdict, bucket: Bucket, reason: str) -> None:
        """Set `v.detail` from the bucket outcome (the Decision-column tooltip)."""
        ver = self.cfg.verification
        age_days = _age_days(r.updated_date or r.created_date)
        if v.kind in (VerdictKind.KEEP, VerdictKind.REVIEW) and bucket is not Bucket.VERIFIED:
            clause = _keep_bucket_detail(
                reason,
                ver.cold_min_users,
                ver.warm_min_users,
                ver.grace_days,
                ver.archive_dead_after_days,
                age_days=age_days,
            )
            if v.kind is VerdictKind.KEEP:
                v.detail = clause
            else:
                v.detail = f"{v.detail.rstrip('.;, ')}; {clause}" if v.detail else clause
        elif v.kind is VerdictKind.KEEP and bucket is Bucket.VERIFIED:
            v.detail = _qualified_bucket_detail(
                reason,
                ver.hot_min_users,
                ver.warm_min_users,
                ver.cold_min_users,
                grace_days=ver.grace_days,
                age_days=age_days,
            )

    def reaggregate(self, result: AuditResult, records: list[AssistantRecord]) -> None:
        """Re-derive verdicts once the purpose evidence exists (13.2 item 4).

        `audit()` has to decide before the Elasticsearch KNN augmentation has
        run — the augmentation reads `result.verdicts` to know which records to
        embed — so the first pass necessarily sees empty purpose maps. Three
        outcome is a function of exactly that map:

            rule 2   CLEANUP:purpose_duplicate    <- purpose_neighbors

        so without this second pass no input of any shape could produce it,
        and the Cleanup page's `purpose_duplicate` filter would be guaranteed
        to return zero rows on every corpus. That is the same
        failure as F-71 — a consumer running before its producer — and not a
        statement about what this marketplace happens to contain.

        A no-op when the maps are empty: a run with `include_es_similarity` off,
        or one whose similarity backend was UNAVAILABLE, must produce exactly
        the verdicts it produced before. Re-running the same pure function over
        the same empty evidence is how that is guaranteed rather than intended.
        """
        if not result.purpose_neighbors:
            return

        verdicts, bucket_distribution, bucket_usage_retention = self.decide(
            records,
            gate_results=result.gate_results,
            similarity_results=result.similarity_results,
            usage_tiers=result.usage_tiers,
            purpose_neighbors=result.purpose_neighbors,
        )
        result.verdicts = verdicts
        result.bucket_distribution = bucket_distribution
        result.bucket_usage_retention = bucket_usage_retention
