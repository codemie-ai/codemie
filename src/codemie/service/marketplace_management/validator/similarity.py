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

"""Similarity scoring: structural signals, exact name, and recorded clones.

v1 similarity has exactly three mechanisms (decision D3b, recorded in
`docs/marketplace-audit-20260820/CHANGE-BRAINSTORM.md`):

**1. A weighted sum over six structural signals**, each normalized to [0, 1]:

    - name           Levenshtein ratio on normalized names
    - tools          Jaccard on tool identifiers
    - mcp_servers    Jaccard on MCP-server identifiers
    - skills         Jaccard on skill IDs
    - datasources    Jaccard on datasource identifiers
    - categories     Jaccard on category IDs

The weights sum to 1.0 (enforced by `SimilarityConfig.validate()`), so the
maximum attainable combined score is 1.0 and both thresholds are reachable.

**2. An exact normalized-name override** — two assistants with the same
normalized name are CRITICAL regardless of score.

**3. A recorded clone-edge override** — `assistant_clone_event` records that
someone created assistant B by cloning assistant A. That is not an
approximation of duplication, it is duplication with a receipt, so while the
pair's *current* structural score is still at or above the CRITICAL
threshold the edge pins the combined score to 1.0 and marks it CRITICAL. The
platform has recorded this all along and the detector ignored it (F-70).

The gate on current score matters: a clone the owner has since substantially
reworked is no longer a duplicate of its source, and permanently marking it
CRITICAL on provenance alone is a verdict no amount of rework can clear (MR
!4070 review comment #23). Once the structural score drops below the bar the
pair falls through to the ordinary threshold branches like any other.

WHAT IS NOT HERE, AND WHY
=========================
Three dense-vector text signals (`description`, `system_prompt`, `purpose`)
used to be scored here through a pluggable text backend. Every wired
implementation of that backend returned `None` from `fit_transform`, so all
three contributed exactly 0.0 to every pair in all 82 recorded audit runs —
while holding 0.45 of the weight budget. The highest score any pair could
reach was 0.65, below even the 0.70 OPTIONAL bar (F-13): the marketplace
advertised paraphrase detection that was arithmetically incapable of firing.

The signals, the backends, the numpy KNN shortlist and the sklearn
agglomerative clustering that consumed them are deleted rather than disabled
(F-71). Reinstating text similarity is a funded follow-up that needs a real
dense-vector backend, an index backfill, batched `msearch`, a cosine rescale
(ES `_score` is `(1+cos)/2`, F-28) and a threshold recalibration against
measurement — not a config flag.
"""

from __future__ import annotations

from typing import Iterable, Optional

from rapidfuzz import fuzz

from .config import Config
from .gates import normalize_name
from .models import (
    AssistantRecord,
    Severity,
    SimilarityPair,
    SimilarityResult,
)


# A clone edge as stored: (source_assistant_id, cloned_assistant_id).
CloneEdges = Iterable[tuple[str, str]]


# ---------------------------------------------------------------------------
# Set-based signals (Jaccard)
# ---------------------------------------------------------------------------


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    if union == 0:
        return 0.0
    return inter / union


# ---------------------------------------------------------------------------
# String-similarity signal for names
# ---------------------------------------------------------------------------


def name_similarity(a: str, b: str) -> float:
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    return fuzz.ratio(na, nb) / 100.0


# ---------------------------------------------------------------------------
# Clone edges
# ---------------------------------------------------------------------------


def normalize_clone_edges(edges: Optional[CloneEdges]) -> frozenset[tuple[str, str]]:
    """Collapse directed (source, clone) edges into order-independent keys.

    The stored edge has a direction — B was cloned from A — but similarity is
    symmetric, so both lookups must find it.
    """
    if not edges:
        return frozenset()
    return frozenset((a, b) if a < b else (b, a) for a, b in edges if a and b and a != b)


# ---------------------------------------------------------------------------
# Per-pair scoring
# ---------------------------------------------------------------------------


SIGNAL_KEYS = (
    "name",
    "tools",
    "mcp_servers",
    "skills",
    "datasources",
    "categories",
    "clone",
)

# Reason slugs written to `SimilarityPair.reason` and persisted on
# `mm_audit_similarity`. Kept as constants because the UI and the cleanup
# filters read them back.
REASON_CLONE_EDGE = "clone_edge"
REASON_EXACT_NAME = "exact_name_match"


def score_pair(
    a: AssistantRecord,
    b: AssistantRecord,
    cfg: Config,
    clone_edges: frozenset[tuple[str, str]] = frozenset(),
) -> SimilarityPair:
    weights = cfg.similarity.weights
    is_clone = ((a.id, b.id) if a.id < b.id else (b.id, a.id)) in clone_edges

    signals = {
        "name": name_similarity(a.name, b.name),
        "tools": jaccard(set(a.tools), set(b.tools)),
        "mcp_servers": jaccard(set(a.mcp_servers), set(b.mcp_servers)),
        "skills": jaccard(set(a.skills), set(b.skills)),
        "datasources": jaccard(set(a.datasources), set(b.datasources)),
        "categories": jaccard(set(a.categories), set(b.categories)),
        # Provenance, reported alongside the approximations but never blended
        # into them — see `combined` below.
        "clone": 1.0 if is_clone else 0.0,
    }

    combined = 0.0
    for k, v in signals.items():
        combined += v * weights.get(k, 0.0)

    exact_name = normalize_name(a.name) != "" and normalize_name(a.name) == normalize_name(b.name)

    severity: Optional[Severity] = None
    reason: Optional[str] = None

    if is_clone and combined >= cfg.similarity.thresholds.critical:
        # A recorded clone edge is evidence, not an estimate -- but only while
        # the clone's *current* content still backs it up. `combined` here is
        # the structural score computed above from live tools/mcp/skills/
        # datasources/categories/name (the `clone` signal itself carries no
        # configured weight, so it never contributes to `combined`). Pinning
        # to 1.0 regardless of that score used to mark every clone a critical
        # duplicate forever, even after the owner substantially reworked the
        # content (MR !4070 review comment #23) -- so the pin now only fires
        # when today's content is still at or above the critical bar; a
        # reworked clone falls through to the ordinary structural branches
        # below like any other pair.
        combined = 1.0
        severity = Severity.CRITICAL
        reason = REASON_CLONE_EDGE
    elif combined >= cfg.similarity.thresholds.critical or exact_name:
        severity = Severity.CRITICAL
        reason = (
            REASON_EXACT_NAME
            if exact_name and combined < cfg.similarity.thresholds.critical
            else f"combined>={cfg.similarity.thresholds.critical}"
        )
    elif combined >= cfg.similarity.thresholds.optional:
        severity = Severity.OPTIONAL
        reason = f"combined>={cfg.similarity.thresholds.optional}"

    return SimilarityPair(
        a_id=a.id,
        b_id=b.id,
        signals={k: round(v, 4) for k, v in signals.items()},
        combined=combined,
        severity=severity,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Bulk audit over all records
# ---------------------------------------------------------------------------


def run_all_similarity(
    records: list[AssistantRecord],
    cfg: Config,
    clone_edges: Optional[CloneEdges] = None,
) -> tuple[dict[str, SimilarityResult], None]:
    """Score every pair; retain pairs at or above the report threshold.

    Every pair is scored directly. There is no vector shortlist any more, and
    there is nothing to shortlist *for*: each pair costs six set intersections
    and one Levenshtein ratio, so the whole 219-record corpus resolves in well
    under a second — against the ~250s the per-record embedding round-trips
    used to cost (F-26). The previous code also fell back to all-pairs
    whenever the text backend produced no index, which was always, so this is
    the same asymptotic work with the numpy allocation removed.

    The second element of the return tuple is the purpose-embedding index the
    caller used to receive. It is permanently `None`; the tuple shape is kept
    so `Engine.audit()` and any external caller keep unpacking successfully.
    """
    if not records:
        return {}, None

    edges = normalize_clone_edges(clone_edges)
    ids = [r.id for r in records]
    threshold = min(cfg.similarity.thresholds.optional, cfg.similarity.report_threshold)

    pairs_by_id: dict[str, list[SimilarityPair]] = {rid: [] for rid in ids}

    for i in range(len(records)):
        a = records[i]
        for j in range(i + 1, len(records)):
            b = records[j]
            pair = score_pair(a, b, cfg, edges)
            if pair.combined < threshold and pair.severity is None:
                continue
            pairs_by_id[a.id].append(pair)
            pairs_by_id[b.id].append(
                SimilarityPair(
                    a_id=b.id,
                    b_id=a.id,
                    signals=pair.signals,
                    combined=pair.combined,
                    severity=pair.severity,
                    reason=pair.reason,
                )
            )

    results: dict[str, SimilarityResult] = {}
    for rid, pairs in pairs_by_id.items():
        pairs.sort(key=lambda p: p.combined, reverse=True)
        results[rid] = SimilarityResult(assistant_id=rid, pairs=pairs)
    return results, None


SIGNAL_DESCRIPTIONS = {
    "name": "name",
    "tools": "tool set",
    "mcp_servers": "MCP servers",
    "skills": "skill set",
    "datasources": "datasources",
    "categories": "categories",
    "clone": "clone history",
}


def _signal_phrase(label: str, score: float) -> str:
    if score >= 0.99:
        return f"identical {label} ({score:.2f})"
    if score >= 0.85:
        return f"near-identical {label} ({score:.2f})"
    if score >= 0.70:
        return f"strong {label} overlap ({score:.2f})"
    return f"partial {label} overlap ({score:.2f})"


def _canonical_hint(a: AssistantRecord, b: AssistantRecord) -> Optional[str]:
    """Name the more-used assistant as the likely canonical one, when the gap is large."""
    du, ou = a.unique_users_count, b.unique_users_count
    if du + ou <= 0:
        return None
    big, big_u, small_u = (a, du, ou) if du >= ou else (b, ou, du)
    if big_u > 0 and (small_u == 0 or big_u >= 3 * max(1, small_u)):
        return f"canonical looks like **{big.name}** ({big_u:,} users vs {small_u:,})"
    return None


def explain_pair(a: AssistantRecord, b: AssistantRecord, pair: SimilarityPair) -> str:
    """Compact human-readable reason: which signals dominated + usage delta."""
    parts: list[str] = []

    if pair.reason == REASON_CLONE_EDGE:
        parts.append("**one was cloned from the other**")

    exact_name = normalize_name(a.name) and normalize_name(a.name) == normalize_name(b.name)
    if exact_name:
        parts.append("**exact name match**")

    scored = {k: v for k, v in pair.signals.items() if k != "clone"}
    for signal, score in sorted(scored.items(), key=lambda kv: -(kv[1] or 0))[:3]:
        if score >= 0.5:
            parts.append(_signal_phrase(SIGNAL_DESCRIPTIONS.get(signal, signal), score))

    if not parts:
        parts.append(f"moderate multi-signal overlap (combined {pair.combined:.2f})")

    hint = _canonical_hint(a, b)
    if hint:
        parts.append(hint)

    return " · ".join(parts)


def build_clusters(
    similarity_results: dict[str, SimilarityResult],
    min_score: float,
) -> list[list[str]]:
    """Union-find over pairs with combined >= min_score.

    Reachable, unlike the vector clustering that was deleted alongside it: it
    consumes the structural pair scores computed above, which now actually
    reach the OPTIONAL threshold.
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    for res in similarity_results.values():
        for pair in res.pairs:
            if pair.combined >= min_score:
                parent.setdefault(pair.a_id, pair.a_id)
                parent.setdefault(pair.b_id, pair.b_id)
                union(pair.a_id, pair.b_id)

    clusters_map: dict[str, list[str]] = {}
    for k in parent:
        root = find(k)
        clusters_map.setdefault(root, []).append(k)

    return sorted(
        (c for c in clusters_map.values() if len(c) >= 2),
        key=lambda c: -len(c),
    )
