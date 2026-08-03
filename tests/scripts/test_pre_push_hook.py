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

"""Subprocess tests for scripts/git-hooks/pre_push.sh (EPMCDME-13747)."""

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / "scripts" / "git-hooks" / "pre_push.sh"

STUB_TEMPLATE = """#!/usr/bin/env bash
echo "$(basename "$0"): $@" >> "{log}"
exit {code}
"""


def _make_stub(repo: Path, name: str, log: Path, code: int) -> str:
    stub = repo / name
    stub.write_text(STUB_TEMPLATE.format(log=log, code=code))
    stub.chmod(0o755)
    return str(stub)


def _run(repo: Path, pytest_code=0, sonar_code=0, env_overrides=None):
    log = repo / "calls.log"
    pytest_stub = _make_stub(repo, "pytest_stub.sh", log, pytest_code)
    sonar_stub = _make_stub(repo, "sonar_stub.sh", log, sonar_code)
    env = os.environ.copy()
    env["PYTEST_CMD"] = f"bash {pytest_stub}"
    env["SONAR_CMD"] = f"bash {sonar_stub}"
    env["CODEMIE_PREPUSH_ENABLED"] = "true"
    if env_overrides:
        env.update(env_overrides)
    result = subprocess.run(
        ["bash", str(HOOK)],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    calls = log.read_text() if log.exists() else ""
    return result, calls


def test_passes_when_pytest_and_sonar_pass(tmp_path):
    result, calls = _run(tmp_path, pytest_code=0, sonar_code=0)
    assert result.returncode == 0, result.stdout + result.stderr


def test_pytest_receives_enterprise_ignore(tmp_path):
    _, calls = _run(tmp_path, pytest_code=0, sonar_code=0)
    assert "--ignore=tests/enterprise/" in calls


def test_pytest_failure_blocks_and_skips_sonar(tmp_path):
    result, calls = _run(tmp_path, pytest_code=1, sonar_code=0)
    assert result.returncode != 0
    # sonar stub must NOT have run
    assert "--ignore=tests/enterprise/" in calls
    assert "sonar_stub.sh" not in calls


def test_sonar_failure_blocks_push(tmp_path):
    result, _ = _run(tmp_path, pytest_code=0, sonar_code=1)
    assert result.returncode != 0


def test_skipped_when_not_enabled(tmp_path):
    result, calls = _run(tmp_path, env_overrides={"CODEMIE_PREPUSH_ENABLED": "false"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == ""


def test_numeric_one_enabled_runs(tmp_path):
    result, calls = _run(tmp_path, pytest_code=0, sonar_code=0, env_overrides={"CODEMIE_PREPUSH_ENABLED": "1"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--ignore=tests/enterprise/" in calls


def test_on_enabled_runs(tmp_path):
    result, calls = _run(tmp_path, pytest_code=0, sonar_code=0, env_overrides={"CODEMIE_PREPUSH_ENABLED": "on"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--ignore=tests/enterprise/" in calls


def test_legacy_precommit_enabled_runs_and_warns(tmp_path):
    result, calls = _run(
        tmp_path,
        pytest_code=0,
        sonar_code=0,
        env_overrides={
            "CODEMIE_PREPUSH_ENABLED": "false",  # overridden by legacy var
            "CODEMIE_PRECOMMIT_ENABLED": "true",
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--ignore=tests/enterprise/" in calls
    assert "DEPRECATION" in (result.stdout + result.stderr)
    assert "CODEMIE_PREPUSH_ENABLED" in (result.stdout + result.stderr)
