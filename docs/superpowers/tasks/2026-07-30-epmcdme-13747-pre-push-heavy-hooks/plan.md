# EPMCDME-13747 — Move pytest + sonar-local to pre-push; add commit-msg enforcement — Implementation Plan

> **For agentic workers:** This plan is executed inline via `superpowers:test-driven-development` under `sdlc-factory:sdlc-light` (Stage 4). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `git commit` fast (ruff + license headers only) by moving the full pytest suite and `make sonar-local` into a new `pre-push` hook, and add a `commit-msg` hook that enforces the `EPMCDME-<n>:` message prefix.

**Architecture:** The repo uses the [pre-commit](https://pre-commit.com/) framework. Today a single `local` hook at `stages: [pre-commit]` runs the monolithic `scripts/git-hooks/pre_commit.sh` (staged-ruff → ruff check → license → full pytest → `make sonar-local`). We split responsibilities across three one-file-per-responsibility bash scripts under `scripts/git-hooks/` — `pre_commit.sh` (refactored, fast), `pre_push.sh` (new, heavy), `commit_msg.sh` (new, message policy) — each wired to its own stage in `.pre-commit-config.yaml`. `pre-commit` and `commit-msg` run unconditionally on every commit. `pre-push` is opt-in via `CODEMIE_PREPUSH_ENABLED` (default `false`) because the heavy suite should never silently block pushes while pre-existing failures live on `main`. Per-tool command overrides (`RUFF_CMD`, `PYTEST_CMD`, `SONAR_CMD`) keep the scripts unit-testable with stubs, exactly like `_ruff_staged.sh` / `test_ruff_staged_hook.py`.

**Tech Stack:** Bash, the `pre-commit` framework (dev dep `^3.8.0`), Poetry, pytest (subprocess-style tests), Node `run-local-sonar.js`, GNU Make.

## Global Constraints

- **License headers:** every new/modified `.sh` and `.py` file MUST carry the Apache 2.0 header (verbatim, matching existing files in `scripts/git-hooks/` and `tests/scripts/`). The license checker covers `.py` and `.sh` under `src/`, `scripts/`, `tests/`.
- **SonarQube gate:** new code must stay under Cyclomatic Complexity 15. Keep shell/branching flat.
- **Scope boundary (confirmed with user):** gitleaks and other "cheap hooks" for the fast pre-commit are the SIBLING sub-task's job — do NOT add gitleaks or touch `.gitleaks.toml` here.
- **Tool invocation convention:** hooks call tools via `poetry run <tool>` by default, exposed through an overridable `*_CMD` env var (default value = the real command) so tests can substitute stubs.
- **Toggle:** only `pre-push` has an env-var gate. `CODEMIE_PREPUSH_ENABLED=true` (or `1`/`on`) enables the heavy suite; any other value (including unset) skips it. `pre-commit` and `commit-msg` run unconditionally — no toggle for either.
- **Enterprise deselection:** the pre-push pytest run MUST pass `--ignore=tests/enterprise/` on the CLI (NOT via `pytest.ini`), because `tests/enterprise/*` import `codemie_enterprise` at module scope and fail at collection in OSS setups.
- **No double pytest:** the pre-push hook runs pytest once with coverage, then calls `make sonar-local` with `SONAR_SKIP_TESTS=1` so the Node script reuses the produced `coverage.xml`.
- **Commit messages** in this branch follow `EPMCDME-13747: <desc>`.

---

## File Structure

- `scripts/git-hooks/commit_msg.sh` — **new.** Validates the commit message file against `^EPMCDME-[0-9]+:`; bypasses merge/revert/autosquash. Always on — no toggle.
- `scripts/git-hooks/pre_push.sh` — **new.** Runs full pytest (excluding `tests/enterprise/`) with coverage, then `make sonar-local` with `SONAR_SKIP_TESTS=1`. Opt-in: skipped by default (`CODEMIE_PREPUSH_ENABLED` defaults to `false`; set to `true` to enable). Overridable via `PYTEST_CMD`/`SONAR_CMD`.
- `scripts/git-hooks/pre_commit.sh` — **modify.** Remove the pytest and `make sonar-local` sections; keep staged-ruff → ruff check → license headers. No toggle — always runs. Parametrize `ruff check` and license via `RUFF_CMD`/`LICENSE_CMD` for testability.
- `tests/scripts/test_commit_msg_hook.py` — **new.** Subprocess tests for `commit_msg.sh`.
- `tests/scripts/test_pre_push_hook.py` — **new.** Subprocess tests for `pre_push.sh` using stub `PYTEST_CMD`/`SONAR_CMD`.
- `tests/scripts/test_pre_commit_fast_hook.py` — **new.** Subprocess test proving the refactored `pre_commit.sh` no longer runs the heavy steps.
- `.pre-commit-config.yaml` — **modify.** Keep the (now-fast) pre-commit hook; add a `pre-push` hook and a `commit-msg` hook.
- `Makefile` — **modify.** Add an `install-hooks` target that installs all three hook types.
- `README.md` — **modify.** Update the "Git Hooks" section: fast commit flow, new pre-push/commit-msg behavior, `CODEMIE_PREPUSH_ENABLED` (opt-in, default false), `make install-hooks`.

---

## Task 1: `commit-msg` hook — enforce `EPMCDME-<n>:` prefix

**Files:**
- Create: `scripts/git-hooks/commit_msg.sh`
- Test: `tests/scripts/test_commit_msg_hook.py`

**Interfaces:**
- Consumes: nothing (leaf script).
- Produces: an executable bash script invoked as `commit_msg.sh <path-to-commit-msg-file>`. Exit 0 = message accepted or bypassed; exit 1 = rejected. Reads the message from `$1`. No env toggle — always runs.

**Behavior spec:**
- If a merge is in progress (`git rev-parse -q --verify MERGE_HEAD`) → exit 0.
- First non-comment, non-blank line of the message: if it starts with `Merge `, `Revert `, `fixup!`, `squash!`, or `amend!` → exit 0 (auto-generated / autosquash).
- Else require the first line to match `^EPMCDME-[0-9]+:` → exit 0; otherwise print a helpful message with the offending line and the expected format, exit 1.

- [ ] **Step 1: Write the failing tests**

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

"""Subprocess tests for scripts/git-hooks/commit_msg.sh (EPMCDME-13747)."""

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / "scripts" / "git-hooks" / "commit_msg.sh"


def _run(repo: Path, message: str, env_overrides=None):
    msg_file = repo / "COMMIT_EDITMSG"
    msg_file.write_text(message)
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        ["bash", str(HOOK), str(msg_file)],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "scratch"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main"], cwd=repo, check=True)
    return repo


def test_valid_prefix_accepted(tmp_path):
    repo = _git_repo(tmp_path)
    result = _run(repo, "EPMCDME-13747: Add pre-push hook\n")
    assert result.returncode == 0, result.stdout + result.stderr


def test_missing_prefix_rejected(tmp_path):
    repo = _git_repo(tmp_path)
    result = _run(repo, "fix: something without a ticket\n")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "EPMCDME-" in (result.stdout + result.stderr)


def test_prefix_without_colon_rejected(tmp_path):
    repo = _git_repo(tmp_path)
    result = _run(repo, "EPMCDME-13747 missing the colon\n")
    assert result.returncode == 1, result.stdout + result.stderr


def test_merge_commit_message_bypassed(tmp_path):
    repo = _git_repo(tmp_path)
    result = _run(repo, "Merge branch 'main' into feature\n")
    assert result.returncode == 0, result.stdout + result.stderr


def test_fixup_message_bypassed(tmp_path):
    repo = _git_repo(tmp_path)
    result = _run(repo, "fixup! EPMCDME-13747: earlier commit\n")
    assert result.returncode == 0, result.stdout + result.stderr


def test_merge_head_present_bypasses_validation(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / ".git" / "MERGE_HEAD").write_text("0" * 40 + "\n")
    result = _run(repo, "no ticket here at all\n")
    assert result.returncode == 0, result.stdout + result.stderr


def test_leading_comment_lines_ignored(tmp_path):
    repo = _git_repo(tmp_path)
    result = _run(repo, "# a comment\n\nEPMCDME-13747: real subject\n")
    assert result.returncode == 0, result.stdout + result.stderr
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/scripts/test_commit_msg_hook.py -v`
Expected: FAIL — `commit_msg.sh` does not exist yet (bash exits non-zero: "No such file or directory"), so the accept/bypass tests fail.

- [ ] **Step 3: Write the minimal implementation**

Create `scripts/git-hooks/commit_msg.sh`:

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

# commit-msg hook: enforce the EPMCDME-<n>: subject prefix.
#
# Invoked by git / the pre-commit framework with the path to the commit
# message file as $1. Exit 0 = accepted or intentionally bypassed;
# exit 1 = rejected.

set -euo pipefail

msg_file="${1:-}"
if [[ -z "$msg_file" || ! -f "$msg_file" ]]; then
  echo "[commit-msg] No commit message file provided; skipping."
  exit 0
fi

# Bypass while a merge is in progress (merge commit messages are generated).
if git rev-parse -q --verify MERGE_HEAD >/dev/null 2>&1; then
  exit 0
fi

# First meaningful line (skip blank lines and scissors/comment lines).
subject=""
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  [[ "$line" == \#* ]] && continue
  subject="$line"
  break
done < "$msg_file"

# Auto-generated / autosquash subjects are allowed through unchanged.
case "$subject" in
  "Merge "* | "Revert "* | "fixup!"* | "squash!"* | "amend!"*)
    exit 0
    ;;
esac

if [[ "$subject" =~ ^EPMCDME-[0-9]+: ]]; then
  exit 0
fi

echo "[commit-msg] Commit message must start with 'EPMCDME-<number>:'."
echo "[commit-msg] Got: '$subject'"
echo "[commit-msg] Example: 'EPMCDME-13747: Move heavy checks to pre-push'"
exit 1
```

Then make it executable and mark it executable in git:

```bash
chmod +x scripts/git-hooks/commit_msg.sh
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/scripts/test_commit_msg_hook.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/git-hooks/commit_msg.sh tests/scripts/test_commit_msg_hook.py
git commit -m "EPMCDME-13747: Add commit-msg hook enforcing EPMCDME-<n>: prefix"
```

---

## Task 2: `pre-push` hook — full pytest (excl. enterprise) + sonar-local

**Files:**
- Create: `scripts/git-hooks/pre_push.sh`
- Test: `tests/scripts/test_pre_push_hook.py`

**Interfaces:**
- Consumes: nothing (leaf script).
- Produces: an executable bash script. Exit 0 = pass/skip; non-zero = a heavy gate failed. Env: `CODEMIE_PREPUSH_ENABLED` (default `false`, opt-in), `PYTEST_CMD` (default `poetry run pytest`), `SONAR_CMD` (default `make sonar-local`).

**Behavior spec:**
- **Backward compat:** if `CODEMIE_PRECOMMIT_ENABLED` is `true`/`1`/`on`, print a deprecation warning banner (stderr) and treat it as `CODEMIE_PREPUSH_ENABLED=true`. Developers who had the old variable set continue to get the heavy suite with a clear migration message.
- Unless `CODEMIE_PREPUSH_ENABLED` is `true`/`1`/`on` (case-insensitive) → print skip message and exit 0 (opt-in; default skips).
- Run: `$PYTEST_CMD tests/ --ignore=tests/enterprise/ -W ignore::DeprecationWarning --cov --cov-report=xml:coverage.xml`. On failure → print a tip (`make test`) and exit with pytest's code.
- On pytest success → run sonar reusing coverage: `SONAR_SKIP_TESTS=1 $SONAR_CMD`. On failure → tip (`make sonar-local`) and exit 1.
- Success → print a concise confirmation.

- [ ] **Step 1: Write the failing tests**

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

"""Subprocess tests for scripts/git-hooks/pre_push.sh (EPMCDME-13747)."""

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / "scripts" / "git-hooks" / "pre_push.sh"

# A stub that records its argv to a log file and exits with a chosen code.
STUB_TEMPLATE = """#!/usr/bin/env bash
echo "$@" >> "{log}"
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
    env["CODEMIE_PREPUSH_ENABLED"] = "true"  # opt-in by default for tests
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
    # sonar stub must NOT have run (only the pytest line is present).
    assert "--ignore=tests/enterprise/" in calls
    assert calls.count("\n") == 1


def test_sonar_failure_blocks_push(tmp_path):
    result, _ = _run(tmp_path, pytest_code=0, sonar_code=1)
    assert result.returncode != 0


def test_skipped_when_not_enabled(tmp_path):
    result, calls = _run(tmp_path, env_overrides={"CODEMIE_PREPUSH_ENABLED": "false"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == ""


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/scripts/test_pre_push_hook.py -v`
Expected: FAIL — `pre_push.sh` does not exist yet.

- [ ] **Step 3: Write the minimal implementation**

Create `scripts/git-hooks/pre_push.sh`:

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

# pre-push hook: run the heavy quality gates before code reaches the remote.
#   1) full pytest (excluding tests/enterprise, which need codemie_enterprise)
#      with coverage -> coverage.xml
#   2) make sonar-local, reusing that coverage.xml (SONAR_SKIP_TESTS=1)
#
# Opt-in: set CODEMIE_PREPUSH_ENABLED=true to enable. Disabled by default so
# the hook never blocks pushes unless the developer explicitly opts in.
# PYTEST_CMD / SONAR_CMD override the tool invocations for unit testing.

set -euo pipefail

enabled="${CODEMIE_PREPUSH_ENABLED:-false}"
shopt -s nocasematch
if [[ "$enabled" != "true" && "$enabled" != "1" && "$enabled" != "on" ]]; then
  echo "[pre-push] CODEMIE_PREPUSH_ENABLED=$enabled -> skipping heavy checks."
  echo "[pre-push] Tip: set CODEMIE_PREPUSH_ENABLED=true to run pytest + sonar before push."
  exit 0
fi
shopt -u nocasematch

PYTEST_CMD="${PYTEST_CMD:-poetry run pytest}"
SONAR_CMD="${SONAR_CMD:-make sonar-local}"

echo "[pre-push] Running full test suite (excluding tests/enterprise)..."
set +e
$PYTEST_CMD tests/ --ignore=tests/enterprise/ -W ignore::DeprecationWarning \
  --cov --cov-report=xml:coverage.xml
pytest_rc=$?
set -e
if [[ $pytest_rc -ne 0 ]]; then
  echo "[pre-push] Tests failed (exit $pytest_rc). Tip: run 'make test' to reproduce."
  exit $pytest_rc
fi

echo "[pre-push] Tests passed. Running shared local SonarQube check..."
if ! SONAR_SKIP_TESTS=1 $SONAR_CMD; then
  echo "[pre-push] SonarQube check failed. Tip: run 'make sonar-local' to reproduce."
  exit 1
fi

echo "[pre-push] All heavy checks passed. Push proceeding."
```

Then:

```bash
chmod +x scripts/git-hooks/pre_push.sh
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/scripts/test_pre_push_hook.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/git-hooks/pre_push.sh tests/scripts/test_pre_push_hook.py
git commit -m "EPMCDME-13747: Add pre-push hook running pytest + sonar-local"
```

---

## Task 3: Refactor `pre_commit.sh` to drop the heavy steps

**Files:**
- Modify: `scripts/git-hooks/pre_commit.sh`
- Test: `tests/scripts/test_pre_commit_fast_hook.py`

**Interfaces:**
- Consumes: `scripts/git-hooks/_ruff_staged.sh` (unchanged; already honors `RUFF_CMD`).
- Produces: refactored `pre_commit.sh` — staged-ruff → `$RUFF_CMD check` → license header check via `$LICENSE_CMD`. No pytest, no sonar. No toggle — always runs. Env: `RUFF_CMD` (default `poetry run ruff`) and `LICENSE_CMD` (default `poetry run python scripts/license_headers/check_license_headers.py`).

- [ ] **Step 1: Write the failing test**

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
    # Stage a NON-python file: _ruff_staged.sh short-circuits (no staged .py),
    # then pre_commit.sh runs ruff check + license via the stubs below.
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
    assert "SonarQube" not in combined
    assert "sonar-local" not in combined


def test_source_has_no_pytest_or_sonar_invocation():
    text = HOOK.read_text()
    assert "make sonar-local" not in text
    assert "pytest" not in text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/scripts/test_pre_commit_fast_hook.py -v`
Expected: FAIL — current `pre_commit.sh` still contains `pytest` and `make sonar-local`, and its `ruff check`/license calls use hardcoded `poetry run ...` (not the stubs), so the fast-path run does not exit 0 cleanly and the source-scan asserts fail.

- [ ] **Step 3: Refactor the implementation**

Replace `scripts/git-hooks/pre_commit.sh` sections 2.a–2.c and the success block. Keep the header, the poetry-presence check, and the `_ruff_staged.sh` call (no toggle to keep). The body from the staged-ruff call onward becomes:

```bash
# --- 1. Ruff formatting and fixes (fast pass, staged Python content only) ---
bash "$(dirname "$0")/_ruff_staged.sh"

# --- 2. Fast checks only (Ruff lint + license headers). Heavy steps (full
#        pytest + sonar-local) moved to the pre-push hook (EPMCDME-13747). ---
echo "[pre-commit] No formatting changes detected. Running ruff checks and license checks..."

RUFF_CMD="${RUFF_CMD:-poetry run ruff}"
LICENSE_CMD="${LICENSE_CMD:-poetry run python scripts/license_headers/check_license_headers.py}"

# 2.a Ruff check (non-mutating)
if ! $RUFF_CMD check; then
  echo "[pre-commit] Ruff check failed. Please fix linting issues above."
  exit 1
fi

# 2.b Apache 2.0 license header check
echo "[pre-commit] Checking Apache 2.0 license headers..."
if ! $LICENSE_CMD --check --quiet; then
  echo "[pre-commit] License header check failed."
  echo "[pre-commit] Tip: run 'make license' to fix or validate license headers locally."
  exit 1
fi

# Success: heavy checks (pytest + sonar-local) now run on 'git push'.
>&2 echo "[pre-commit] Fast checks passed. Full tests + SonarQube run on push (pre-push hook)."
```

Also update the top-of-file comment block (lines describing steps 1–3) to reflect that only ruff + license run here, and adjust the ERR-trap tip from `make verify` to `make ruff` (fast) — keep it truthful.

- [ ] **Step 4: Run the test to verify it passes**

Run: `poetry run pytest tests/scripts/test_pre_commit_fast_hook.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Regression-check the ruff-staged tests still pass**

Run: `poetry run pytest tests/scripts/test_ruff_staged_hook.py -v`
Expected: PASS (unchanged — `_ruff_staged.sh` was not modified).

- [ ] **Step 6: Commit**

```bash
git add scripts/git-hooks/pre_commit.sh tests/scripts/test_pre_commit_fast_hook.py
git commit -m "EPMCDME-13747: Slim pre-commit to ruff + license (heavy steps to pre-push)"
```

---

## Task 4: Wire the hooks and update onboarding (config + Makefile + README)

**Files:**
- Modify: `.pre-commit-config.yaml`
- Modify: `Makefile`
- Modify: `README.md`

**Test-first: no** — this task is framework config, a Makefile target, and docs; there is no unit behavior to assert. Verification is via `pre-commit validate-config` and a dry `make -n`.

**Interfaces:**
- Consumes: `scripts/git-hooks/pre_commit.sh`, `pre_push.sh`, `commit_msg.sh` from Tasks 1–3.
- Produces: three registered hooks and a `make install-hooks` target.

- [ ] **Step 1: Update `.pre-commit-config.yaml`**

Replace the file contents with (keep the existing pre-commit entry; add two more):

```yaml
repos:
  - repo: local
    hooks:
      - id: codemie-pre-commit
        name: Codemie pre-commit (ruff fast fix + lint + license)
        entry: bash -lc 'bash scripts/git-hooks/pre_commit.sh 2>&1 | tee /dev/tty 2>/dev/null; exit ${PIPESTATUS[0]}'
        language: system
        pass_filenames: false
        stages: [pre-commit]
        always_run: true
      - id: codemie-commit-msg
        name: Codemie commit-msg (EPMCDME-<n>: prefix)
        entry: bash scripts/git-hooks/commit_msg.sh
        language: system
        pass_filenames: true
        stages: [commit-msg]
        always_run: true
      - id: codemie-pre-push
        name: Codemie pre-push (pytest + sonar-local)
        entry: bash -lc 'bash scripts/git-hooks/pre_push.sh 2>&1 | tee /dev/tty 2>/dev/null; exit ${PIPESTATUS[0]}'
        language: system
        pass_filenames: false
        stages: [pre-push]
        always_run: true
```

Note: for `commit-msg`, `pass_filenames: true` makes the framework append the commit-message file path as the final argument — that becomes `$1` in `commit_msg.sh`.

- [ ] **Step 2: Add the `install-hooks` target to `Makefile`**

Insert after the `install-oss` target:

```makefile
install-hooks:
	poetry run pre-commit install --hook-type pre-commit --hook-type commit-msg --hook-type pre-push
```

- [ ] **Step 3: Update the README "Git Hooks" section**

In `README.md`, replace the `### Git Hooks (pre-commit)` block (lines ~150–172) so it documents the split. Key content to include:
- Install: `poetry install` then `make install-hooks` (installs pre-commit, commit-msg, and pre-push).
- Toggle: only `pre-push` has one. `CODEMIE_PREPUSH_ENABLED=true` (or `1`/`on`) enables the heavy suite; default is `false` (opt-in). `pre-commit` and `commit-msg` run unconditionally — no toggle.
- Commit flow (fast): `ruff format`/`ruff check --fix` staged pass → if changed, blocks; else `ruff check` + license headers.
- commit-msg: subject must start with `EPMCDME-<n>:`; merge/revert/fixup!/squash!/amend! bypassed.
- Push flow (heavy): full `pytest` excluding `tests/enterprise/` + `make sonar-local` (reusing coverage); nothing red reaches the remote.
- Keep the existing manual/troubleshooting bullets; update the `chmod +x` example to also mention `pre_push.sh` and `commit_msg.sh`.
- Update the line ~268 "This same command is also executed by the repo pre-commit hook after Ruff and pytest pass." to say the pre-**push** hook.

- [ ] **Step 4: Verify the config parses**

Run: `poetry run pre-commit validate-config .pre-commit-config.yaml && echo OK`
Expected: `OK` (exit 0). If `pre-commit` is unavailable, fall back to `poetry run python -c "import yaml,sys; yaml.safe_load(open('.pre-commit-config.yaml')); print('OK')"`.

Run: `make -n install-hooks`
Expected: prints the `poetry run pre-commit install --hook-type ...` line (dry run, no execution).

- [ ] **Step 5: License-header check on the new shell scripts**

Run: `poetry run python scripts/license_headers/check_license_headers.py --check --quiet scripts/git-hooks/pre_push.sh scripts/git-hooks/commit_msg.sh && echo OK`
Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add .pre-commit-config.yaml Makefile README.md
git commit -m "EPMCDME-13747: Wire pre-push + commit-msg hooks and update onboarding docs"
```

---

## Task 5: End-to-end verification of the installed hooks

**Files:** none (verification only).

**Test-first: no** — this is an integration smoke check of the real hooks in a throwaway git repo, run manually. It complements the unit tests without adding permanent assertions.

- [ ] **Step 1: Verify the full script test suite is green**

Run: `poetry run pytest tests/scripts/ -v`
Expected: PASS (all four test files: ruff-staged, commit-msg, pre-push, pre-commit-fast).

- [ ] **Step 2: Smoke-test commit-msg rejection locally (non-destructive)**

Run:
```bash
printf 'bad message\n' > /tmp/msg.txt && bash scripts/git-hooks/commit_msg.sh /tmp/msg.txt; echo "exit=$?"
printf 'EPMCDME-13747: good\n' > /tmp/msg.txt && bash scripts/git-hooks/commit_msg.sh /tmp/msg.txt; echo "exit=$?"
```
Expected: first `exit=1` with the helpful message; second `exit=0`.

- [ ] **Step 3: Smoke-test pre-push disabled path**

Run: `CODEMIE_PREPUSH_ENABLED=false bash scripts/git-hooks/pre_push.sh; echo "exit=$?"`
Expected: skip line + `exit=0` (does not run the real suite).

- [ ] **Step 4: Report results** — record the exact commands run and outcomes in the Stage 6 QA notes.

---

## Self-Review

- **Spec coverage:** move pytest+sonar to pre-push → Task 2 + Task 3; commit-msg `EPMCDME-<n>:` enforcement → Task 1; pre-commit stays fast (ruff + license) → Task 3; enterprise deselection gotcha → Task 2 (`--ignore=tests/enterprise/`) with a dedicated test; onboarding/two-step install → Task 4 (`make install-hooks` + README). Gitleaks intentionally excluded (sibling sub-task, confirmed with user). ✎ Covered.
- **Placeholder scan:** all code steps contain full file contents or exact diffs; no TBD/TODO.
- **Type/name consistency:** env-var names (`CODEMIE_PREPUSH_ENABLED`, `PYTEST_CMD`, `SONAR_CMD`, `RUFF_CMD`, `LICENSE_CMD`, `SONAR_SKIP_TESTS`) and script paths are used identically across tasks and match the repo's existing `RUFF_CMD` convention.
```
