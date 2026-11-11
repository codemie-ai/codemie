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

"""Tests for scripts/check_ruff_changed.py (EPMCDME-16990)."""

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_ruff_changed.py"
_spec = importlib.util.spec_from_file_location("check_ruff_changed", SCRIPT)
chk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(chk)

PYPROJECT = '[tool.ruff]\nline-length = 120\n\n[tool.ruff.lint.per-file-ignores]\n"legacy/*" = ["S"]\n'
# S324 (insecure hash) is in the strict set and not in a default ruff config
OLD_ISSUE = 'import hashlib\n\n\ndef digest(data):\n    return hashlib.md5(data).hexdigest()\n'
CLEAN = "def add(a: int, b: int) -> int:\n    return a + b\n"


def _git(repo: Path, *args: str) -> str:
    cmd = ["git", "-c", "user.name=t", "-c", "user.email=t@example.org", "-c", "commit.gpgsign=false", *args]
    return subprocess.run(cmd, cwd=repo, check=True, capture_output=True, text=True).stdout


def _write(repo: Path, path: str, text: str) -> None:
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A git repo whose `main` has one file with a strict-rule issue; the test branches off it as `feature`."""
    if shutil.which("ruff") is None or shutil.which("git") is None:
        pytest.skip("ruff and git must be on PATH")
    monkeypatch.delenv("RUFF_BIN", raising=False)
    _git(tmp_path, "init", "-q", "-b", "main")
    _write(tmp_path, "pyproject.toml", PYPROJECT)
    _write(tmp_path, "src/old.py", OLD_ISSUE)
    _write(tmp_path, "src/clean.py", CLEAN)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "checkout", "-q", "-b", "feature")
    return tmp_path


def _check(repo: Path) -> int:
    return chk.main(["--root", str(repo), "--base", "main"])


def test_a_new_issue_in_a_changed_file_fails_with_file_line_and_rule(repo, capsys):
    _write(repo, "src/clean.py", CLEAN + "\n\ndef run(cmd):\n    return eval(cmd)\n")
    _git(repo, "commit", "-qam", "eval")
    assert _check(repo) == 1
    out = capsys.readouterr().out
    assert "src/clean.py:6:12: S307" in out
    assert "1 new" in out


def test_an_issue_the_file_had_at_the_merge_base_does_not_fail_even_when_it_moves(repo, capsys):
    _write(repo, "src/old.py", '"""Docstring added above, the old issue moves down."""\n\n' + OLD_ISSUE)
    _git(repo, "commit", "-qam", "touch old")
    assert _check(repo) == 0
    assert "1 issue(s) now, 1 at the merge base, 0 new" in capsys.readouterr().out


def test_a_second_copy_of_an_existing_issue_counts_as_new(repo, capsys):
    _write(repo, "src/old.py", OLD_ISSUE + "\n\ndef digest2(data):\n    return hashlib.md5(data).digest()\n")
    _git(repo, "commit", "-qam", "copy")
    assert _check(repo) == 1
    assert "src/old.py:9:12: S324" in capsys.readouterr().out


def test_a_renamed_file_keeps_its_old_issues(repo):
    _git(repo, "mv", "src/old.py", "src/renamed.py")
    _git(repo, "commit", "-qm", "rename")
    assert _check(repo) == 0


def test_a_new_file_is_checked_and_uncommitted_edits_count(repo, capsys):
    _write(repo, "src/new.py", "import subprocess\n\n\ndef run(cmd):\n    return subprocess.call(cmd, shell=True)\n")
    _git(repo, "add", "src/new.py")  # staged, not committed: the working tree is what is checked
    assert _check(repo) == 1
    assert "src/new.py:5:12: S602" in capsys.readouterr().out


def test_tests_non_python_files_and_per_file_ignores_are_skipped(repo, capsys):
    _write(repo, "tests/test_x.py", "def test_x():\n    assert eval('1') == 1\n")
    _write(repo, "legacy/run.py", "def run(cmd):\n    return eval(cmd)\n")
    _write(repo, "notes.md", "eval\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "skipped")
    assert _check(repo) == 0
    assert "on 1 changed Python file(s)" in capsys.readouterr().out  # legacy/run.py; tests/ is not even counted


def test_no_changed_python_file_passes(repo, capsys):
    assert _check(repo) == 0
    assert "on 0 changed Python file(s)" in capsys.readouterr().out


def test_an_unknown_base_is_a_usage_error(repo):
    assert chk.main(["--root", str(repo), "--base", "no-such-branch"]) == 2


def test_merge_base_option_skips_computing_it(repo):
    base = _git(repo, "rev-parse", "HEAD").strip()
    _write(repo, "src/clean.py", CLEAN + "\n\ndef run(cmd):\n    return eval(cmd)\n")
    _git(repo, "commit", "-qam", "eval")
    assert chk.main(["--root", str(repo), "--base", "no-such-branch", "--merge-base", base]) == 1


def test_pinned_ruff_version_is_read_from_poetry_lock(tmp_path, capsys):
    lock = '[[package]]\nname = "ruff"\nversion = "0.5.7"\ndescription = "x"\n'
    (tmp_path / "poetry.lock").write_text(lock, encoding="utf-8")
    assert chk.main(["--root", str(tmp_path), "--pinned-ruff-version"]) == 0
    assert capsys.readouterr().out.strip() == "0.5.7"
    (tmp_path / "poetry.lock").write_text("", encoding="utf-8")
    assert chk.main(["--root", str(tmp_path), "--pinned-ruff-version"]) == 2


def test_new_issues_matches_by_rule_and_message_not_line():
    def issue(path, code, row, message="m"):
        return {"_path": path, "code": code, "message": message, "location": {"row": row}}

    base = [issue("a.py", "S1", 1), issue("old.py", "S2", 3)]
    head = [issue("a.py", "S1", 10), issue("a.py", "S1", 20), issue("new.py", "S2", 3), issue("a.py", "S3", 1)]
    found = chk.new_issues(base, head, {"old.py": "new.py"})
    assert [(i["_path"], i["code"], i["location"]["row"]) for i in found] == [("a.py", "S3", 1), ("a.py", "S1", 20)]
