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

"""Exact-text comparison of a live object with a committed JSON baseline.

The helper only reads the baseline. Regenerating one is an explicit, manual action
(``REGENERATE_COMMAND``) for an intended, reviewed contract change; no flag or environment
switch makes a failing comparison rewrite the file.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any

REGENERATE_COMMAND = "python -m tests.codemie.rest_api.router_guards.regenerate_baselines"
BASELINES_DIR = Path(__file__).parent / "baselines"
OPENAPI_BASELINE = BASELINES_DIR / "openapi.json"
ROUTE_PRECEDENCE_BASELINE = BASELINES_DIR / "route_precedence.json"
MAX_DIFF_LINES = 200


def canonical_json(obj: Any) -> str:
    """Serialize ``obj`` the way baselines are stored: sorted keys, 2-space indent, trailing newline."""
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"


def _read_baseline_text(baseline_path: Path) -> str:
    if not baseline_path.exists():
        raise AssertionError(
            f"Baseline missing: {baseline_path}\nGenerate it on the base tree with: {REGENERATE_COMMAND}"
        )
    return baseline_path.read_bytes().decode("utf-8")


def load_baseline(baseline_path: Path) -> Any:
    """Return the object stored in ``baseline_path``; a missing file is an assertion failure."""
    return json.loads(_read_baseline_text(baseline_path))


def assert_matches_baseline(live: Any, baseline_path: Path) -> None:
    """Fail unless ``canonical_json(live)`` is byte-for-byte the content of ``baseline_path``."""
    expected = _read_baseline_text(baseline_path)
    actual = canonical_json(live)
    if actual == expected:
        return

    diff = list(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            actual.splitlines(keepends=True),
            fromfile=f"baseline: {baseline_path.name}",
            tofile="live",
        )
    )
    shown = "".join(diff[:MAX_DIFF_LINES])
    hidden = len(diff) - MAX_DIFF_LINES
    truncation = f"\n... diff truncated, {hidden} more line(s)" if hidden > 0 else ""
    raise AssertionError(
        f"Live output differs from baseline {baseline_path}:\n{shown}{truncation}\n"
        f"If the change is intended and reviewed, regenerate the baseline manually with: {REGENERATE_COMMAND}"
    )
