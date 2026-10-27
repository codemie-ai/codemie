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

"""Config loader for validator library.

Default thresholds, weights, and backend settings are now defined in code at:
  codemie.configs.marketplace_management.MarketplaceManagementConfig

Use Config.from_marketplace_management(mm_cfg) to build a Config from that
source.  The legacy load_config(path) / from_dict(raw) helpers are kept for
backward compatibility with tests that supply a raw dict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import re as _stdlib_re

import yaml

# The `regex` module is a declared direct dependency (pyproject.toml:118) chosen
# for exactly this purpose: it is the only engine available here that can bound a
# single match by wall-clock time. `re` cannot -- a catastrophic match is one C
# call, so no Python-level alarm can interrupt it, which is why the RED for this
# step could only be observed by killing a subprocess.
try:  # pragma: no cover - the dependency is declared; the guard is defensive
    import regex as _regex

    _REGEX_TIMEOUT_ERRORS: tuple[type[BaseException], ...] = (TimeoutError,)
except ImportError:  # pragma: no cover
    _regex = None  # type: ignore[assignment]
    _REGEX_TIMEOUT_ERRORS = ()


# ---------------------------------------------------------------------------
# Bounds on admin-supplied `credential_patterns` (F-55 / S6)
# ---------------------------------------------------------------------------
#
# `credential_patterns` is the only free-text *regular expression* list an
# admin can PUT. Every entry is compiled and run over every assistant's system
# prompt and every context block, on the audit thread, inside the global lock
# (F-08). A single catastrophic entry therefore stalls the whole moderation
# surface, and no request can reclaim it.
#
# Three independent bounds, because each covers what the others cannot:
#
#   1. SIZE      -- `CREDENTIAL_PATTERNS_MAX` entries of at most
#                   `CREDENTIAL_PATTERN_MAX_LENGTH` characters each. Cheap,
#                   total, and the only one that is a *proof* of anything: it
#                   caps how much work an admin can even describe.
#   2. SCREENING -- a static shape heuristic plus an empirical pump probe, both
#                   at validation time. See `_reject_if_catastrophic` for an
#                   honest statement of what each can and cannot detect.
#   3. MATCH-TIME-- every search runs through `compile_credential_patterns` /
#                   `search_credential_pattern`, under
#                   `CREDENTIAL_PATTERN_MATCH_TIMEOUT_SECONDS`. This is the
#                   actual guarantee. (2) reduces how often an admin discovers
#                   the hard way that their pattern is useless; (3) is what
#                   makes the failure bounded regardless.
CREDENTIAL_PATTERNS_MAX = 32
CREDENTIAL_PATTERN_MAX_LENGTH = 200
# Bound on one `search()` over one haystack. The shipped patterns answer in
# microseconds against a 12 000-character prompt; 0.25 s is four orders of
# magnitude of headroom and still bounds a full audit of 282 assistants x 8
# patterns x 2 fields at well under a minute even in the worst case.
CREDENTIAL_PATTERN_MATCH_TIMEOUT_SECONDS = 0.25
# Bound on one probe during validation. Shorter than the match bound because a
# rejected pattern should cost an admin a fast 400, not a slow one.
_CREDENTIAL_PROBE_TIMEOUT_SECONDS = 0.1
# The gate never needs more than the similarity ceiling of text; capping the
# haystack caps the input side of any blow-up that the engine does survive.
CREDENTIAL_HAYSTACK_MAX_CHARS = 12000

# Pump strings. A catastrophic pattern is catastrophic because backtracking
# grows exponentially in the length of a *repeating* prefix that ultimately
# fails to match, so each probe is a run of one character class followed by a
# character that forces the failure. Deliberately short (24): an exponential
# pattern already blows the 0.1 s budget here, and a linear one still finishes
# in microseconds.
_PROBE_RUN = 24
_CREDENTIAL_PROBES = tuple(
    unit * _PROBE_RUN + tail
    for unit, tail in (
        ("a", "!"),
        ("A", "!"),
        ("0", "!"),
        (" ", "!"),
        ("ab", "!"),
        ("a1", "!"),
        ("-", "!"),
        ("x", "y!"),
    )
)

# Static shapes that are the classic exponential-backtracking constructions:
# a quantifier applied to a group whose body is itself quantified or is an
# alternation. `[^()]` keeps the scan to the innermost group so an outer group
# containing an unrelated quantifier elsewhere is not flagged.
# Fragments shared by the two shapes below. Built from named pieces rather than
# one long literal so each regex stays readable and under the complexity budget.
_RE_ATOM = r"(?:\\.|\[[^]]*\]|[^()\\])"
_RE_QUANT = r"(?:[*+]|\{\d+,?\d*\})"
_RE_GROUP_OPEN = r"\((?![?]:?[=!<])"
_NESTED_QUANTIFIER = _stdlib_re.compile(_RE_GROUP_OPEN + _RE_ATOM + "*?" + _RE_QUANT + _RE_ATOM + r"*\)\s*" + _RE_QUANT)
_QUANTIFIED_ALTERNATION = _stdlib_re.compile(_RE_GROUP_OPEN + _RE_ATOM + r"*\|" + _RE_ATOM + r"*\)\s*" + _RE_QUANT)
# `a{5000}` is not backtracking at all -- it is just a lot of work, repeated per
# haystack position. Bound it directly.
# Possessive quantifiers: the adjacent `\s*` runs overlap when the optional digits
# are absent, which made this scan quadratic on `{1,` followed by a long run of
# spaces -- and this runs against admin-supplied patterns.
_LARGE_REPEAT = _stdlib_re.compile(r"\{\s*+(\d+)\s*+(?:,\s*+(\d*+)\s*+)?\}")
_MAX_REPEAT_COUNT = 1000


class CredentialPatternError(ValueError):
    """An admin-supplied `credential_patterns` entry that must not reach the engine.

    A `ValueError`, so `PUT /entities/{kind}/config` maps it to a 400 through
    the existing `except Exception` at
    `rest_api/routers/marketplace_management.py:1045-1048` without the router
    needing to know this type exists.
    """


def _reject_if_catastrophic(index: int, pattern: str) -> None:
    """Screen one pattern for runaway backtracking.

    **What this can detect.** Patterns whose *source* contains a quantified
    group over a quantified or alternating body (`(a+)+`, `(a|a)*`, `(x+x+)+`),
    repeat counts above `_MAX_REPEAT_COUNT`, and -- empirically, and
    independently of shape -- any pattern that exceeds
    `_CREDENTIAL_PROBE_TIMEOUT_SECONDS` on one of the eight pump strings in
    `_CREDENTIAL_PROBES`.

    **What this cannot detect, and is not claimed to.** This is a heuristic, not
    a decision procedure; deciding whether a backtracking regex is polynomial is
    not something a screen of this size settles. Specifically it will miss:
    blow-ups that only fire on an alphabet none of the eight probes pump;
    polynomial (rather than exponential) blow-ups, which stay under the probe
    budget at length 24 and only bite on a long real prompt; and back-reference
    or lookaround constructions whose cost is not visible in the source shape.
    It will also *over*-reject: `(?:https?|ftp)+` is alternation-under-quantifier
    and perfectly safe, and is refused anyway.

    That asymmetry is deliberate and it is why this is not the real defence.
    The real defence is `CREDENTIAL_PATTERN_MATCH_TIMEOUT_SECONDS` at match
    time, which bounds every search regardless of what this function concluded.
    """
    _reject_large_repeats(index, pattern)
    _reject_nested_quantifiers(index, pattern)
    if _regex is None:  # pragma: no cover - defensive, see the import guard
        return
    _probe_for_stalls(index, pattern)


def _reject_large_repeats(index: int, pattern: str) -> None:
    for lo, hi in _LARGE_REPEAT.findall(pattern):
        counts = [int(lo)] + ([int(hi)] if hi else [])
        if max(counts) > _MAX_REPEAT_COUNT:
            raise CredentialPatternError(
                f"credential_patterns[{index}] repeats more than {_MAX_REPEAT_COUNT} times; bound the repeat count"
            )


def _reject_nested_quantifiers(index: int, pattern: str) -> None:
    if _NESTED_QUANTIFIER.search(pattern) or _QUANTIFIED_ALTERNATION.search(pattern):
        raise CredentialPatternError(
            f"credential_patterns[{index}] applies a quantifier to a group that is itself "
            "quantified or alternating, which backtracks catastrophically; rewrite it "
            "without the nested quantifier"
        )


def _probe_for_stalls(index: int, pattern: str) -> None:
    """Compile the pattern and time it against the pump strings."""
    try:
        compiled = _regex.compile(pattern)
    except Exception as exc:  # noqa: BLE001 - reported verbatim to the admin
        raise CredentialPatternError(f"credential_patterns[{index}] is not a valid regular expression: {exc}") from exc
    for probe in _CREDENTIAL_PROBES:
        try:
            compiled.search(probe, timeout=_CREDENTIAL_PROBE_TIMEOUT_SECONDS)
        except _REGEX_TIMEOUT_ERRORS:
            raise CredentialPatternError(
                f"credential_patterns[{index}] did not finish within "
                f"{_CREDENTIAL_PROBE_TIMEOUT_SECONDS}s against a {len(probe)}-character probe "
                "and would stall every audit; rewrite it"
            ) from None
        except Exception:  # noqa: BLE001 - a probe-specific engine error is not the admin's problem
            continue


def validate_credential_patterns(patterns: Any) -> list[str]:
    """Validate and return the bounded `credential_patterns` list.

    Raises `CredentialPatternError` (a `ValueError`) on anything the audit
    thread must never be handed. Returns the list unchanged on success so it
    can be used inline where the value is assigned.
    """
    if patterns is None:
        return []
    if isinstance(patterns, (str, bytes)) or not isinstance(patterns, (list, tuple)):
        raise CredentialPatternError("credential_patterns must be a list of regular expressions")
    values = list(patterns)
    if len(values) > CREDENTIAL_PATTERNS_MAX:
        raise CredentialPatternError(
            f"credential_patterns accepts at most {CREDENTIAL_PATTERNS_MAX} entries, got {len(values)}"
        )
    for index, pattern in enumerate(values):
        if not isinstance(pattern, str):
            raise CredentialPatternError(f"credential_patterns[{index}] must be a string, got {type(pattern).__name__}")
        if not pattern.strip():
            raise CredentialPatternError(f"credential_patterns[{index}] is empty")
        if len(pattern) > CREDENTIAL_PATTERN_MAX_LENGTH:
            raise CredentialPatternError(
                f"credential_patterns[{index}] is {len(pattern)} characters; "
                f"the limit is {CREDENTIAL_PATTERN_MAX_LENGTH}"
            )
        try:
            _stdlib_re.compile(pattern)
        except _stdlib_re.error as exc:
            raise CredentialPatternError(
                f"credential_patterns[{index}] is not a valid regular expression: {exc}"
            ) from exc
        _reject_if_catastrophic(index, pattern)
    return values


def compile_credential_patterns(patterns: list[str]) -> list[Any]:
    """Compile for the gate, preferring the engine that can be timed out.

    Falls back to `re` only if `regex` is genuinely unavailable, in which case
    `search_credential_pattern` degrades to an unbounded match -- the pre-8.6
    behaviour, logged rather than silently assumed.
    """
    engine = _regex if _regex is not None else _stdlib_re
    return [engine.compile(p) for p in patterns]


def search_credential_pattern(compiled: Any, text: str) -> Any:
    """Run one credential pattern under a hard wall-clock bound.

    Returns the match, or `None` if there is none **or** if the search exceeded
    `CREDENTIAL_PATTERN_MATCH_TIMEOUT_SECONDS`. A timeout is reported as "no
    match" on purpose: the gate emits CRITICAL findings, and a pattern that
    could not be evaluated is not evidence that a credential is present. The
    caller logs the timeout so an unusable pattern is visible rather than
    silently degrading the check.

    Raises `TimeoutError` so the caller can tell the two cases apart.
    """
    haystack = text[:CREDENTIAL_HAYSTACK_MAX_CHARS]
    if _regex is None:  # pragma: no cover - defensive, see the import guard
        return compiled.search(haystack)
    return compiled.search(haystack, timeout=CREDENTIAL_PATTERN_MATCH_TIMEOUT_SECONDS)


@dataclass
class GatesConfig:
    name_min: int
    name_max: int
    description_min: int
    system_prompt_min: int
    substance_prompt_alt_min: int
    banned_generic_names: list[str] = field(default_factory=list)
    credential_patterns: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # On the dataclass rather than in `from_dict`, so both construction
        # paths are covered: the YAML/`PUT /config` loader *and*
        # `Config.from_marketplace_management`. F-11 is the precedent -- a rule
        # enforced on one path only shipped a config that violated it.
        self.credential_patterns = validate_credential_patterns(self.credential_patterns)


@dataclass
class SimilarityThresholds:
    critical: float
    optional: float


@dataclass
class TfidfConfig:
    max_features: int = 50000
    ngram_range: tuple[int, int] = (1, 2)
    min_df: int = 2


@dataclass
class SimilarityConfig:
    weights: dict[str, float]
    thresholds: SimilarityThresholds
    system_prompt_ceiling: int
    shortlist_size: int
    tfidf: TfidfConfig
    report_threshold: float = 0.40
    # INERT. Retained for the `GET/PUT /entities/{kind}/config` contract only;
    # the backend factory these three selected was deleted with the rest of the
    # unreachable dense-vector path (F-71, EXECUTION-PLAN 9.1). Nothing reads
    # them to choose behaviour. See CHANGE-BRAINSTORM D3b.
    text_backend: str = "elasticsearch"
    minilm_model: str = ""

    # The three dense-vector text signals. No wired backend can score them, so
    # any weight spent on one is a share of the budget that contributes 0.0 to
    # every pair while lowering the effective ceiling of every structural
    # signal — F-13, reconstructed by hand through `PUT /config`. Rejected by
    # name rather than dropped silently, so the operator learns why.
    #
    # Lift this list when the funded vector-rehabilitation follow-up lands a
    # real backend (CHANGE-BRAINSTORM D3b); until then, an unscoreable weight
    # is a configuration error.
    UNSCOREABLE_WEIGHTS = ("description", "system_prompt", "purpose")

    def validate(self) -> None:
        # Every structural signal is always scored, so a config that omits one
        # is silently ignoring a live signal — that is an error. `clone` is
        # deliberately absent from both sets: a recorded clone edge gates and
        # overrides the score rather than being blended into it, so it carries
        # no weight to configure (see `similarity.score_pair`).
        required = {
            "name",
            "tools",
            "mcp_servers",
            "skills",
            "datasources",
            "categories",
        }
        got = set(self.weights)

        unscoreable = sorted(got & set(self.UNSCOREABLE_WEIGHTS))
        if unscoreable:
            raise ValueError(
                "similarity.weights carries weight on signals no wired backend "
                f"can score: {unscoreable}. These are dense-vector text signals "
                "and v1 similarity is structural + exact-name + clone; a weight "
                "on them contributes 0.0 to every pair while consuming part of "
                "the 1.0 budget. Remove them and renormalise."
            )

        missing = required - got
        extra = got - required
        if missing or extra:
            raise ValueError(
                f"similarity.weights must contain exactly {sorted(required)}; "
                f"missing={sorted(missing)}, extra={sorted(extra)}"
            )
        total = sum(self.weights.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"similarity.weights must sum to 1.0 (got {total:.4f})")

        # A threshold no pair can reach is a disabled feature that reports as a
        # strict policy. The maximum attainable combined score is the weight
        # sum (1.0 by the check above), so this can only fire on a threshold
        # above 1.0 — which is exactly the shape of the bug that shipped, where
        # the reachable maximum was 0.65 against a 0.85 bar.
        attainable = total
        if self.thresholds.critical > attainable + 1e-6:
            raise ValueError(
                f"similarity.thresholds.critical={self.thresholds.critical} is "
                f"above the highest attainable combined score ({attainable:.4f}); "
                "no pair could ever be flagged"
            )


@dataclass
class UsageTiersConfig:
    hot_percentile: int
    warm_percentile: int
    cold_percentile: int


@dataclass
class VerificationCoreGates:
    """Hard floor — an assistant that fails ANY of these cannot be VERIFIED."""

    description_min: int = 100
    system_prompt_min: int = 200
    # An assistant with no tool/mcp/skill/datasource still counts as substantive
    # if its system_prompt is at least this long (e.g. the AI/Run FAQ chatbot).
    # 500 is the specified value; see the measurement recorded beside the same
    # default in codemie.configs.marketplace_management.
    substance_prompt_alt_min: int = 500


@dataclass
class VerificationPolishGates:
    """Metadata polish — required for VERIFIED on non-HOT tiers only."""

    description_min: int = 250
    categories_min: int = 2
    require_icon: bool = True
    conversation_starters_min: int = 2


@dataclass
class VerificationConfig:
    """Adoption-aware bucket rules — the new marketplace verification layer."""

    hot_min_users: int = 50
    warm_min_users: int = 7
    cold_min_users: int = 3
    grace_days: int = 60
    archive_dead_after_days: int = 30
    core_gates: VerificationCoreGates = field(default_factory=VerificationCoreGates)
    polish_gates: VerificationPolishGates = field(default_factory=VerificationPolishGates)

    def __post_init__(self) -> None:
        if self.archive_dead_after_days <= 0:
            raise ValueError(f"archive_dead_after_days must be a positive integer; got {self.archive_dead_after_days}")


@dataclass
class Config:
    gates: GatesConfig
    similarity: SimilarityConfig
    usage_tiers: UsageTiersConfig
    verification: VerificationConfig = field(default_factory=VerificationConfig)

    @classmethod
    def from_marketplace_management(cls, mm_cfg: "Any") -> "Config":
        """Convert a MarketplaceManagementConfig into the legacy Config shape.

        This decouples the pure-Python validator library from where the config
        comes from, so the Engine keeps receiving a Config dataclass as before.
        """
        cg = mm_cfg.core_gates
        pg = mm_cfg.polish_gates
        sim = mm_cfg.similarity
        weights = dict(mm_cfg.weights)

        cfg = cls(
            gates=GatesConfig(
                name_min=5,
                name_max=60,
                description_min=cg.description_min,
                system_prompt_min=cg.system_prompt_min,
                substance_prompt_alt_min=cg.substance_prompt_alt_min,
                banned_generic_names=[
                    "assistant",
                    "ai assistant",
                    "bot",
                    "chatbot",
                    "chat bot",
                    "helper",
                    "my assistant",
                    "test",
                    "test assistant",
                    "demo",
                    "example",
                    "untitled",
                    "new assistant",
                    "copy",
                    "copy of",
                    "default",
                ],
                credential_patterns=[
                    r"(?i)AKIA[0-9A-Z]{16}",
                    r"(?i)aws_secret_access_key",
                    r"(?i)-----BEGIN [A-Z ]*PRIVATE KEY-----",
                    r"(?i)ghp_[A-Za-z0-9]{30,}",
                    r"(?i)xox[baprs]-[A-Za-z0-9-]{10,}",
                    r"(?i)Bearer\s+[A-Za-z0-9._-]{20,}",
                    r"(?i)api[_-]?key\s*[:=]\s*['\"]?[A-Za-z0-9_-]{16,}",
                    r"(?i)password\s*[:=]\s*['\"]?[^\s'\"]{6,}",
                ],
            ),
            similarity=SimilarityConfig(
                weights=weights,
                thresholds=SimilarityThresholds(
                    critical=sim.critical_threshold,
                    optional=sim.optional_threshold,
                ),
                system_prompt_ceiling=12000,
                shortlist_size=sim.shortlist_size,
                tfidf=TfidfConfig(),
                report_threshold=sim.report_threshold,
                text_backend=sim.text_backend,
            ),
            usage_tiers=UsageTiersConfig(
                hot_percentile=90,
                warm_percentile=50,
                cold_percentile=10,
            ),
            verification=VerificationConfig(
                hot_min_users=mm_cfg.hot_min_users,
                warm_min_users=mm_cfg.warm_min_users,
                cold_min_users=mm_cfg.cold_min_users,
                grace_days=mm_cfg.grace_days,
                archive_dead_after_days=mm_cfg.archive_dead_after_days,
                core_gates=VerificationCoreGates(
                    description_min=cg.description_min,
                    system_prompt_min=cg.system_prompt_min,
                    substance_prompt_alt_min=cg.substance_prompt_alt_min,
                ),
                polish_gates=VerificationPolishGates(
                    description_min=pg.description_min,
                    categories_min=pg.categories_min,
                    require_icon=pg.require_icon,
                    conversation_starters_min=pg.conversation_starters_min,
                ),
            ),
        )
        # Enforce the weight invariant on the LIVE path. It used to run only
        # inside `from_dict` (the legacy YAML loader), so a broken default
        # dict reached the engine unchallenged and only surfaced later as a
        # 400 on every `PUT /entities/{kind}/config` (F-11).
        cfg.similarity.validate()
        return cfg

    @classmethod
    def for_assistant_gates(cls) -> "Config":
        """The live assistant gate config, loaded the one way.

        Six call sites across two routers and the LLM assessment service each
        hand-wrote `from_marketplace_management(get_marketplace_management_config
        ("assistants"))`, so the publish gate, the reviewer's drawer, the
        gate-check preflight and the batch auditor each loaded their own copy of
        the same config (MR !4070 review comment #12). Imported lazily so the
        validator package stays independent of where the config comes from.
        """
        from codemie.configs.marketplace_management import get_marketplace_management_config

        return cls.from_marketplace_management(get_marketplace_management_config("assistants"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "gates": {
                "name_min": self.gates.name_min,
                "name_max": self.gates.name_max,
                "description_min": self.gates.description_min,
                "system_prompt_min": self.gates.system_prompt_min,
                "substance_prompt_alt_min": self.gates.substance_prompt_alt_min,
                "banned_generic_names": self.gates.banned_generic_names,
                "credential_patterns": self.gates.credential_patterns,
            },
            "similarity": {
                "weights": self.similarity.weights,
                "thresholds": {
                    "critical": self.similarity.thresholds.critical,
                    "optional": self.similarity.thresholds.optional,
                },
                "report_threshold": self.similarity.report_threshold,
                "text_backend": self.similarity.text_backend,
                "minilm_model": self.similarity.minilm_model,
                "system_prompt_ceiling": self.similarity.system_prompt_ceiling,
                "shortlist_size": self.similarity.shortlist_size,
                "tfidf": {
                    "max_features": self.similarity.tfidf.max_features,
                    "ngram_range": list(self.similarity.tfidf.ngram_range),
                    "min_df": self.similarity.tfidf.min_df,
                },
            },
            "usage_tiers": {
                "hot_percentile": self.usage_tiers.hot_percentile,
                "warm_percentile": self.usage_tiers.warm_percentile,
                "cold_percentile": self.usage_tiers.cold_percentile,
            },
            "verification": {
                "hot_min_users": self.verification.hot_min_users,
                "warm_min_users": self.verification.warm_min_users,
                "cold_min_users": self.verification.cold_min_users,
                "grace_days": self.verification.grace_days,
                "archive_dead_after_days": self.verification.archive_dead_after_days,
                "core_gates": {
                    "description_min": self.verification.core_gates.description_min,
                    "system_prompt_min": self.verification.core_gates.system_prompt_min,
                    "substance_prompt_alt_min": self.verification.core_gates.substance_prompt_alt_min,
                },
                "polish_gates": {
                    "description_min": self.verification.polish_gates.description_min,
                    "categories_min": self.verification.polish_gates.categories_min,
                    "require_icon": self.verification.polish_gates.require_icon,
                    "conversation_starters_min": self.verification.polish_gates.conversation_starters_min,
                },
            },
        }


def load_config(path: str | Path) -> Config:
    """Load and validate a YAML config file."""
    with open(path, "r") as fp:
        raw = yaml.safe_load(fp)
    return from_dict(raw)


def _verification_from_dict(raw: dict[str, Any]) -> VerificationConfig:
    core = raw.get("core_gates", {})
    polish = raw.get("polish_gates", {})
    return VerificationConfig(
        hot_min_users=int(raw.get("hot_min_users", 50)),
        warm_min_users=int(raw.get("warm_min_users", 6)),
        cold_min_users=int(raw.get("cold_min_users", 2)),
        grace_days=int(raw.get("grace_days", 90)),
        archive_dead_after_days=int(raw.get("archive_dead_after_days", 30)),
        core_gates=VerificationCoreGates(
            description_min=int(core.get("description_min", 100)),
            system_prompt_min=int(core.get("system_prompt_min", 200)),
            substance_prompt_alt_min=int(core.get("substance_prompt_alt_min", 800)),
        ),
        polish_gates=VerificationPolishGates(
            description_min=int(polish.get("description_min", 250)),
            categories_min=int(polish.get("categories_min", 2)),
            require_icon=bool(polish.get("require_icon", True)),
            conversation_starters_min=int(polish.get("conversation_starters_min", 2)),
        ),
    )


def from_dict(raw: dict[str, Any]) -> Config:
    g = raw.get("gates", {})
    s = raw.get("similarity", {})
    u = raw.get("usage_tiers", {})
    v = raw.get("verification", {})
    tfidf = s.get("tfidf", {})

    cfg = Config(
        gates=GatesConfig(
            name_min=int(g.get("name_min", 5)),
            name_max=int(g.get("name_max", 60)),
            description_min=int(g.get("description_min", 100)),
            system_prompt_min=int(g.get("system_prompt_min", 200)),
            substance_prompt_alt_min=int(g.get("substance_prompt_alt_min", 500)),
            banned_generic_names=list(g.get("banned_generic_names", [])),
            credential_patterns=list(g.get("credential_patterns", [])),
        ),
        similarity=SimilarityConfig(
            weights=dict(s.get("weights", {})),
            thresholds=SimilarityThresholds(
                critical=float(s.get("thresholds", {}).get("critical", 0.92)),
                optional=float(s.get("thresholds", {}).get("optional", 0.80)),
            ),
            system_prompt_ceiling=int(s.get("system_prompt_ceiling", 12000)),
            shortlist_size=int(s.get("shortlist_size", 100)),
            tfidf=TfidfConfig(
                max_features=int(tfidf.get("max_features", 50000)),
                ngram_range=tuple(tfidf.get("ngram_range", [1, 2])),
                min_df=int(tfidf.get("min_df", 2)),
            ),
            report_threshold=float(s.get("report_threshold", 0.55)),
            text_backend=str(s.get("text_backend", "elasticsearch")),
            minilm_model=str(s.get("minilm_model", "")),
        ),
        usage_tiers=UsageTiersConfig(
            hot_percentile=int(u.get("hot_percentile", 90)),
            warm_percentile=int(u.get("warm_percentile", 50)),
            cold_percentile=int(u.get("cold_percentile", 10)),
        ),
        verification=_verification_from_dict(v),
    )
    cfg.similarity.validate()
    return cfg
