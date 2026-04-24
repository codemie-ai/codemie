---
name: qa-lead
description: QA Lead quality gate runner for the Codemie project. Use when implementation is complete and code needs to be verified before committing or creating a PR. Triggers on phrases like "run quality gates", "check code quality", "run qa", "verify my changes", "pre-commit checks", "qa check", "act as qa lead", or when tech-lead or solution-architect suggests quality verification as a next step.
---

# QA Lead: Quality Gate Enforcement

## Purpose

Runs all mandatory quality gates sequentially, reports pass/fail per gate, and provides actionable remediation guidance. Acts as the quality gatekeeper before code reaches review or merge.

**Gate sequence** (fastest to slowest):
1. **Ruff** — formatting + linting (fast)
2. **License headers** — copyright compliance (fast)
3. **Gitleaks** — secret scanning (medium, requires Docker)
4. **SonarQube local** — static analysis (slow, requires Node.js)
5. **Tests** — full test suite (slowest, only when explicitly requested)

---

## Workflow

### Step 1: Activate VirtualEnv

**MANDATORY** before any Python command:

```bash
source .venv/bin/activate
```

---

### Step 2: Run Gates Sequentially

Run each gate in order. Report status after each gate before moving to the next.

#### Gate 1: Ruff (Format + Lint)

```bash
source .venv/bin/activate && make ruff
```

**Pass**: No output or only informational messages.
**Fail**: Shows file paths with violations.

`make ruff` already applies `--fix` and `ruff format`. If it still fails, show the specific errors — they require manual intervention.

---

#### Gate 2: License Headers

```bash
source .venv/bin/activate && make license-check
```

**Pass**: Silent or "All files have correct license headers."
**Fail**: Lists files missing the Apache 2.0 license header.

**Auto-fix**:
```bash
make license-fix
```

Re-run `make license-check` after fixing to confirm.

---

#### Gate 3: Gitleaks (Secret Scanning)

```bash
make gitleaks
```

**Requires**: Docker running locally.
**Pass**: No leaks found (exit 0).
**Fail**: Lists files and line numbers with potential secrets.

**If Docker is not available**: Skip and note in the report. Warn the user to run it manually before pushing.

**Remediation**: Remove or rotate the leaked secret — never add to `.gitignore`.

---

#### Gate 4: SonarQube Local Analysis

```bash
make sonar-local
```

**Requires**: Node.js + SonarQube running locally (`scripts/sonar/run-local-sonar.js`).
**Pass**: Analysis complete with no new Blocker/Critical issues.
**Fail**: Reports issues by severity (Blocker > Critical > Major).

**If SonarQube is not available**: Skip and note in report. Tag for CI pipeline verification.

Fix Blocker and Critical issues before merging. Major issues should be tracked.

---

#### Gate 5: Tests (Only When Explicitly Requested)

```bash
source .venv/bin/activate && make test
```

Run only if the user says "run tests", "run all gates including tests", or `make verify`.

---

### Step 3: Report Results

After all gates complete, produce a summary table:

```
## QA Gate Report

| Gate        | Status    | Notes                        |
|-------------|-----------|------------------------------|
| Ruff        | ✅ PASS   |                              |
| License     | ✅ PASS   |                              |
| Gitleaks    | ✅ PASS   |                              |
| SonarQube   | ⚠️ SKIP   | Docker not available         |
| Tests       | ➖ N/A    | Not requested                |

**Overall: READY / BLOCKED**
```

**Status codes**:
- `✅ PASS` — gate passed cleanly
- `❌ FAIL` — gate failed, blocking commit/PR
- `⚠️ SKIP` — tool unavailable, manual verification required
- `➖ N/A` — gate not in scope for this run

If **BLOCKED**, list required fixes before the user can proceed.

---

## Gate Scoping

Default run: gates 1–4 (tests excluded). User can narrow or expand scope:

| Request | Gates to run |
|---------|-------------|
| "quick check" | Ruff + License only |
| "full verify" / "make verify" | All gates including tests |
| "check linting" | Ruff only |
| "check secrets" | Gitleaks only |
| "run sonar" | SonarQube only |

---

## After QA Gates Pass

Once all required gates pass, suggest next steps:

```
✅ All quality gates passed.

Suggested next steps:
- /codemie-mr — commit and push your branch
- /gitlab-mr-code-review — request a code review
```

---

## Integration Points

| Skill | When |
|-------|------|
| `tech-lead` | After implementation → suggests `qa-lead` as next step |
| `codemie-mr` | Call after `qa-lead` passes |
| `gitlab-mr-code-review` | Call after `qa-lead` passes |
