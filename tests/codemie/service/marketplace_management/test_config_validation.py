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

"""The shipped similarity config must satisfy its own invariant (F-11).

`SimilarityConfig.validate()` requires the weight dict to sum to 1.0, but it
was only ever called from `from_dict` — the legacy YAML path. The live path,
`Config.from_marketplace_management`, skipped it, so the defaults shipped
summing to 1.10 and nobody noticed until `PUT /entities/{kind}/config` (which
does round-trip through `from_dict`) rejected *every* admin edit with a 400
about weights the admin had not touched.

These tests pin both halves: the shipped numbers are valid, and the live
constructor is the thing that enforces it.
"""

from __future__ import annotations

import pytest

from codemie.configs.marketplace_management import (
    MARKETPLACE_MANAGEMENT_DEFAULTS,
    MarketplaceManagementConfig,
    list_supported_kinds,
    get_marketplace_management_config,
)
from codemie.service.marketplace_management.validator.config import Config


def test_shipped_weights_sum_to_one() -> None:
    """The defaults an admin inherits must already be valid."""
    total = sum(MARKETPLACE_MANAGEMENT_DEFAULTS.weights.values())

    assert abs(total - 1.0) < 1e-6, f"shipped similarity weights sum to {total:.4f}, not 1.0"


@pytest.mark.parametrize("kind", list_supported_kinds())
def test_every_registered_kind_ships_a_valid_config(kind: str) -> None:
    """Each per-kind config instance is independently validated."""
    cfg = Config.from_marketplace_management(get_marketplace_management_config(kind))

    cfg.similarity.validate()


# ---------------------------------------------------------------------------
# 9.1 / 9.7 -- no weight may be spent on a signal that cannot be scored (F-13)
# ---------------------------------------------------------------------------


DEAD_TEXT_SIGNALS = ("description", "system_prompt", "purpose")


def test_no_dead_text_weights() -> None:
    """The weight dict spends nothing on the three dense-vector text signals.

    `description`, `system_prompt` and `purpose` are scored by a text backend
    whose only wired implementation returns `None` from `fit_transform`, so
    every one of them contributed exactly 0.0 to every pair in all 82 recorded
    runs. Between them they held 0.45 of a budget that shipped summing to 1.10
    -- which both broke the sum-to-1.0 invariant (F-11) and capped the highest
    attainable combined score at 0.65, below the 0.70 OPTIONAL bar (F-13).

    v1 is structural + exact-name + clone (CHANGE-BRAINSTORM D3b). Asserted for
    every registered kind: each ships its own independently-editable dict.
    """
    for kind in list_supported_kinds():
        weights = get_marketplace_management_config(kind).weights

        for dead in DEAD_TEXT_SIGNALS:
            assert dead not in weights, (
                f"{kind}: weight {dead!r}={weights[dead]} is spent on a signal " "no wired backend can score"
            )
        total = sum(weights.values())
        assert abs(total - 1.0) < 1e-9, f"{kind}: weights sum to {total:.4f}, not 1.0"


def test_a_text_weight_is_rejected_not_silently_ignored() -> None:
    """Re-introducing a text weight through the config API must 400, not pass.

    Absent from the defaults is not the same as impossible: `PUT /entities/
    {kind}/config` takes an arbitrary weight dict, and `score_pair` reads it
    with `.get(k, 0.0)`, so a text key an admin adds is accepted, sums into the
    1.0 total, and then contributes nothing -- silently lowering the effective
    ceiling of every structural signal. That is F-13 reconstructed by hand.
    The validator has to name the reason rather than accept the budget.
    """
    revived = MarketplaceManagementConfig()
    revived.weights = {**revived.weights, "tools": 0.03, "purpose": 0.20}
    assert abs(sum(revived.weights.values()) - 1.0) < 1e-9, "fixture must sum to 1.0"

    with pytest.raises(ValueError, match="purpose"):
        Config.from_marketplace_management(revived)


def test_from_marketplace_management_rejects_broken_weights() -> None:
    """The live constructor enforces the invariant, not just `from_dict`.

    Without this the only guard was on the YAML path, so a bad weight dict
    reached the engine silently and only blew up later at the API edge.
    """
    broken = MarketplaceManagementConfig()
    broken.weights = dict(broken.weights)
    broken.weights["tools"] = broken.weights["tools"] + 0.5

    with pytest.raises(ValueError, match="sum to 1.0"):
        Config.from_marketplace_management(broken)


# ---------------------------------------------------------------------------
# 8.6 -- bounded credential_patterns (F-55 / S6)
# ---------------------------------------------------------------------------
#
# `credential_patterns` is the only free-text *regex* list an admin can PUT.
# Every entry is compiled and run over every assistant's system prompt and
# every context block, on the audit thread, inside the global lock (F-08). One
# unbounded or catastrophically-backtracking entry is therefore a one-request
# denial of service against the whole moderation surface.
#
# `PUT /entities/{kind}/config` funnels the merged document through
# `validator.config.from_dict` and maps any exception to a 400
# (`rest_api/routers/marketplace_management.py:1045-1048`), so rejecting inside
# `from_dict` is what produces the 400 the plan asks for. The router-level
# assertion below pins that mapping so the two halves cannot drift apart.

# A pattern that is catastrophic in a real engine at trivial input size:
# `(a|a)+$` against 22 a's and a non-match takes ~0.3 s in stdlib `re` and
# doubles per added character.
CATASTROPHIC_PATTERN = r"(a|a)+$"


def _config_document() -> dict:
    """The shipped assistant config, as `PUT /config` sees it before merging."""
    return Config.from_marketplace_management(get_marketplace_management_config("assistants")).to_dict()


def _with_patterns(patterns: list[str]) -> dict:
    doc = _config_document()
    doc["gates"] = {**doc["gates"], "credential_patterns": patterns}
    return doc


def test_credential_patterns_are_bounded() -> None:
    """Over-long, over-many, unparseable and catastrophic patterns are all rejected.

    Four independent rejections, because each is a different way to weaponise
    the same field and any one of them alone leaves the hole open.
    """
    from codemie.service.marketplace_management.validator.config import (
        CREDENTIAL_PATTERNS_MAX,
        CREDENTIAL_PATTERN_MAX_LENGTH,
        from_dict,
    )

    over_long = "(?i)" + "a" * (CREDENTIAL_PATTERN_MAX_LENGTH + 1)
    with pytest.raises(ValueError, match="credential_patterns"):
        from_dict(_with_patterns([over_long]))

    with pytest.raises(ValueError, match="credential_patterns"):
        from_dict(_with_patterns(["a"] * (CREDENTIAL_PATTERNS_MAX + 1)))

    with pytest.raises(ValueError, match="credential_patterns"):
        from_dict(_with_patterns(["(unclosed"]))

    with pytest.raises(ValueError, match="credential_patterns"):
        from_dict(_with_patterns([CATASTROPHIC_PATTERN]))


def test_shipped_credential_patterns_survive_their_own_validator() -> None:
    """The eight defaults an admin inherits must pass the bound they will be held to.

    A validator strict enough to reject the shipped list would make the feature
    unconfigurable on first `PUT`, which is exactly how F-11 happened.
    """
    cfg = Config.from_marketplace_management(get_marketplace_management_config("assistants"))

    from codemie.service.marketplace_management.validator.config import (
        validate_credential_patterns,
    )

    assert validate_credential_patterns(cfg.gates.credential_patterns) == list(cfg.gates.credential_patterns)


# Run in a child process, not in-process: a catastrophic `re` match is a single
# C call, so no Python-level alarm can interrupt it. Before the fix this script
# does not terminate and the subprocess timeout is what reports the failure --
# which is precisely the production symptom (F-08: the audit thread is stuck
# inside the global lock and nothing can reclaim it).
_MATCH_BOUND_SCRIPT = """
import sys, time
from codemie.configs.marketplace_management import get_marketplace_management_config
from codemie.service.marketplace_management.validator.config import Config
from codemie.service.marketplace_management.validator.gates import gate_no_inline_credentials
sys.path.insert(0, {conftest_dir!r})
from conftest import make_assistant

cfg = Config.from_marketplace_management(get_marketplace_management_config("assistants"))
cfg.gates.credential_patterns = [{pattern!r}]
record = make_assistant(id="redos-victim", system_prompt="a" * 4000 + "!")

started = time.monotonic()
findings = gate_no_inline_credentials(record, cfg)
print("ELAPSED", round(time.monotonic() - started, 3))
print("FINDINGS", len(findings))
"""


def test_credential_gate_is_bounded_at_match_time() -> None:
    """Even a pattern that slipped past validation cannot hang the audit thread.

    Static analysis of regular expressions is a heuristic, so the *guarantee*
    has to be at match time: the gate runs under a hard wall-clock timeout and
    a pattern that exceeds it is dropped with a log line, not allowed to spin.
    The pattern is injected past the validator on purpose -- this test exists
    precisely for the case the validator misses.
    """
    import subprocess
    import sys
    from pathlib import Path

    script = _MATCH_BOUND_SCRIPT.format(
        conftest_dir=str(Path(__file__).resolve().parent),
        pattern=CATASTROPHIC_PATTERN,
    )
    try:
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        pytest.fail(
            "gate_no_inline_credentials did not return within 60s for a single assistant: "
            f"the admin-supplied pattern {CATASTROPHIC_PATTERN!r} stalls the audit thread (F-55)"
        )

    assert proc.returncode == 0, f"probe failed:\n{proc.stdout}\n{proc.stderr}"
    elapsed = float(proc.stdout.split("ELAPSED")[1].split()[0])
    assert elapsed < 5.0, (
        f"gate_no_inline_credentials spent {elapsed:.1f}s on one assistant; "
        "an admin-supplied regex must not be able to stall the audit"
    )
    # CR-003: a timed-out pattern emits one credential_scan_timeout CRITICAL finding
    # so the assistant is not falsely passed as clean (partial scan = incomplete result).
    findings_count = int(proc.stdout.split("FINDINGS")[1].split()[0])
    assert findings_count >= 1, "a timed-out pattern must emit a scan_incomplete finding (CR-003)"


def test_put_config_rejects_a_catastrophic_pattern_with_400() -> None:
    """The rejection must reach the client as a 400, not as a 500 or a 200.

    `from_dict` raising is only half the fix; the half that matters to an admin
    is that `PUT /entities/{kind}/config` turns it into a 400 naming the field.
    Asserted against the real handler so the two cannot drift.
    """
    import asyncio

    from fastapi import HTTPException

    from codemie.rest_api.routers.marketplace_management import ConfigUpdate, update_config
    from codemie.service.marketplace_management.models import EntityKind

    from codemie.configs.marketplace_management import get_marketplace_management_config
    from codemie.service.marketplace_management.validator.config import Config

    base_cfg = Config.from_marketplace_management(get_marketplace_management_config("assistants"))
    gates_dict = {
        "name_min": base_cfg.gates.name_min,
        "name_max": base_cfg.gates.name_max,
        "description_min": base_cfg.gates.description_min,
        "system_prompt_min": base_cfg.gates.system_prompt_min,
        "substance_prompt_alt_min": base_cfg.gates.substance_prompt_alt_min,
        "credential_patterns": [CATASTROPHIC_PATTERN],
    }
    body = ConfigUpdate(gates=gates_dict)

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(update_config(body=body, kind=EntityKind.ASSISTANT))

    assert excinfo.value.status_code == 400
    assert "credential_patterns" in str(excinfo.value.detail)
