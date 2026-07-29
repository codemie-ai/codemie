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

"""Subprocess tests for scripts/git-hooks/_ruff_staged.sh (EPMCDME-13740)."""

import subprocess


UNFORMATTED = "x   =   1\ny=2\n"
FORMATTED = "x = 1\ny = 2\n"


def _stage(repo, path: str, content: str):
    file = repo / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(content)
    subprocess.run(["git", "add", "--", path], cwd=repo, check=True)


def test_fixture_yields_git_repo(staged_repo):
    """staged_repo fixture must return a Path to an initialized git repo."""
    proc = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=staged_repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == "true"


def test_no_staged_py_files_exits_zero(staged_repo, run_helper):
    """When nothing Python is staged, the helper is a no-op (exit 0)."""
    (staged_repo / "notes.md").write_text("just markdown\n")
    subprocess.run(["git", "add", "--", "notes.md"], cwd=staged_repo, check=True)

    result = run_helper(staged_repo)

    assert result.returncode == 0, result.stderr
    assert "skipping Ruff" in result.stdout or "No staged" in result.stdout


def test_clean_staged_plus_unstaged_garbage_exits_zero(staged_repo, run_helper):
    """Primary bug fix: unrelated unstaged mods must not trigger abort."""
    _stage(staged_repo, "clean.py", FORMATTED)
    (staged_repo / "garbage.py").write_text(UNFORMATTED)

    result = run_helper(staged_repo)

    assert result.returncode == 0, (
        f"Helper aborted despite staged file being clean.\n" f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert (staged_repo / "garbage.py").read_text() == UNFORMATTED


def test_ruff_reformats_staged_file_exits_one(staged_repo, run_helper):
    """When ruff would change staged content, helper exits 1 with re-add message.

    The working tree file is NOT mutated - the helper only reports what needs
    to change. This is intentional: mutating the working tree would clobber
    unstaged edits that share the file (the git add -p footgun).
    """
    _stage(staged_repo, "bad.py", UNFORMATTED)

    result = run_helper(staged_repo)

    assert result.returncode == 1, result.stdout
    assert "bad.py" in result.stdout
    assert "ruff format" in result.stdout
    # Working-tree file untouched - user runs ruff format themselves.
    assert (staged_repo / "bad.py").read_text() == UNFORMATTED


def test_missing_trailing_newline_exits_one(staged_repo, run_helper):
    """CR-001 regression: staged file missing trailing newline must be flagged.

    Command substitution `$(...)` strips trailing newlines, so an earlier
    implementation that captured `orig=$(git show :f)` and
    `fixed=$(...ruff...)` silently equalized both to newline-less strings and
    let the file through. The helper now uses temp files + `cmp -s` so
    trailing-newline differences are detected.
    """
    file = staged_repo / "no_newline.py"
    file.write_bytes(b"x = 1")  # no trailing newline
    subprocess.run(["git", "add", "--", "no_newline.py"], cwd=staged_repo, check=True)

    result = run_helper(staged_repo)

    assert result.returncode == 1, (
        f"Missing-newline staged file passed hook (CR-001 regression).\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "no_newline.py" in result.stdout


def test_syntax_error_in_staged_file_reports_clearly(staged_repo, run_helper):
    """CR-002 regression: syntax error must produce clear message, not generic ERR trap."""
    _stage(staged_repo, "broken.py", "def f(x\n")

    result = run_helper(staged_repo)

    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "broken.py" in combined
    assert "Ruff format failed" in combined, (
        f"Expected explicit 'Ruff format failed' message.\n" f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_partial_staging_clean_hunk_dirty_unstaged_exits_zero(staged_repo, run_helper):
    """git add -p scenario: staged content clean, unstaged content dirty -> exit 0."""
    file = staged_repo / "partial.py"
    file.write_text(FORMATTED)
    subprocess.run(["git", "add", "--", "partial.py"], cwd=staged_repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "seed"], cwd=staged_repo, check=True)
    file.write_text(FORMATTED + "z = 3\n")
    subprocess.run(["git", "add", "--", "partial.py"], cwd=staged_repo, check=True)
    file.write_text(FORMATTED + "z = 3\n" + UNFORMATTED)

    result = run_helper(staged_repo)

    assert result.returncode == 0, (
        f"Helper aborted despite staged content being clean (partial-stage bug).\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert UNFORMATTED in file.read_text()
