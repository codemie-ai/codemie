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

"""Deterministic validation gates per EPMCDME-14231.

Each gate is a pure function of (assistant, config) that returns a list of
Finding objects (empty = pass). The gate REGISTRY drives execution order.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Callable

from .config import (
    CREDENTIAL_PATTERN_MATCH_TIMEOUT_SECONDS,
    Config,
    compile_credential_patterns,
    search_credential_pattern,
)
from .models import AssistantRecord, Finding, Severity

# stdlib logging: the validator package is kept
# free of `codemie.configs` imports so it stays a pure library.
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

# No leading `\s*`: with it, every position inside a long run of whitespace
# re-scanned the rest of the run, which is quadratic. `normalize_name` strips the
# whitespace left in front of a removed suffix itself.
_SUFFIX_RE = re.compile(
    r"(?:\(copy\)|\(clone\)|copy(?:\s+of)?|v\d+)\s*$",
    re.IGNORECASE,
)
_WS_RE = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """Case-insensitive, whitespace-normalized, common-suffix-stripped."""
    if not name:
        return ""
    s = name.strip().lower()
    # strip repeated suffixes (e.g. "Foo (copy) (copy)")
    while True:
        new = _SUFFIX_RE.sub("", s).strip()
        if new == s:
            break
        s = new
    return _WS_RE.sub(" ", s)


# ---------------------------------------------------------------------------
# Individual gates — each returns list[Finding]
# ---------------------------------------------------------------------------


def gate_name_length(a: AssistantRecord, cfg: Config) -> list[Finding]:
    n = len(a.name or "")
    if n < cfg.gates.name_min:
        return [
            Finding(
                rule="name_length",
                severity=Severity.CRITICAL,
                message=f"Name is {n} chars, minimum is {cfg.gates.name_min}.",
                detail={"length": n, "min": cfg.gates.name_min},
            )
        ]
    if n > cfg.gates.name_max:
        return [
            Finding(
                rule="name_length",
                severity=Severity.CRITICAL,
                message=f"Name is {n} chars, maximum is {cfg.gates.name_max}.",
                detail={"length": n, "max": cfg.gates.name_max},
            )
        ]
    return []


def gate_name_not_banned(a: AssistantRecord, cfg: Config) -> list[Finding]:
    banned = {normalize_name(n) for n in cfg.gates.banned_generic_names}
    if normalize_name(a.name) in banned:
        return [
            Finding(
                rule="name_not_banned",
                severity=Severity.CRITICAL,
                message=f"Name '{a.name}' matches a banned generic name.",
                detail={"normalized": normalize_name(a.name)},
            )
        ]
    return []


def gate_description_length(a: AssistantRecord, cfg: Config) -> list[Finding]:
    n = len(a.description or "")
    if n < cfg.gates.description_min:
        return [
            Finding(
                rule="description_length",
                severity=Severity.CRITICAL,
                message=f"Description is {n} chars, minimum is {cfg.gates.description_min}.",
                detail={"length": n, "min": cfg.gates.description_min},
            )
        ]
    return []


def gate_description_not_equal_name(a: AssistantRecord, cfg: Config) -> list[Finding]:
    if not a.description or not a.name:
        return []
    if normalize_name(a.description) == normalize_name(a.name):
        return [
            Finding(
                rule="description_not_equal_name",
                severity=Severity.OPTIONAL,
                message="Description is identical to name — add substance.",
            )
        ]
    return []


def gate_system_prompt_length(a: AssistantRecord, cfg: Config) -> list[Finding]:
    n = len(a.system_prompt or "")
    if n < cfg.gates.system_prompt_min:
        return [
            Finding(
                rule="system_prompt_length",
                severity=Severity.CRITICAL,
                message=f"System prompt is {n} chars, minimum is {cfg.gates.system_prompt_min}.",
                detail={"length": n, "min": cfg.gates.system_prompt_min},
            )
        ]
    return []


def gate_at_least_one_category(a: AssistantRecord, cfg: Config) -> list[Finding]:
    if not a.categories:
        return [
            Finding(
                rule="at_least_one_category",
                severity=Severity.CRITICAL,
                message="No categories assigned — at least one is required.",
            )
        ]
    return []


def gate_substance(a: AssistantRecord, cfg: Config) -> list[Finding]:
    has_capability = bool(a.tools or a.mcp_servers or a.skills or a.datasources)
    prompt_len = len(a.system_prompt or "")
    prompt_substantive = prompt_len >= cfg.gates.substance_prompt_alt_min
    if has_capability or prompt_substantive:
        return []
    return [
        Finding(
            rule="substance",
            severity=Severity.CRITICAL,
            message=(
                "No capability configured (no tool, MCP, skill, or datasource) "
                f"and system prompt is only {prompt_len} chars "
                f"(need >= {cfg.gates.substance_prompt_alt_min})."
            ),
            detail={
                "prompt_length": prompt_len,
                "prompt_alt_min": cfg.gates.substance_prompt_alt_min,
                "tools": len(a.tools),
                "mcp_servers": len(a.mcp_servers),
                "skills": len(a.skills),
                "datasources": len(a.datasources),
            },
        )
    ]


# Compiled patterns are cached by the exact tuple of sources, so a full audit
# compiles the list once instead of once per assistant (282x8 before). The key
# is the pattern tuple rather than the Config object because `PUT /config`
# replaces the Config wholesale and an identity-keyed cache would never hit.
# lru_cache(maxsize=32) provides automatic LRU eviction so a long-lived process
# that has seen many config edits does not accumulate compiled pattern lists.
@lru_cache(maxsize=32)
def _compile_credential_patterns_cached(key: tuple[str, ...]) -> list:
    return compile_credential_patterns(list(key))


def _credential_patterns_for(cfg: Config) -> list:
    return _compile_credential_patterns_cached(tuple(cfg.gates.credential_patterns))


def gate_no_inline_credentials(a: AssistantRecord, cfg: Config) -> list[Finding]:
    """Scan for inline secrets under a hard per-match time bound (F-55 / S6).

    `credential_patterns` is admin-supplied, so every search runs through
    `search_credential_pattern`, which bounds one match to
    `CREDENTIAL_PATTERN_MATCH_TIMEOUT_SECONDS`. A pattern that exceeds it is
    skipped with a WARNING for the rest of this assistant rather than allowed
    to spin: the audit thread holds the global lock (F-08), so an unbounded
    match is not a slow gate, it is an outage.
    """
    patterns = _credential_patterns_for(cfg)
    hits: list[Finding] = []
    for label, text in _credential_haystacks(a):
        for pat in patterns:
            try:
                m = search_credential_pattern(pat, text)
            except TimeoutError:
                hits.append(_scan_timeout_finding(pat, label, a.id))
                continue
            if m:
                # Redact the actual match; only report the pattern that fired.
                hits.append(
                    Finding(
                        rule="no_inline_credentials",
                        severity=Severity.CRITICAL,
                        message=f"Possible inline credential in {label}.",
                        detail={"pattern": pat.pattern, "location": label},
                    )
                )
                break  # one hit per location is enough
    return hits


def _credential_haystacks(a: AssistantRecord) -> list[tuple[str, str]]:
    """(label, text) pairs to scan: the system prompt plus string fields of each context block."""
    haystacks = [("system_prompt", a.system_prompt or "")]
    ctx = a.raw.get("context") or []
    if not isinstance(ctx, list):
        return haystacks
    for i, block in enumerate(ctx):
        if not isinstance(block, dict):
            continue
        for k in ("name", "description", "content", "value"):
            v = block.get(k)
            if isinstance(v, str):
                haystacks.append((f"context[{i}].{k}", v))
    return haystacks


def _scan_timeout_finding(pat, label: str, assistant_id: str) -> Finding:
    """CRITICAL finding so the assistant is not passed as clean when a pattern could not be evaluated.

    Skipping silently would let a partial scan appear as a full pass (CR-003).
    """
    pattern_str = getattr(pat, "pattern", "<unknown>")
    logger.warning(
        "[marketplace_audit] credential pattern %r exceeded %ss on %s of %s; emitting scan_incomplete finding",
        pattern_str,
        CREDENTIAL_PATTERN_MATCH_TIMEOUT_SECONDS,
        label,
        assistant_id,
    )
    return Finding(
        rule="credential_scan_timeout",
        severity=Severity.CRITICAL,
        message=(
            f"Credential pattern scan timed out on {label}; "
            "scan is incomplete and the assistant cannot be passed as clean."
        ),
        detail={
            "pattern": pattern_str,
            "location": label,
            "timeout_seconds": CREDENTIAL_PATTERN_MATCH_TIMEOUT_SECONDS,
        },
    )


GateFn = Callable[[AssistantRecord, Config], list[Finding]]

# Ordered per the ticket's acceptance criteria.
REGISTRY: list[tuple[str, GateFn]] = [
    ("name_length", gate_name_length),
    ("name_not_banned", gate_name_not_banned),
    ("description_length", gate_description_length),
    ("description_not_equal_name", gate_description_not_equal_name),
    ("system_prompt_length", gate_system_prompt_length),
    ("at_least_one_category", gate_at_least_one_category),
    ("substance", gate_substance),
    ("no_inline_credentials", gate_no_inline_credentials),
]


def run_all(a: AssistantRecord, cfg: Config) -> list[Finding]:
    findings: list[Finding] = []
    for _rule, fn in REGISTRY:
        findings.extend(fn(a, cfg))
    return findings
