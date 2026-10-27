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

"""The shipped similarity numbers must equal the numbers that were decided.

F-88: every specified weight and threshold was changed somewhere between the
story and the code, an unspecified ninth signal (`purpose`, 0.20) ended up
carrying the largest weight in the system, and `substance_prompt_alt_min`
shipped at 800 against a specified 500. The result is that nobody can say
what bar the marketplace is held to.

These tests are the reconciliation required by EXECUTION-PLAN 9.7: they pin
the *decided* numbers (recorded in CHANGE-BRAINSTORM D3b / D3b-weights) so a
future edit to either the config or the decision breaks the other one loudly
instead of drifting.
"""

from __future__ import annotations

from codemie.configs.marketplace_management import (
    MARKETPLACE_MANAGEMENT_DEFAULTS,
    get_marketplace_management_config,
    list_supported_kinds,
)
from codemie.service.marketplace_management.validator.config import Config

# --- The decision, transcribed. Change these only together with the record in
# --- docs/marketplace-audit-20260820/CHANGE-BRAINSTORM.md.
SPEC_SUBSTANCE_PROMPT_ALT_MIN = 500

AGREED_WEIGHTS = {
    "name": 0.08,
    "tools": 0.23,
    "mcp_servers": 0.20,
    "skills": 0.26,
    "datasources": 0.15,
    "categories": 0.08,
}
AGREED_CRITICAL_THRESHOLD = 0.70
AGREED_OPTIONAL_THRESHOLD = 0.55
AGREED_REPORT_THRESHOLD = 0.40


def test_substance_threshold_matches_the_spec() -> None:
    """`substance_prompt_alt_min` is the specified 500, not the shipped 800.

    Measured against the live corpus before deciding: of 219 audited
    assistants, 58 configure no tool / MCP / skill / datasource, and 56 of
    those clear **both** bars — zero rows change verdict between 800 and 500.
    With no measurable cost to honouring the written spec, the config moves to
    the spec rather than the spec moving to the config.
    """
    assert MARKETPLACE_MANAGEMENT_DEFAULTS.core_gates.substance_prompt_alt_min == SPEC_SUBSTANCE_PROMPT_ALT_MIN


def test_substance_threshold_reaches_the_gate() -> None:
    """The number the gate actually enforces is the number in the config.

    `Config.from_marketplace_management` copies it into two places
    (`gates` and `verification.core_gates`); both feed live code paths, and a
    config value that only lands in one of them is half-applied.
    """
    cfg = Config.from_marketplace_management(MARKETPLACE_MANAGEMENT_DEFAULTS)

    assert cfg.gates.substance_prompt_alt_min == SPEC_SUBSTANCE_PROMPT_ALT_MIN
    assert cfg.verification.core_gates.substance_prompt_alt_min == SPEC_SUBSTANCE_PROMPT_ALT_MIN


def test_similarity_weights_match_the_agreed_config() -> None:
    """The weight dict is exactly the six structural signals, summing to 1.0."""
    weights = MARKETPLACE_MANAGEMENT_DEFAULTS.weights

    assert weights == AGREED_WEIGHTS
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_thresholds_match_the_agreed_config() -> None:
    sim = MARKETPLACE_MANAGEMENT_DEFAULTS.similarity

    assert sim.critical_threshold == AGREED_CRITICAL_THRESHOLD
    assert sim.optional_threshold == AGREED_OPTIONAL_THRESHOLD
    assert sim.report_threshold == AGREED_REPORT_THRESHOLD


def test_the_critical_threshold_is_reachable() -> None:
    """A perfect structural match must be able to clear the CRITICAL bar.

    This is the arithmetic F-13 was about: with 0.45 of the budget pinned to
    signals that always scored 0.0, the maximum attainable combined score was
    0.65 — below even the OPTIONAL bar of 0.70. A threshold nobody can hit is
    not a strict policy, it is a disabled feature that reports as a strict
    policy. Asserted for every registered kind, because each ships its own
    independently-editable weight dict.
    """
    for kind in list_supported_kinds():
        mm_cfg = get_marketplace_management_config(kind)
        attainable = sum(mm_cfg.weights.values())

        assert attainable >= mm_cfg.similarity.critical_threshold, (
            f"{kind}: the highest score any pair can reach is {attainable:.2f}, "
            f"below the critical threshold {mm_cfg.similarity.critical_threshold}"
        )
        assert attainable >= mm_cfg.similarity.optional_threshold
