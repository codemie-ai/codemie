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

"""Refactor guard: pre_commit.sh must no longer run pytest / sonar (EPMCDME-13747)."""

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / "scripts" / "git-hooks" / "pre_commit.sh"

PASS_STUB = "#!/usr/bin/env bash\nexit 0\n"


def _run_fast_path(tmp_path: Path):
    repo = tmp_path / "scratch"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    # Stage a non-python file: _ruff_staged.sh short-circuits (no staged .py),
    # then pre_commit.sh runs ruff check + license via stubs below.
    (repo / "notes.md").write_text("hello\n")
    subprocess.run(["git", "add", "--", "notes.md"], cwd=repo, check=True)

    stub = repo / "pass.sh"
    stub.write_text(PASS_STUB)
    stub.chmod(0o755)

    env = os.environ.copy()
    env["RUFF_CMD"] = f"bash {stub}"
    env["LICENSE_CMD"] = f"bash {stub}"
    return subprocess.run(
        ["bash", str(HOOK)],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_fast_path_passes_without_heavy_steps(tmp_path):
    result = _run_fast_path(tmp_path)
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    # These strings only appear when sonar actually runs, not in status messages
    assert "sonar-scanner" not in combined
    assert "make sonar-local" not in combined


def test_source_has_no_pytest_or_sonar_invocation():
    text = HOOK.read_text()
    # Assert no invocation lines (allow comments that mention these tools)
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "make sonar-local" not in stripped, f"Found sonar invocation: {line}"
        assert "pytest" not in stripped, f"Found pytest invocation: {line}"
