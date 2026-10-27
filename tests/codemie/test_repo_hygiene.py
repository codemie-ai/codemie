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
"""Repository invariants for the marketplace management feature.

The whole feature tree shipped as untracked files, which meant it was neither
reviewable, revertable nor covered by CI. These tests keep that from recurring.
"""

import pathlib
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

TRACKED_TREES = [
    "src/codemie/service/marketplace_management",
    "src/codemie/rest_api/routers/marketplace_management.py",
    "src/codemie/configs/marketplace_management.py",
    "src/codemie/templates/agents/marketplace_assessor_prompt.py",
    "tests/codemie/service/marketplace_management",
    # These two were missing, and the gap was not theoretical: a hardening wave
    # left five files untracked — including its own alembic revision — and this
    # guard caught only three of them. A migration that is not committed is a
    # schema change nobody can review, revert or replay.
    "src/external/alembic/versions",
    "tests/codemie/rest_api/routers",
    "tests/codemie/marketplace_fixtures",
    # Widened a second time, for a sharper reason than the first. A later wave
    # left tests/codemie/external/alembic/_alembic_worker.py untracked — a
    # helper spawned as a subprocess by 35 *tracked* tests. Those tests pass
    # locally and fail on a fresh clone, which is the worst failure mode a
    # suite can have: green for the author, red for everyone else.
    "tests/codemie/external",
]


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(REPO_ROOT), *args],
        text=True,
        stderr=subprocess.STDOUT,
    )


@pytest.mark.parametrize("tree", TRACKED_TREES)
def test_marketplace_feature_is_tracked(tree: str) -> None:
    """Every marketplace management path is committed, not left untracked."""
    tracked = [line for line in _git("ls-files", "--", tree).splitlines() if line]

    assert tracked, (
        f"{tree!r} is not tracked by git. The marketplace management feature must be "
        f"committed so it is reviewable, revertable and covered by CI."
    )


def test_no_untracked_files_under_marketplace_trees() -> None:
    """No stray untracked file hides inside an otherwise-committed tree."""
    untracked = [
        line for line in _git("ls-files", "--others", "--exclude-standard", "--", *TRACKED_TREES).splitlines() if line
    ]

    assert not untracked, "Untracked files found under the marketplace management trees:\n  " + "\n  ".join(untracked)


def test_alembic_revisions_for_marketplace_are_tracked() -> None:
    """The squashed marketplace management migration is tracked by git."""
    for revision in ("f3e2d1c0b9a8_marketplace_management_squashed",):
        tracked = [
            line for line in _git("ls-files", "--", f"src/external/alembic/versions/{revision}.py").splitlines() if line
        ]
        assert tracked, f"alembic revision {revision!r} is not tracked by git"
