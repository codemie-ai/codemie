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

"""Tests for scripts/check_poetry_lock.py (EPMCDME-16990)."""

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_poetry_lock.py"
_spec = importlib.util.spec_from_file_location("check_poetry_lock", SCRIPT)
chk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(chk)

PYPROJECT = """\
[project]
name = "lock-demo"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = []

[tool.poetry]
package-mode = false
"""


@pytest.fixture
def locked_project(tmp_path):
    """A tiny Poetry project with no dependencies and a fresh poetry.lock (no index access needed)."""
    poetry = shutil.which("poetry")
    if poetry is None:
        pytest.skip("poetry not on PATH")
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    env = {**os.environ, "POETRY_CACHE_DIR": str(tmp_path / ".cache"), "POETRY_VIRTUALENVS_CREATE": "false"}
    subprocess.run([poetry, "lock", "--no-interaction", "--directory", str(tmp_path)], check=True, env=env)
    return tmp_path


def test_a_fresh_lock_passes(locked_project, capsys):
    assert chk.main(["--root", str(locked_project)]) == 0
    assert "matches pyproject.toml" in capsys.readouterr().out


def test_a_stale_lock_fails_and_says_how_to_fix_it(locked_project, capsys):
    pyproject = locked_project / "pyproject.toml"
    pyproject.write_text(PYPROJECT.replace("dependencies = []", 'dependencies = ["requests>=2"]'), encoding="utf-8")
    assert chk.main(["--root", str(locked_project)]) == 1
    out = capsys.readouterr().out
    assert "does not match pyproject.toml" in out
    assert "Run `poetry lock`" in out


def test_a_missing_lock_fails(tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    assert chk.main(["--root", str(tmp_path)]) == 1
    assert "poetry.lock does not exist" in capsys.readouterr().out


def test_no_poetry_is_a_usage_error(tmp_path, monkeypatch):
    monkeypatch.delenv("POETRY_BIN", raising=False)
    monkeypatch.setattr(chk.shutil, "which", lambda _name: None)
    assert chk.main(["--root", str(tmp_path)]) == 2


def test_only_poetry_errors_are_reported_not_its_deprecation_warnings():
    output = (
        "Warning: [tool.poetry.description] is deprecated. Use [project.description] instead.\n"
        "If you want to define multiple readmes, you should define them in [tool.poetry].\n"
        "Error: pyproject.toml changed significantly since poetry.lock was last generated. "
        "Run `poetry lock` to fix the lock file.\n"
    )
    assert chk.poetry_errors(output) == [output.splitlines()[2]]


def test_poetry_runs_without_project_plugins(tmp_path, monkeypatch):
    """The MR's pyproject.toml can name Poetry plugins ([tool.poetry.requires-plugins]); the check must not load them."""
    (tmp_path / "poetry.lock").write_text("", encoding="utf-8")
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(chk.subprocess, "run", fake_run)
    assert chk.check(tmp_path, "poetry") == (True, [])
    assert "--no-plugins" in calls[0]
