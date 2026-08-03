# Technical Research

**Task**: pre-commit pre-push git-hooks commit-msg pytest sonar-local ruff license-headers gitleaks
**Generated**: 2026-07-30T00:00:00Z
**Research path**: codegraph (available) + filesystem deep-read

---

## 1. Original Context

Title: [PoC] Move pytest + sonar-local from pre-commit to pre-push; add commit-msg EPMCDME-XXXXX enforcement

## What
Current pre-commit runs full pytest + `make sonar-local` on every commit → pushes people to `CODEMIE_PRECOMMIT_ENABLED=false` or `-n`.
## Proposal
- **pre-commit** stays fast: ruff + gitleaks + license headers + cheap hooks (see sibling sub-task)
- **pre-push** takes heavy steps: full pytest + `make sonar-local`
- Same guarantee (nothing red reaches the remote), better commit UX
- Add **`commit-msg`** hook enforcing `EPMCDME-\d+:` prefix
## Gotcha (from local baseline)
Local full suite: 13462 passed, ~46-48 failed — all `ModuleNotFoundError: codemie_enterprise` (OSS setup can't install codemie-enterprise without GCP registry auth). Any "run pytest in hook" policy must deselect `tests/enterprise/` locally or gate on package importability.
## Priority
MEDIUM — the single biggest DX improvement in this set.

---

## 2. Codebase Findings

### Existing Implementations

- `scripts/git-hooks/pre_commit.sh` — monolithic hook; runs staged-ruff → ruff check → license headers → full pytest → make sonar-local; checks `CODEMIE_PRECOMMIT_ENABLED` env var (exits 0 if false/0/off)
- `scripts/git-hooks/_ruff_staged.sh` — standalone helper invoked by `pre_commit.sh`; extracts staged blobs via `git show :$f`, pipes through ruff format + check, byte-compares with `cmp -s`; never mutates working tree; supports `RUFF_CMD` override for testability
- `.pre-commit-config.yaml` — single `local` repo hook `codemie-pre-commit`, `stages: [pre-commit]`, `always_run: true`, entry `bash -lc 'bash scripts/git-hooks/pre_commit.sh ...'`; NO `pre-push` or `commit-msg` stages defined
- `Makefile` — relevant targets: `test` (`poetry run pytest tests/`), `ruff` (format + check --fix + check), `license-check` (check_license_headers.py --check --quiet), `gitleaks` (docker run zricethezav/gitleaks:v8.30.0 dir --verbose), `sonar-local` (`node scripts/sonar/run-local-sonar.js`), `verify` (ruff + license + gitleaks + test)
- `scripts/sonar/run-local-sonar.js` — Node.js; reads `.sonarlint/connectedMode.json` + `sonar-project.properties`; runs `poetry run pytest tests/ --cov --cov-report=xml:coverage.xml` unless `SONAR_SKIP_TESTS` is set; then runs `sonar-scanner` with quality gate wait; exits 0 silently if `SONAR_TOKEN` is unset
- `pytest.ini` — `testpaths = tests`, `addopts = --import-mode=importlib`, `pythonpath = src`, `filterwarnings = ignore::DeprecationWarning, ignore::RuntimeWarning`; no `--ignore=tests/enterprise`, no markers for enterprise deselection
- `tests/scripts/test_ruff_staged_hook.py` — existing unit tests for `_ruff_staged.sh`; uses `staged_repo` + `run_helper` fixtures; covers: no-staged-py, clean staged+dirty unstaged, ruff-reformats-staged, missing trailing newline, syntax error, partial-stage (`git add -p`) scenario
- `tests/enterprise/` — subdirs: `langfuse/`, `litellm/`, `mcp_auth/`, `migration/`, `observability/`; top-level: `__init__.py`, `conftest.py`, `test_graceful_degradation.py`, `test_loader.py`
- `tests/enterprise/conftest.py` — fixtures `mock_enterprise_installed` / `mock_enterprise_not_installed` that monkeypatch `codemie.enterprise.loader.*`; these are test-time fixtures, NOT collection-time skip guards; tests that import `codemie_enterprise.*` directly at module scope will still raise `ModuleNotFoundError` at collection
- `.git/hooks/` — only `.sample` files; no active hooks installed; pre-commit framework manages the installed `.git/hooks/pre-commit` via `poetry run pre-commit install`

### Architecture and Layers Affected

- **Shell scripting layer** (`scripts/git-hooks/`): new `pre_push.sh` and `commit_msg.sh` scripts must be created here; `pre_commit.sh` must be refactored to remove pytest and sonar-local calls
- **pre-commit framework config** (`.pre-commit-config.yaml`): must add new hook entries with `stages: [pre-push]` and `stages: [commit-msg]`; operators must run `poetry run pre-commit install --hook-type pre-push` and `poetry run pre-commit install --hook-type commit-msg` during onboarding
- **Makefile / build targets**: `verify` target currently includes `test` — if verify is called in CI it will still run tests; no changes strictly required but `sonar-local` documentation may need updating
- **pytest configuration** (`pytest.ini`): no changes strictly required IF the hook uses `--ignore=tests/enterprise/` as a CLI argument; alternatively, a new pytest marker `enterprise` could be added and the hook uses `-m "not enterprise"` — either approach avoids touching pytest.ini
- **Node.js sonar script** (`scripts/sonar/run-local-sonar.js`): no code changes needed IF the pre-push hook sets `SONAR_SKIP_TESTS` before calling `make sonar-local` (to avoid double pytest run); the script's silent exit-0 on missing `SONAR_TOKEN` is a pre-existing design choice
- **Tests layer** (`tests/scripts/`): new unit tests for `pre_push.sh` and `commit_msg.sh` must be added here following the `test_ruff_staged_hook.py` pattern

### Integration Points

- `pre-commit` Python framework (dev dependency, `^3.8.0`) manages hook installation and execution via `.pre-commit-config.yaml`; adding new stages requires both config AND `pre-commit install --hook-type <type>` — two-step onboarding that must be documented
- `poetry` runtime wraps all Python invocations (`ruff`, `pytest`, `python scripts/license_headers/...`) inside the hook scripts; hooks inherit the shell environment including `VIRTUAL_ENV` if set
- `docker` runtime required by `make gitleaks`; if gitleaks is to be part of the fast pre-commit (per the task), Docker must be available at commit time
- `node` + `sonar-scanner` required by `make sonar-local`; `SONAR_TOKEN` must be set for the sonar gate to have any effect
- `.sonarlint/connectedMode.json` + `sonar-project.properties` must exist for `run-local-sonar.js` to run; absent config → script exits non-zero

### Patterns and Conventions

- **Escape hatch pattern**: `CODEMIE_PRECOMMIT_ENABLED` env var checked at the top of `pre_commit.sh` (false/0/off → exit 0); the same pattern must be applied to the new pre-push hook (`CODEMIE_PREPUSH_ENABLED`) to maintain parity
- **Subprocess delegation**: `pre_commit.sh` invokes `_ruff_staged.sh` as a subprocess; new scripts should follow the same one-responsibility-per-script convention
- **`RUFF_CMD` override for testability**: `_ruff_staged.sh` reads `RUFF_CMD` so tests can substitute a mock; new hook scripts that invoke tools should follow the same pattern for unit-test isolation
- **`staged_repo` fixture pattern**: `test_ruff_staged_hook.py` creates a real temp git repo and runs the script against it; new hook test files must follow this pattern
- **`always_run: true` in pre-commit config**: current hook runs even when no files are staged; pre-push and commit-msg hooks have different triggers (push event / commit-msg file) and may not need this flag

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/` — directory exists; guide files covering testing conventions, architecture layers, and project patterns are present; no dedicated guide specifically for git hook conventions was identified
- `README.md` (lines ~153–159) — documents `CODEMIE_PRECOMMIT_ENABLED` escape hatch and `poetry run pre-commit install` onboarding step; does NOT document `--hook-type pre-push` or `--hook-type commit-msg` install steps
- `Makefile:verify` — the verify target documents the canonical quality gate sequence (ruff + license + gitleaks + test) but does not separate fast vs. slow steps

### Architectural Decisions

- **Single monolithic hook**: The current design intentionally runs all quality gates in one script (pre_commit.sh) to simplify the mental model; the task explicitly proposes splitting this into two hooks
- **Escape hatch via env var rather than `--no-verify`**: Use of `CODEMIE_PRECOMMIT_ENABLED` is a documented pattern; it avoids training developers to use `--no-verify` habitually, which would also bypass the new pre-push hook if not managed carefully
- **`sonar-local` graceful skip on missing SONAR_TOKEN**: An explicit design decision in `run-local-sonar.js` — developers without SonarQube access can still commit/push without error

### Derived Conventions

- New shell scripts in `scripts/git-hooks/` should follow bash naming: `pre_push.sh` and `commit_msg.sh` (underscores, `.sh` extension, matching `pre_commit.sh`)
- New test files should go in `tests/scripts/` following the `test_ruff_staged_hook.py` naming convention: `test_pre_push_hook.py`, `test_commit_msg_hook.py`
- All tool invocations in hooks use `poetry run <tool>` — never bare `pytest`, `ruff`, or `python`

---

## 4. Testing Landscape

### Existing Coverage

- `tests/scripts/test_ruff_staged_hook.py` — 6 test cases for `_ruff_staged.sh`; covers the full behavior matrix of the staged ruff helper; well-written with real git repos as fixtures
- No test file for `pre_commit.sh` itself (the monolithic hook is not unit-tested at the script level; only its sub-component `_ruff_staged.sh` is tested)
- No test file for any pre-push or commit-msg behavior (neither exists yet)

### Testing Framework and Patterns

- Framework: `pytest` (version from `pyproject.toml` dev deps)
- Pattern: create a real `tmp_path` git repo via `staged_repo` fixture, run the shell script as a subprocess, assert return code and stdout/stderr
- Mocking: `RUFF_CMD` env var override substitutes a script that either passes or fails without running real ruff
- The `run_helper` fixture wraps `subprocess.run` with env passthrough — new tests should replicate this

### Coverage Gaps

- `pre_commit.sh` itself has no test; refactoring it to remove pytest/sonar steps is an untested change
- New `pre_push.sh` script will have zero coverage until tests are written
- New `commit_msg.sh` script will have zero coverage until tests are written
- Enterprise test deselection logic (whether `--ignore=tests/enterprise` works correctly or an importability gate is used) needs a test that verifies the hook succeeds in an OSS environment (without `codemie_enterprise` installed)

---

## 5. Configuration and Environment

### Environment Variables

- `CODEMIE_PRECOMMIT_ENABLED` — escape hatch; false/0/off → `pre_commit.sh` exits 0 immediately; referenced in `pre_commit.sh:28-31` and `README.md:153-155`; default is true (not set); documented as `false` in `.env` for some developer setups — meaning the hook is currently bypassed by default for those developers
- `SONAR_TOKEN` — SonarQube auth token; if unset, `run-local-sonar.js` prints a skip message and exits 0; must be set for the pre-push quality gate to have real effect
- `SONAR_SKIP_TESTS` — if set, `run-local-sonar.js` skips running pytest for coverage (reuses existing `coverage.xml`); the pre-push hook MUST set this after pytest has already run to avoid double execution
- `SONAR_BRANCH_NAME` — override for git branch detection when `.git/HEAD` cannot be resolved
- `SONAR_SCANNER_PATH` — override for `sonar-scanner` binary path
- `RUFF_CMD` — override for ruff invocation in `_ruff_staged.sh`; used by tests to substitute a stub

### Configuration Files

- `.pre-commit-config.yaml` — declares all hooks, stages, and entry points for the pre-commit framework; must be modified to add pre-push and commit-msg hook entries
- `pytest.ini` — `testpaths`, `addopts`, `filterwarnings`; no enterprise exclusion currently; the hook command (not pytest.ini) should carry `--ignore=tests/enterprise/`
- `scripts/sonar/run-local-sonar.js` — reads `.sonarlint/connectedMode.json` and `sonar-project.properties` at runtime; both must exist for sonar-local to run successfully
- `.gitleaks.toml` — allowlist config for gitleaks; extends default ruleset; current allowlist entries: `config/index-dumps/**`, `__pycache__/**`, `.pytest_cache/**`, `.idea/**`, `.keys/**`, `.env_local`, `.env.local`

### Feature Flags and Deployment Concerns

- `CODEMIE_PRECOMMIT_ENABLED` functions as a feature flag for the pre-commit hook; a parallel `CODEMIE_PREPUSH_ENABLED` variable should be introduced for the pre-push hook
- No deployment manifests are affected by this change
- Onboarding setup must be updated: `poetry run pre-commit install` alone is insufficient after this change; developers must also run `poetry run pre-commit install --hook-type pre-push` and `poetry run pre-commit install --hook-type commit-msg`; a `make install-hooks` target would reduce onboarding friction

---

## 6. Risk Indicators

- **Double pytest execution**: `pre_commit.sh` currently calls `pytest` directly and then calls `make sonar-local`, which calls `pytest` again internally for coverage via `run-local-sonar.js`; the new pre-push hook must set `SONAR_SKIP_TESTS` after running pytest, or pass a pre-built `coverage.xml`, to avoid running the full test suite twice (total 3–8 minute penalty per push)
- **Enterprise test failures block all OSS contributors**: `pytest.ini` has no `--ignore=tests/enterprise`; ~46–48 tests in `tests/enterprise/` raise `ModuleNotFoundError: codemie_enterprise` at collection time in any OSS setup; the pre-push hook script must explicitly pass `--ignore=tests/enterprise/` (not relying on pytest.ini) and this behavior needs a test to prevent regression
- **`tests/enterprise/conftest.py` fixtures are NOT collection guards**: The `mock_enterprise_installed` / `mock_enterprise_not_installed` fixtures only run at test time, not at import/collection time; tests that import `codemie_enterprise.*` directly at module scope (not inside a function body) will still fail at collection; `pytest.importorskip` is the correct pattern but is only used in `tests/codemie/rest_api/security/jwks/test_integration.py` — other enterprise tests are inconsistent
- **`sonar-local` silently exits 0 without SONAR_TOKEN**: Any developer who does not have `SONAR_TOKEN` set gets a non-enforcing quality gate; the pre-push hook provides a false sense of security for those developers; this is a pre-existing design choice but should be documented explicitly as a known limitation
- **`CODEMIE_PRECOMMIT_ENABLED` naming collision**: The existing escape hatch is named for pre-commit; repurposing or renaming it for pre-push would break existing developer dotfiles; a NEW `CODEMIE_PREPUSH_ENABLED` variable is needed and both must be documented in README
- **`gitleaks` uses Docker full-dir scan**: `make gitleaks` scans the entire working directory, not staged files or the diff being pushed; this will flag gitignored local files (`.env`, personal `.codemie/` config) as false positives unless the `.gitleaks.toml` allowlist is extended; for a push hook, `gitleaks protect` or `gitleaks git --log-opts=origin/HEAD..HEAD` would be more precise; this affects whether gitleaks belongs in the fast pre-commit or the pre-push
- **`commit-msg` hook with pre-commit framework requires specific wiring**: The pre-commit framework passes the commit message file path via `PRE_COMMIT_COMMIT_MSG_FILENAME` env var (not as `$1`); the hook script must read from that env var or be declared with `args: [$1]` in the config; amend, squash, and merge commits carry the existing message through the hook and must satisfy the `EPMCDME-\d+:` regex — merge commit messages (e.g., `Merge branch ...`) will fail without an explicit exclusion for merge/squash commits
- **Two-step install not documented**: After this change, `poetry run pre-commit install` alone is insufficient; developers who cloned before the change and run only the existing onboarding step will have the pre-commit hook but NOT the pre-push or commit-msg hooks; `README.md` and ideally a `make install-hooks` target must be updated before the change is merged
- **No tests for `pre_commit.sh` itself**: The monolithic script is untested; refactoring it to remove pytest/sonar steps changes its behavior with no regression safety net beyond manual testing
- **New `pre_push.sh` and `commit_msg.sh` start at zero coverage**: No test scaffolding exists for these new scripts; the `test_ruff_staged_hook.py` pattern (real git repo, subprocess invocation, env override) must be replicated
- **SonarQube network dependency in pre-push hook**: Developers on restricted networks, VPNs, or without SonarQube access will be blocked by `make sonar-local` if `SONAR_TOKEN` is set but the server is unreachable; the exit-0 fallback only applies when `SONAR_TOKEN` is absent, not when the token is present but the network is unavailable — `run-local-sonar.js` does not implement a network timeout/fallback

---

## 7. Summary for Complexity Assessment

The task touches four distinct layers: the shell scripting layer (two new scripts must be created in `scripts/git-hooks/`, and `pre_commit.sh` must be refactored), the pre-commit framework configuration layer (`.pre-commit-config.yaml` gains two new hook entries), the onboarding/documentation layer (README and optionally a new `make install-hooks` Makefile target), and the test layer (two new test files modeled on `test_ruff_staged_hook.py`). The estimated file change surface is 7–9 files: `.pre-commit-config.yaml`, `scripts/git-hooks/pre_commit.sh` (removal of pytest/sonar steps), `scripts/git-hooks/pre_push.sh` (new), `scripts/git-hooks/commit_msg.sh` (new), `tests/scripts/test_pre_push_hook.py` (new), `tests/scripts/test_commit_msg_hook.py` (new), `README.md` (onboarding docs), and optionally `Makefile` (new `install-hooks` target). No changes to application source code are required.

Technical novelty is low to medium. The pre-commit framework already supports `stages: [pre-push]` and `stages: [commit-msg]` natively — this is a well-documented framework capability, not a novel pattern. The structural pattern for new scripts and tests is established by `_ruff_staged.sh` and `test_ruff_staged_hook.py`. The only non-trivial design decision is how to deselect `tests/enterprise/` in the pre-push hook: the simplest approach (`--ignore=tests/enterprise/`) requires no pytest.ini changes and no importability-gating logic, which is preferable for a PoC. The `commit-msg` hook's merge-commit exclusion requires a one-line guard (`if git rev-parse -q --verify MERGE_HEAD > /dev/null 2>&1; then exit 0; fi`), which is idiomatic shell and not complex.

The affected area has partial test coverage: `_ruff_staged.sh` is well-tested, but `pre_commit.sh` itself has no tests and the new scripts start at zero coverage. The sonar-local gate carries a silent-skip risk (no `SONAR_TOKEN` → exit 0) that limits its effectiveness as an enforcement mechanism for offline developers — this should be called out in the PoC evaluation. The most significant risk is the two-step install (`pre-commit install --hook-type pre-push` and `--hook-type commit-msg`) not being automated; without a `make install-hooks` target, the hooks will silently not be active for developers who run only the existing `poetry run pre-commit install` step.
