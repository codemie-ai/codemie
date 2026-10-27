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

"""Core data models for the validator library."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Severity(str, Enum):
    """Finding severity per EPMCDME-14231."""

    CRITICAL = "CRITICAL"
    OPTIONAL = "OPTIONAL"


class VerdictKind(str, Enum):
    """Per-assistant triage verdict, aggregated by the engine."""

    KEEP = "KEEP"
    REVIEW = "REVIEW"
    CANDIDATE_FOR_CLEANUP = "CANDIDATE_FOR_CLEANUP"


class GateRule(str, Enum):
    """Every deterministic quality check `gates.py` can emit a Finding for.

    This is the vocabulary `GET /entities/{kind}/facets` publishes under
    `rules` and `GET /entities/{kind}/cleanup?rule=` accepts. It was an
    unconstrained `str` on both sides, so a stale bookmark or a typo returned
    `200 {"total": 0}` — indistinguishable from "nothing failed that check"
    (F-48).

    Kept in lockstep with `gates.py` by
    `test_marketplace_management_contract.py::test_gate_rule_enum_matches_the_gates`.
    """

    NAME_LENGTH = "name_length"
    NAME_NOT_BANNED = "name_not_banned"
    DESCRIPTION_LENGTH = "description_length"
    DESCRIPTION_NOT_EQUAL_NAME = "description_not_equal_name"
    SYSTEM_PROMPT_LENGTH = "system_prompt_length"
    AT_LEAST_ONE_CATEGORY = "at_least_one_category"
    SUBSTANCE = "substance"
    NO_INLINE_CREDENTIALS = "no_inline_credentials"
    CREDENTIAL_SCAN_TIMEOUT = "credential_scan_timeout"


class CleanupReason(str, Enum):
    DUPLICATE = "duplicate"  # weighted-score CRITICAL similarity
    PURPOSE_DUPLICATE = "purpose_duplicate"  # purpose-only cosine >= 0.85 to canonical
    INVALID = "invalid"  # >=2 CRITICAL gate failures
    DEAD = "dead"  # DEAD tier + stale


class UsageTier(str, Enum):
    HOT = "HOT"
    WARM = "WARM"
    COLD = "COLD"
    DEAD = "DEAD"


class Bucket(str, Enum):
    """Adoption-aware, tier-driven surface bucket. Orthogonal to VerdictKind.

    Three buckets — the sub-type of a delete (dead / semantic-clone / big-cluster
    surplus / invalid metadata / etc.) is carried in `Verdict.bucket_reason`.

    Ordering matches the display order in the Overview KPI row.
    """

    VERIFIED = "VERIFIED"  # default marketplace surface
    COMMUNITY = "COMMUNITY"  # visible in search / opt-in surface, not featured
    DELETE = "DELETE"  # remove from the marketplace (via merge, archive, or immediate delete)


@dataclass(frozen=True)
class Finding:
    """One validation finding — result of a gate or a similarity comparison."""

    rule: str
    severity: Severity
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "severity": self.severity.value,
            "message": self.message,
            "detail": self.detail,
        }


@dataclass
class GateResult:
    """Findings from running deterministic gates on one assistant."""

    assistant_id: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity is Severity.CRITICAL)

    @property
    def optional_count(self) -> int:
        return sum(1 for f in self.findings if f.severity is Severity.OPTIONAL)

    def rules_failed(self) -> list[str]:
        return [f.rule for f in self.findings]


@dataclass
class SimilarityPair:
    """Similarity between two assistants at the per-signal + combined level."""

    a_id: str
    b_id: str
    signals: dict[str, float]
    combined: float
    severity: Optional[Severity] = None  # set when >= optional threshold
    reason: Optional[str] = None  # e.g. "combined>=0.92" or "exact_name_match"

    def to_dict(self) -> dict[str, Any]:
        return {
            "a_id": self.a_id,
            "b_id": self.b_id,
            "signals": self.signals,
            "combined": round(self.combined, 4),
            "severity": self.severity.value if self.severity else None,
            "reason": self.reason,
        }


@dataclass
class SimilarityResult:
    """All similarity pairs for one assistant, sorted descending by combined score."""

    assistant_id: str
    pairs: list[SimilarityPair] = field(default_factory=list)

    def top_pair(self) -> Optional[SimilarityPair]:
        return self.pairs[0] if self.pairs else None


@dataclass
class Verdict:
    """Aggregated triage verdict for one assistant."""

    kind: VerdictKind
    reason: Optional[CleanupReason] = None
    detail: str = ""
    # Structured refs so the UI can render clickable jump-links.
    canonical_id: Optional[str] = None  # the "keep this instead" assistant, when the verdict points at one
    canonical_name: Optional[str] = None
    canonical_users: Optional[int] = None
    match_score: Optional[float] = None  # cosine or weighted score that triggered the verdict

    # Adoption-aware surface bucket — orthogonal to `kind`. Populated by
    # `engine.assign_bucket()` in a second pass after `aggregate_verdict()`.
    bucket: Optional[Bucket] = None
    bucket_reason: Optional[str] = None  # short slug e.g. "hot_adoption", "cold_broken"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "reason": self.reason.value if self.reason else None,
            "detail": self.detail,
            "canonical_id": self.canonical_id,
            "canonical_name": self.canonical_name,
            "canonical_users": self.canonical_users,
            "match_score": self.match_score,
            "bucket": self.bucket.value if self.bucket else None,
            "bucket_reason": self.bucket_reason,
        }


@dataclass
class AssistantRecord:
    """Slim, normalized assistant record used across validator + app.

    Kept flat and JSON-friendly on purpose so it survives the migration from
    the PoC's in-memory store into whatever CodeMie chooses for the real
    marketplace index.
    """

    id: str
    name: str
    description: str
    system_prompt: str
    categories: list[str]
    tools: list[str]  # resolved identifiers across toolkits
    mcp_servers: list[str]
    skills: list[str]
    datasources: list[str]  # extracted from context blocks
    icon_url: Optional[str]
    is_global: bool
    shared: bool
    creator: Optional[str]
    unique_users_count: int
    unique_likes_count: int
    unique_dislikes_count: int
    clone_count: int
    version_count: int
    updated_date: Optional[str]
    created_date: Optional[str]

    # Additional context signals surfaced in the detail view so reviewers
    # can decide "what is this thing?" without opening the raw JSON.
    author_name: Optional[str] = None  # from created_by.name — real human
    author_username: Optional[str] = None  # from created_by.username
    project: Optional[str] = None
    slug: Optional[str] = None
    llm_model_type: Optional[str] = None
    agent_mode: Optional[str] = None
    is_react: Optional[bool] = None
    conversation_starters: list[str] = field(default_factory=list)
    plan_prompt: Optional[str] = None
    nested_assistants: list[dict] = field(default_factory=list)
    prompt_variables: list[dict] = field(default_factory=list)

    raw: dict[str, Any] = field(default_factory=dict)  # original JSON for drill-down

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "creator": self.creator,
            "unique_users_count": self.unique_users_count,
            "unique_likes_count": self.unique_likes_count,
            "unique_dislikes_count": self.unique_dislikes_count,
            "clone_count": self.clone_count,
            "categories": self.categories,
            "updated_date": self.updated_date,
        }


@dataclass
class AuditResult:
    """Everything produced by one audit run over all assistants."""

    total: int
    gate_results: dict[str, GateResult]  # keyed by assistant id
    similarity_results: dict[str, SimilarityResult]  # keyed by assistant id
    verdicts: dict[str, Verdict]  # keyed by assistant id
    usage_tiers: dict[str, UsageTier]  # keyed by assistant id
    clusters: list[list[str]]  # duplicate clusters (>=0.80)
    per_rule_hits: dict[str, list[str]]  # rule -> [assistant_id, ...]
    took_seconds: float

    # Per-assistant purpose-nearest neighbours above threshold. The engine no
    # longer produces these -- the in-memory embedding matrix that fed them
    # never existed at runtime (F-71) and the code that built it is deleted.
    # `AuditCache.run_audit` fills this from the Elasticsearch KNN
    # augmentation after `Engine.audit()` returns.
    # Shape: {aid: [{other_id, cosine, other_users, other_name}, ...]}
    purpose_neighbors: dict[str, list[dict]] = field(default_factory=dict)

    # Purpose-cluster labels: {assistant_id: cluster_id}. -1 = singleton.
    # Populated when the engine runs the clustering step (default: yes).
    purpose_cluster_id: dict[str, int] = field(default_factory=dict)
    # Per-cluster canonical (highest-usage member id) + total members.
    cluster_canonical: dict[int, str] = field(default_factory=dict)
    cluster_size: dict[int, int] = field(default_factory=dict)

    # Adoption-aware bucket rollups derived from Verdict.bucket. Populated by
    # Engine.audit() after assign_bucket() runs. Buckets ordered per the enum.
    bucket_distribution: dict[str, int] = field(default_factory=dict)
    bucket_usage_retention: dict[str, float] = field(default_factory=dict)  # % of total user-touches per bucket
