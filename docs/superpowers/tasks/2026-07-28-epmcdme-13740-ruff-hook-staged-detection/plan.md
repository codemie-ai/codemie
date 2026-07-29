# EPMCDME-13740: pre-commit hook staged-files fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `scripts/git-hooks/pre_commit.sh` so its "ruff applied changes" detection sees only ruff's changes on staged Python files, not any unstaged edit; also cover the fix with a pytest subprocess test and unblock the deprecated `stages: [commit]` in `.pre-commit-config.yaml`.

**Architecture:** Extract the ruff-staged-files block from `pre_commit.sh` into a small helper `scripts/git-hooks/_ruff_staged.sh` that the main hook invokes as a subprocess. The helper reads staged Python files via `git diff --cached`, hashes them with `git hash-object` before/after ruff runs, and exits 1 only when ruff actually mutated staged content. Because the helper is a standalone bash script, pytest can drive it in a scratch git repo without triggering license/pytest/sonar. The helper uses `${RUFF_CMD:-poetry run ruff}` so tests can inject `ruff` directly without poetry.

**Tech Stack:** bash 3.2 (macOS-compatible), ruff `^0.5.4` via `poetry run` in prod / `ruff` directly in tests, pytest 8.x + subprocess in `tests/scripts/`.

## Global Constraints

- **bash 3.2 compatible** — hook runs via `bash -lc` on macOS. No `mapfile`, no associative arrays, no `readarray`.
- **Apache 2.0 license header required** on any new `.sh` or `.py` file (see existing hook header lines 1–14 for the exact block).
- **Ticket-prefixed commit messages**: `EPMCDME-13740: <Short description>` per `.ai-run/guides/standards/git-workflow.md`.
- **Hook behavior stays identical** for non-ruff sections (license headers, pytest, sonar). No new dependencies, no changes to `pyproject.toml`, no changes to ruff config.
- **`--force-exclude` mandatory** wherever ruff is called with explicit paths — otherwise pyproject `exclude = [".agents", ".claude"]` is bypassed.
- **Design source of truth**: `/Users/oleg_sotnichenko/codemie-dev/docs/backend-hooks-improvement-plan.md` §1 (verified locally 2026-07-27, then reverted). Reuse that snippet.

---

## File Structure

- Create: `scripts/git-hooks/_ruff_staged.sh` — standalone helper, ~55 lines including Apache header.
- Modify: `scripts/git-hooks/pre_commit.sh:43-58` — replace the broken block with a single call to the helper.
- Modify: `.pre-commit-config.yaml:9` — `stages: [commit]` → `stages: [pre-commit]`.
- Create: `tests/scripts/__init__.py` — empty file to make the dir a package.
- Create: `tests/scripts/conftest.py` — pytest fixture `staged_repo` returning a scratch git repo (Path).
- Create: `tests/scripts/test_ruff_staged_hook.py` — 4 subprocess test cases against the helper.

Rationale for the helper split: the current `pre_commit.sh` embeds ruff/license/pytest/sonar in one 116-line script that is impossible to unit-test without a full poetry env. Factoring the 30-line ruff-staged block out lets pytest exercise the exact behavior in isolation (empty staged list, clean staged file, mutated staged file, partial-staging bug scenario) using `RUFF_CMD=ruff` — no poetry, no license checker, no sonar.

---

### Task 1: Scratch-repo pytest fixture

**Files:**
- Create: `tests/scripts/__init__.py`
- Create: `tests/scripts/conftest.py`
- Create: `tests/scripts/test_ruff_staged_hook.py` (smoke test only in this task)

**Interfaces:**
- Consumes: pytest 8.x `tmp_path` fixture, `subprocess.run`.
- Produces:
  - `staged_repo(tmp_path) -> Path` — pytest fixture yielding a `Path` to an initialized git repo with a minimal `pyproject.toml` containing `[tool.ruff] line-length = 120` and `[tool.ruff.lint] select = ["E", "F", "I"]`. `user.name`/`user.email` are set locally so commits work if needed. The fixture also sets `RUFF_CMD=ruff` in a returned env dict.
  - `run_helper(repo: Path, env_overrides: dict | None = None) -> subprocess.CompletedProcess` — helper invoker: `subprocess.run(["bash", "<repo-root>/scripts/git-hooks/_ruff_staged.sh"], cwd=repo, env=..., capture_output=True, text=True)`. Note: `<repo-root>` here is the codemie repo root (where the script lives), NOT the scratch repo; find it via `Path(__file__).resolve().parents[2]`.

**Test-first: yes** — smoke test asserting the fixture yields a directory that `git rev-parse` recognizes as a repo will fail because the fixture does not exist yet.

- [ ] **Step 1: Write the failing smoke test**

Create `tests/scripts/__init__.py` (empty).

Create `tests/scripts/test_ruff_staged_hook.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```
poetry run pytest tests/scripts/test_ruff_staged_hook.py::test_fixture_yields_git_repo -v
```

Expected: FAIL with `fixture 'staged_repo' not found`.

- [ ] **Step 3: Implement conftest.py**

Create `tests/scripts/conftest.py`:

```python
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
    return subprocess.run(
        cmd, cwd=cwd, env=env, capture_output=True, text=True, check=check
    )


@pytest.fixture
def staged_repo(tmp_path):
    """Initialize a scratch git repo with minimal ruff config.

    Returns the repo path. Callers stage files, then invoke run_helper().
    """
    if shutil.which("ruff") is None:
        pytest.skip("ruff not on PATH — run via `poetry run pytest`")
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
```

- [ ] **Step 4: Run test to verify it passes**

```
poetry run pytest tests/scripts/test_ruff_staged_hook.py::test_fixture_yields_git_repo -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/scripts/__init__.py tests/scripts/conftest.py tests/scripts/test_ruff_staged_hook.py
git commit -m "EPMCDME-13740: Add scratch-repo pytest fixture for pre-commit hook tests"
```

---

### Task 2: `_ruff_staged.sh` helper (test-driven)

**Files:**
- Create: `scripts/git-hooks/_ruff_staged.sh` (permission 755)
- Modify: `tests/scripts/test_ruff_staged_hook.py` (add 4 scenario tests + helpers)

**Interfaces:**
- Consumes: `run_helper`, `staged_repo` fixtures from Task 1.
- Produces:
  - `scripts/git-hooks/_ruff_staged.sh` — bash 3.2 script. Reads `RUFF_CMD` env (default `poetry run ruff`). Uses `set -euo pipefail`. Exit codes: `0` if no staged .py OR ruff produced no changes; `1` if ruff mutated at least one staged file (with message listing changed files). Non-zero ruff exit from `ruff check --fix` is swallowed (matches current hook behavior — lint violations don't block the commit, formatting/fix changes do).

**Test-first: yes** — 4 subprocess scenarios covering the primary bug (`git add -p` false abort), the standard "already clean" pass, the "ruff reformatted staged" abort, and the "no staged .py" short-circuit. All 4 will fail because the helper does not exist.

- [ ] **Step 1: Append the 4 failing scenarios to `test_ruff_staged_hook.py`**

```python
# Append to tests/scripts/test_ruff_staged_hook.py

UNFORMATTED = "x   =   1\ny=2\n"       # ruff reformats
FORMATTED = "x = 1\ny = 2\n"           # ruff leaves alone


def _stage(repo, path: str, content: str):
    file = repo / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(content)
    subprocess.run(["git", "add", "--", path], cwd=repo, check=True)


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
    (staged_repo / "garbage.py").write_text(UNFORMATTED)  # unstaged, untracked

    result = run_helper(staged_repo)

    assert result.returncode == 0, (
        f"Helper aborted despite staged file being clean.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    # Unstaged file must be untouched by ruff.
    assert (staged_repo / "garbage.py").read_text() == UNFORMATTED


def test_ruff_reformats_staged_file_exits_one(staged_repo, run_helper):
    """When ruff mutates a staged file, helper exits 1 with re-add message."""
    _stage(staged_repo, "bad.py", UNFORMATTED)

    result = run_helper(staged_repo)

    assert result.returncode == 1, result.stdout
    assert "bad.py" in result.stdout
    assert "git add" in result.stdout
    # Working-tree copy was reformatted by ruff.
    assert (staged_repo / "bad.py").read_text() == FORMATTED


def test_partial_staging_clean_hunk_dirty_unstaged_exits_zero(staged_repo, run_helper):
    """git add -p scenario: staged hunk clean, unstaged hunk dirty -> exit 0."""
    file = staged_repo / "partial.py"
    file.write_text(FORMATTED)
    subprocess.run(["git", "add", "--", "partial.py"], cwd=staged_repo, check=True)
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "seed"], cwd=staged_repo, check=True
    )
    # Now add a clean line (staged) AND a dirty line (unstaged).
    file.write_text(FORMATTED + "z = 3\n" + UNFORMATTED)
    # Stage only the middle clean line via a targeted diff.
    subprocess.run(
        ["git", "add", "--", "partial.py"], cwd=staged_repo, check=True
    )
    # Reset the last two lines out of the index so they stay unstaged.
    subprocess.run(
        ["git", "restore", "--staged", "--", "partial.py"], cwd=staged_repo, check=True
    )
    # Re-stage just the "z = 3" addition by rewriting index via apply --cached.
    # Simpler: stage the whole clean part, then modify the working tree to add garbage.
    file.write_text(FORMATTED + "z = 3\n")
    subprocess.run(
        ["git", "add", "--", "partial.py"], cwd=staged_repo, check=True
    )
    file.write_text(FORMATTED + "z = 3\n" + UNFORMATTED)

    result = run_helper(staged_repo)

    assert result.returncode == 0, (
        f"Helper aborted despite staged content being clean (partial-stage bug).\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    # Ruff should NOT have touched the unstaged garbage in the working tree.
    assert UNFORMATTED in file.read_text()
```

- [ ] **Step 2: Run tests to verify all 4 fail**

```
poetry run pytest tests/scripts/test_ruff_staged_hook.py -v
```

Expected: 4 FAIL (helper script missing → bash exits non-zero with "No such file or directory"). The `test_fixture_yields_git_repo` from Task 1 still PASSes.

- [ ] **Step 3: Implement the helper**

Create `scripts/git-hooks/_ruff_staged.sh` (executable, mode 755):

```bash
#!/usr/bin/env bash
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

# Ruff format/fix pass restricted to staged Python files.
# Exit 0 = no staged .py OR ruff produced no changes.
# Exit 1 = ruff mutated at least one staged .py (user must re-stage).
#
# Sourced/invoked from scripts/git-hooks/pre_commit.sh. RUFF_CMD env overrides
# the ruff invocation prefix (default "poetry run ruff") for testing.

set -euo pipefail

RUFF_CMD="${RUFF_CMD:-poetry run ruff}"

staged_py_files=$(git diff --cached --name-only --diff-filter=ACMR -- '*.py')

if [[ -z "$staged_py_files" ]]; then
  echo "[pre-commit] No staged Python files - skipping Ruff format/fix pass."
  exit 0
fi

hash_staged_py() {
  while IFS= read -r f; do
    [[ -f "$f" ]] || continue
    printf '%s %s\n' "$(git hash-object -- "$f")" "$f"
  done <<< "$staged_py_files"
}

echo "[pre-commit] Running Ruff format/fix on staged Python files..."
before_hashes=$(hash_staged_py)
# shellcheck disable=SC2086
printf '%s\n' "$staged_py_files" | tr '\n' '\0' | xargs -0 $RUFF_CMD format --force-exclude --
# shellcheck disable=SC2086
printf '%s\n' "$staged_py_files" | tr '\n' '\0' | xargs -0 $RUFF_CMD check --fix --force-exclude -- || true
after_hashes=$(hash_staged_py)
changed_files=$(comm -13 <(sort <<< "$before_hashes") <(sort <<< "$after_hashes") | cut -d' ' -f2- || true)

if [[ -n "$changed_files" ]]; then
  echo "[pre-commit] Ruff applied changes to the following files:"
  echo "$changed_files"
  echo "[pre-commit] Please stage the changes (git add ...) and commit again."
  echo "[pre-commit] Skipping tests now to avoid running them twice."
  exit 1
fi

exit 0
```

Then make it executable:

```bash
chmod +x scripts/git-hooks/_ruff_staged.sh
```

- [ ] **Step 4: Run tests to verify all 4 pass**

```
poetry run pytest tests/scripts/test_ruff_staged_hook.py -v
```

Expected: all 5 tests PASS (1 from Task 1 + 4 new).

- [ ] **Step 5: Commit**

```bash
git add scripts/git-hooks/_ruff_staged.sh tests/scripts/test_ruff_staged_hook.py
git commit -m "EPMCDME-13740: Implement staged-only ruff helper with hash-object detection"
```

---

### Task 3: Wire the helper into `pre_commit.sh` + fix stages deprecation

**Files:**
- Modify: `scripts/git-hooks/pre_commit.sh:43-58` — replace the broken block.
- Modify: `.pre-commit-config.yaml:9` — one-line YAML edit.

**Interfaces:**
- Consumes: `scripts/git-hooks/_ruff_staged.sh` (Task 2 deliverable).
- Produces: no new interface; behavioral change only.

**Test-first: no** — this is a straight refactor: the helper is already tested in isolation (Task 2), and the top-level hook has no test harness (would require poetry+license+pytest+sonar). Validation is (a) helper tests still green, (b) manual smoke via reproduction scenario in the "Manual validation" section below. Not adding a shell-driven end-to-end test for the full hook is intentional — it would require mocking poetry, license_headers, pytest, and sonar for zero incremental coverage over Task 2's tests.

- [ ] **Step 1: Replace the broken block in `pre_commit.sh`**

Open `scripts/git-hooks/pre_commit.sh`. Replace lines 43–58 (the entire `# --- 1. Ruff formatting and fixes (fast pass) ---` section through the closing `fi` of the `changed_files` check) with:

```bash
# --- 1. Ruff formatting and fixes (fast pass, staged Python files only) ---
# The staged-only detection lives in _ruff_staged.sh so it is unit-testable.
# It exits 1 if ruff mutated at least one staged file, 0 otherwise.
bash "$(dirname "$0")/_ruff_staged.sh"
```

Do NOT change the comment header (lines 1–22), the trap on line 24, the enable-check (26–34), the poetry check (37–41), or anything from the "# --- 2. Full verification" comment onward.

- [ ] **Step 2: Verify the file still parses and `chmod +x` survived**

```
bash -n scripts/git-hooks/pre_commit.sh
test -x scripts/git-hooks/pre_commit.sh && test -x scripts/git-hooks/_ruff_staged.sh && echo OK
```

Expected: `OK` printed, no parse errors.

- [ ] **Step 3: Re-run the helper tests to confirm nothing regressed**

```
poetry run pytest tests/scripts/test_ruff_staged_hook.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 4: Fix `stages: [commit]` → `stages: [pre-commit]`**

Edit `.pre-commit-config.yaml` line 9:

```yaml
        stages: [pre-commit]
```

Verify:

```
grep -n "stages:" .pre-commit-config.yaml
```

Expected: `9:        stages: [pre-commit]`.

- [ ] **Step 5: Commit**

```bash
git add scripts/git-hooks/pre_commit.sh .pre-commit-config.yaml
git commit -m "EPMCDME-13740: Wire _ruff_staged helper into pre_commit and fix stages deprecation"
```

---

## Manual validation (post-implementation, before MR)

The hook is currently disabled locally via `CODEMIE_PRECOMMIT_ENABLED=false` in `.env`. To reproduce the fix, temporarily flip it and walk through the primary bug scenario:

1. **Enable the hook:**
   ```bash
   # From repo root; edit .env or export inline for one shell:
   CODEMIE_PRECOMMIT_ENABLED=true
   poetry run pre-commit install    # only needed once
   ```

2. **Reproduce the `git add -p` false-abort bug on the OLD hook (control):**
   Check out `main`, pick any `.py` file, add two edits: one clean (e.g. add a docstring line), one deliberately unformatted (`x   =   1` style). Run `git add -p` and stage only the clean hunk. Attempt `git commit -m "test"`. Expected on OLD hook: aborts with "Ruff applied changes". This is the bug.

3. **Verify the fix on this branch:**
   Return to `EPMCDME-13740_ruff-hook-staged-detection`. Repeat step 2's staging. Attempt commit. Expected: commit proceeds past the ruff section (may still fail downstream on license/pytest/sonar — that's unrelated). The unstaged unformatted hunk in the working tree is left untouched.

4. **Confirm the "genuine mutation" path still aborts:**
   Stage a `.py` file whose staged content is unformatted (`git add <file>` on a file with `x   =   1`). Attempt commit. Expected: hook exits 1 with "Ruff applied changes to the following files: \<file\>" and instructions to `git add` + re-commit.

5. **Confirm the "no staged .py" short-circuit:**
   Stage only a non-Python file (e.g. `git add README.md`). Attempt commit. Expected: hook prints "No staged Python files - skipping Ruff format/fix pass" and proceeds.

6. **Restore your local `.env`:**
   ```bash
   CODEMIE_PRECOMMIT_ENABLED=false
   ```
   (This is the workspace default per prior local convention.)

Document steps 1 and 6 in the MR description so a reviewer running the hook on their own machine does not commit an `.env` change by accident.

---

## Self-review

**Spec coverage:**
- Requirement 1 (all bullets: staged-only, early skip, hash-object detection, `--force-exclude`, preserve abort message, rest of hook untouched, bash 3.2) — covered by Task 2 helper + Task 3 wiring. Bash 3.2 verified by using `while IFS= read -r`, `<<<`, no `mapfile`/assoc.
- Requirement 2 (stages deprecation) — Task 3 Step 4.
- Requirement 3 (pytest subprocess test with 4 scenarios) — Task 1 (fixture) + Task 2 (all 4 scenarios). Runnable via `poetry run pytest tests/scripts/test_ruff_staged_hook.py`.

**Placeholder scan:** none — all steps have concrete code blocks and exact commands.

**Type consistency:** N/A (bash + pytest); the two Python fixtures `staged_repo` and `run_helper` are referenced with matching names between conftest.py (defined) and test file (consumed).

**Risk mitigation:**
- `${RUFF_CMD:-poetry run ruff}` env override lets tests run without poetry and keeps prod behavior identical.
- Helper is invoked as a subprocess (`bash "$(dirname "$0")/_ruff_staged.sh"`) rather than sourced, so any `set -e` state leak between the two scripts is impossible.
- License headers copied verbatim from the existing `pre_commit.sh:1-14` (Apache 2.0) — passes `make license-check`.
- `stages: [pre-commit]` change is one line, YAML parses identically for pre-commit ≥3.2.
