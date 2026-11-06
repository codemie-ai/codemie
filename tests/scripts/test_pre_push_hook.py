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
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / "scripts" / "git-hooks" / "pre_push.sh"
ALEMBIC_SCRIPT = REPO_ROOT / "scripts" / "check_alembic_heads.py"

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
    env["ALEMBIC_CHECK_CMD"] = "true"  # the migration check has its own tests below
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
    result, calls = _run(tmp_path, env_overrides={"CODEMIE_PREPUSH_ENABLED": "false", **_real_alembic_check(tmp_path)})
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


def _migration_tree(repo: Path, heads: int) -> None:
    versions = repo / "alembic" / "versions"
    versions.mkdir(parents=True)
    (repo / "alembic" / "env.py").write_text("")
    (versions / "a_first.py").write_text('revision = "a"\ndown_revision = None\n')
    for i in range(heads):
        (versions / f"h{i}.py").write_text(f'revision = "h{i}"\ndown_revision = "a"\n')


def _real_alembic_check(repo: Path) -> dict:
    """ALEMBIC_CHECK_CMD that runs the real script with this interpreter (paths may contain spaces, so a wrapper)."""
    wrapper = repo / "alembic_check.sh"
    wrapper.write_text(
        "#!/usr/bin/env bash\n" f'exec "{Path(sys.executable).as_posix()}" "{ALEMBIC_SCRIPT.as_posix()}" "$@"\n'
    )
    wrapper.chmod(0o755)
    return {"ALEMBIC_CHECK_CMD": f"bash {wrapper.as_posix()}"}


def test_two_migration_heads_block_the_push_even_without_the_opt_in(tmp_path):
    _migration_tree(tmp_path, heads=2)
    result, calls = _run(tmp_path, env_overrides={"CODEMIE_PREPUSH_ENABLED": "false", **_real_alembic_check(tmp_path)})
    assert result.returncode == 1
    assert "expected exactly one head, found 2" in result.stdout
    assert "h0 (h0.py)" in result.stdout and "h1 (h1.py)" in result.stdout
    assert "push blocked" in result.stderr
    assert calls == ""


def test_one_migration_head_lets_the_push_through_without_the_opt_in(tmp_path):
    _migration_tree(tmp_path, heads=1)
    result, calls = _run(tmp_path, env_overrides={"CODEMIE_PREPUSH_ENABLED": "false", **_real_alembic_check(tmp_path)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "skipping heavy checks" in result.stdout
    assert calls == ""


def test_a_failing_migration_check_stops_before_pytest_and_sonar(tmp_path):
    result, calls = _run(tmp_path, env_overrides={"ALEMBIC_CHECK_CMD": "false"})
    assert result.returncode == 1
    assert calls == ""


def _bin_dir(repo: Path, broken: tuple[str, ...], working: tuple[str, ...] = ()) -> str:
    """A PATH entry with interpreters that fail (like the Windows Store `python3` placeholder) or work."""
    bin_dir = repo / "bin"
    bin_dir.mkdir()
    for name in broken:
        (bin_dir / name).write_text("#!/usr/bin/env bash\nexit 9009\n")
        (bin_dir / name).chmod(0o755)
    for name in working:
        (bin_dir / name).write_text(f'#!/usr/bin/env bash\nexec "{Path(sys.executable).as_posix()}" "$@"\n')
        (bin_dir / name).chmod(0o755)
    return f"{bin_dir.as_posix()}{os.pathsep}{os.environ.get('PATH', '')}"


def test_a_broken_python3_falls_back_to_python_and_still_checks(tmp_path):
    _migration_tree(tmp_path, heads=2)
    path = _bin_dir(tmp_path, broken=("python3",), working=("python",))
    result, _ = _run(
        tmp_path, env_overrides={"CODEMIE_PREPUSH_ENABLED": "false", "ALEMBIC_CHECK_CMD": "", "PATH": path}
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "expected exactly one head, found 2" in result.stdout


def test_no_working_python_blocks_the_push_with_a_clear_message(tmp_path):
    _migration_tree(tmp_path, heads=1)
    path = _bin_dir(tmp_path, broken=("python3", "python"))
    result, calls = _run(
        tmp_path, env_overrides={"CODEMIE_PREPUSH_ENABLED": "false", "ALEMBIC_CHECK_CMD": "", "PATH": path}
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "No working python3/python" in result.stderr and "push blocked" in result.stderr
    assert calls == ""
