# Technical Research

**Task**: pre-commit hook ruff staged-files git
**Generated**: 2026-07-28T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-13740 [PoC] Fix ruff pre-commit hook staged-files detection.

## What
`scripts/git-hooks/pre_commit.sh` detects ruff-applied changes via `git ls-files -m`, which matches *any* unstaged modification.

## Why it hurts
- `git add -p` (partial staging) → false "Ruff applied changes" and commit aborts
- `ruff format` (no args) silently reformats unstaged/unrelated work

## Fix (verified locally 2026-07-27, then reverted)
- Run ruff only on `git diff --cached --name-only --diff-filter=ACMR -- '*.py'`
- Detect ruff-applied changes by `git hash-object` on each staged file *before/after* (compare via `comm -13`)
- Pass `--force-exclude` so pyproject excludes apply to explicit paths
- Skip entirely if no staged `.py` files

Priority: HIGH — real UX bug affecting anyone using partial staging.

A design note from a prior verified-then-reverted attempt lives at:
/Users/oleg_sotnichenko/codemie-dev/docs/backend-hooks-improvement-plan.md
Please read it and cross-reference against the current state of scripts/git-hooks/pre_commit.sh.

---

## 2. Codebase Findings

### Existing Implementations

- `scripts/git-hooks/pre_commit.sh` — the sole pre-commit hook script, 116 lines; only file in the `git-hooks/` directory
- `.pre-commit-config.yaml` — pre-commit framework config that invokes the hook via `bash -lc 'bash scripts/git-hooks/pre_commit.sh 2>&1 | tee /dev/tty 2>/dev/null; exit ${PIPESTATUS[0]}'`; uses deprecated `stages: [commit]` syntax (pre-commit 3.x renamed this to `stages: [pre-commit]`)
- `pyproject.toml` — ruff dev dependency pinned at `^0.5.4`; contains all ruff configuration at approx. line 197
- `Makefile` — defines `ruff`, `ruff-format`, `ruff-fix`, and `verify` targets; `make ruff` runs repo-wide `ruff format` then `ruff check --fix` then `ruff check` (same surface-area problem as the hook, not in scope here)
- `scripts/sonar/run-local-sonar.js` — sonar local runner called from hook step 10
- `scripts/license_headers/check_license_headers.py` — Apache 2.0 header checker called from hook step 8
- `/Users/oleg_sotnichenko/codemie-dev/docs/backend-hooks-improvement-plan.md` — authoritative design note; contains verified replacement bash snippet for Section 1; status: "plan only, nothing applied" as of 2026-07-27

**Current hook execution flow (step by step):**
1. Read `CODEMIE_PRECOMMIT_ENABLED`; exit 0 if false/0/off (**currently set to `false` in `.env`** — hook is disabled locally)
2. Assert `poetry` is on PATH
3. `poetry run ruff format` — **on all repo files, no path restriction**
4. `poetry run ruff check --fix || true` — **on all repo files**; non-zero exit swallowed
5. `git ls-files -m` → if non-empty: print files, tell user to `git add`, exit 1 — **bug: fires on any unstaged modification, not just ruff-caused ones**
6. `poetry run ruff check` — read-only lint; fails on violations
7. `poetry run python scripts/license_headers/check_license_headers.py --check --quiet`
8. `poetry run pytest -q -r a tests/`; fails on non-zero
9. `make sonar-local`; fails on non-zero

### Architecture and Layers Affected

This task touches a single architectural layer: **Developer Tooling / Shell Scripts**. No Python source, no API router, no service or repository layer, no DB models, no migrations are involved.

- **Shell script layer**: `scripts/git-hooks/pre_commit.sh` — the only file requiring change for the core Section 1 fix
- **Pre-commit framework config** (`-pre-commit-config.yaml`): may require the `stages: [commit]` → `stages: [pre-commit]` fix (adjacent to this ticket, flagged in design note Section 5)
- All other layers (FastAPI routes, LangGraph agents, Elasticsearch clients, NATS workers) are untouched

### Integration Points

- **pre-commit framework** — hook is not a direct `.git/hooks/pre-commit` symlink; it is managed by the `pre-commit` tool via `.pre-commit-config.yaml`; developers must run `poetry run pre-commit install` to wire it
- **ruff** — invoked via `poetry run ruff`; version `^0.5.4` in dev deps; config authoritative in `pyproject.toml`
- **git plumbing commands** — fix introduces `git diff --cached --name-only --diff-filter=ACMR`, `git hash-object`, and `comm -13`; all standard, no external dependencies
- **Makefile** — `make ruff` target runs repo-wide ruff (separate call path from the hook; not changed by this ticket)

### Patterns and Conventions

- **macOS bash 3.2 compatibility required** — hook invoked via `bash -lc`; no `mapfile`, no associative arrays permitted; design note explicitly acknowledges this and the replacement snippet is written to comply
- **`CODEMIE_PRECOMMIT_ENABLED` escape hatch** — env var toggles the hook off; currently `false` in `.env`; developers must flip this to `true` to test the fix
- **`--force-exclude` must accompany explicit path lists** — when ruff is called with explicit file paths (as the fix requires), pyproject `exclude` patterns are bypassed unless `--force-exclude` is passed
- **`make ruff` is the repo-wide lint command** — guides say prefer `make ruff` over manual ruff invocation; hook is a narrower staged-only variant
- **Apache 2.0 license headers required** — any new or replaced shell scripts need the header; `make license-check` / `make license-fix` enforce this

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `/Users/oleg_sotnichenko/codemie-dev/codemie/.ai-run/guides/quality-gates.md` — defines the gate order, exact commands, and pass/fail criteria for ruff, license check, tests, sonar; directly relevant to what the hook runs and in what order
- `/Users/oleg_sotnichenko/codemie-dev/codemie/.ai-run/guides/standards/code-quality.md` — states ruff config authority is `pyproject.toml`; instructs use of `make ruff` not manual invocation
- `/Users/oleg_sotnichenko/codemie-dev/codemie/AGENTS.md` — "Check Guides First" rule; Stop Hook rule for `make ruff` failures; git operations only on explicit user request; load `standards/git-workflow.md` before git ops

### Architectural Decisions

- No ADR directory found. The single authoritative record of planned hook changes is `/Users/oleg_sotnichenko/codemie-dev/docs/backend-hooks-improvement-plan.md`. This document records verified findings across 9 sections (ruff staged-fix, ruff version upgrade, isort, gitleaks, pre-commit-hooks, hadolint, type checking, pre-push migration, additional rules). Status of all sections: "plan only, nothing applied."
- The Section 1 replacement snippet in that document was verified locally on 2026-07-27 and then reverted — it is the direct implementation target for this ticket.

### Derived Conventions

- Shell scripts live under `scripts/`; hook scripts under `scripts/git-hooks/`
- The hook is the only shell-layer file in the repo; no shared shell library exists — helper functions must be inlined or the script must remain self-contained
- `CODEMIE_PRECOMMIT_ENABLED=false` is the intended way to opt out; no `git commit -n` bypass is documented
- bash 3.2 compatible idioms: use `while IFS= read -r line` loops, not `mapfile`; use plain indexed arrays, not associative arrays

### External Documentation Findings

Not applicable — this task involves only git plumbing builtins (`git diff`, `git hash-object`) and ruff CLI flags (`--force-exclude`). No third-party SDK or external HTTP API is introduced.

---

## 4. Testing Landscape

### Existing Coverage

- `pre_commit.sh` has **zero test coverage** — no shell tests, no Python tests
- No `tests/scripts/` or `tests/hooks/` directory exists
- Python tests (in `tests/`) mirror `src/` and cover application logic only; they do not exercise shell scripts

### Testing Framework and Patterns

- pytest 8.3.1 with pytest-asyncio, pytest-mock, pytest-httpx, pytest-cov, pytest-env
- Session-scoped `autouse` fixtures mock infrastructure (DB engine, env vars via `pytest.ini`)
- No shell test framework present (no bats, no shunit2)
- `pytest.ini`: `testpaths = tests`, `pythonpath = src`, `--import-mode=importlib`

### Coverage Gaps

- The entire `scripts/git-hooks/pre_commit.sh` script is untested
- The fix introduces additional git plumbing logic (`git hash-object` loop, `comm -13` comparison) that has no automated test harness
- If tests are desired, the options are: (a) subprocess-based Python integration test invoking the script in a scratch git repo, or (b) bats shell tests — neither framework is present and adding one is out of scope for this ticket

---

## 5. Configuration and Environment

### Environment Variables

- `CODEMIE_PRECOMMIT_ENABLED` — hook escape hatch; any value of `false`, `0`, or `off` skips the hook; **currently set to `false` in `.env`** meaning the hook is entirely disabled for local development; testers must flip this to exercise the fix
- No other hook-specific env vars are referenced in the script

### Configuration Files

- `pyproject.toml` (lines ~197–253) — sole ruff configuration file:
  - `line-length = 120`, `indent-width = 4`
  - `exclude = [".agents", ".claude"]` — these paths are skipped unless `--force-exclude` is missing (a bug the fix addresses)
  - `select = ["E", "F", "B", "N", "C4", "G", "T20", "RSE", "SIM", "C", "W", "PERF", "ISC"]` — no `I` (isort) despite isort being configured
  - `unfixable = ["B"]` — bugbear violations never auto-fixed
  - `per-file-ignores`: generated code, external src, template/prompt files, test files all have relaxed rules
- `.pre-commit-config.yaml` — single local hook `codemie-pre-commit`; `pass_filenames: false`, `always_run: true`, `stages: [commit]` (deprecated)

### Feature Flags and Deployment Concerns

- `CODEMIE_PRECOMMIT_ENABLED` is the only toggle; it must be set to a truthy value for the fix to take effect during testing
- No CI/CD pipeline file (`.gitlab-ci.yml` absent); ruff runs in CI only via `make verify`; this hook fix has zero CI blast radius
- The fix is entirely local-developer-facing; no deployment manifests are involved

---

## 6. Risk Indicators

- **Hook is currently disabled** — `CODEMIE_PRECOMMIT_ENABLED=false` in `.env`; the fix will have no effect until developers explicitly re-enable it; reviewer and author must document this in the MR
- **Zero test coverage for the hook** — the `git hash-object` before/after loop and `comm -13` comparison logic cannot be automatically validated without a bats or subprocess-based test harness, neither of which exists; correctness relies entirely on the verified manual test from 2026-07-27
- **bash 3.2 compatibility constraint** — the replacement snippet must avoid `mapfile` and associative arrays; the design note snippet is written correctly, but any deviation during implementation risks breakage on macOS (the primary developer platform)
- **`--force-exclude` required but not currently used** — omitting it after the staged-files refactor would silently re-include `.agents`/`.claude` files in ruff runs when those files happen to be staged; must be included in the replacement invocations
- **`stages: [commit]` deprecation** in `.pre-commit-config.yaml` — design note Section 5 flags this; it is adjacent to this ticket but the file will likely be touched if the hook description or wording changes; decision needed on whether to fix in the same MR
- **`make ruff` still runs repo-wide** — the Makefile target is not changed by this ticket; developers who run `make ruff` manually (e.g. from Claude Code's stop hook) still get repo-wide reformatting; this is a separate but related surface-area problem documented in the design note
- **ruff version `^0.5.4`** — upgrade to `0.16.x` (design note Section 2) is deferred to a separate MR but is noted as HIGH; the staged-files fix should be implemented and merged independently

---

## 7. Summary for Complexity Assessment

This task touches a single file in a single architectural layer: the Developer Tooling / Shell Scripts layer. The primary change is confined to `scripts/git-hooks/pre_commit.sh` (116 lines); a secondary minor edit to `.pre-commit-config.yaml` may be warranted to fix the deprecated `stages: [commit]` syntax. No Python source files, no API layer, no database layer, and no external service integration are involved. The total expected file change surface is 1–2 files with the core logic replacement being approximately 30–50 lines as indicated by the design note's verified replacement snippet.

The task follows an established pattern (bash scripting with git plumbing builtins) and introduces no new frameworks or dependencies. The exact implementation is already specified and was verified locally in the design note at `/Users/oleg_sotnichenko/codemie-dev/docs/backend-hooks-improvement-plan.md` Section 1. The only implementation constraint requiring attention is bash 3.2 compatibility (no `mapfile`, no associative arrays), which the design note explicitly addresses. Technical novelty is minimal — this is a targeted bug fix replacing a broad `git ls-files -m` check with a precise `git hash-object` before/after comparison.

The key risk factors are operational rather than technical: the hook is currently disabled via `CODEMIE_PRECOMMIT_ENABLED=false` (affecting verification), there is zero automated test coverage for the shell script and no test infrastructure to add it, and the fix requires the reviewer to manually reproduce the `git add -p` scenario to confirm correctness. These factors make the task low-complexity in terms of code change but require careful manual validation and clear MR documentation of the re-enablement step.
