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

"""Fixtures for scripts/ test suite (EPMCDME-13740)."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER_SCRIPT = REPO_ROOT / "scripts" / "git-hooks" / "_ruff_staged.sh"

MINIMAL_PYPROJECT = """\
[tool.ruff]
line-length = 120

[tool.ruff.lint]
select = ["E", "F"]
"""


def _run(cmd, cwd, env=None, check=True):
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, check=check)


@pytest.fixture
def staged_repo(tmp_path):
    """Initialize a scratch git repo with minimal ruff config.

    Returns the repo path. Callers stage files, then invoke run_helper().
    """
    if shutil.which("ruff") is None:
        pytest.skip("ruff not on PATH - run via `poetry run pytest`")
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")

    repo = tmp_path / "scratch"
    repo.mkdir()
    _run(["git", "init", "--quiet", "--initial-branch=main"], cwd=repo)
    _run(["git", "config", "user.email", "test@example.com"], cwd=repo)
    _run(["git", "config", "user.name", "Test User"], cwd=repo)
    _run(["git", "config", "commit.gpgsign", "false"], cwd=repo)
    (repo / "pyproject.toml").write_text(MINIMAL_PYPROJECT)
    return repo


@pytest.fixture
def run_helper():
    """Return a callable that invokes _ruff_staged.sh in a given repo."""

    def _invoke(repo: Path, env_overrides=None):
        env = os.environ.copy()
        env["RUFF_CMD"] = "ruff"
        if env_overrides:
            env.update(env_overrides)
        return subprocess.run(
            ["bash", str(HELPER_SCRIPT)],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    return _invoke
