# QA Gate Report — epmcdme-12959-budget-stale-allocation

**Branch**: EPMCDME-12959
**Runner**: poetry (guide-first via .ai-run/guides/quality-gates.md)
**Started**: 2026-06-22T09:00:00Z
**Status**: BLOCKED (gitleaks — pre-existing untracked files; see detail)

## Gates

| Gate            | Status  | Command               | Notes |
|-----------------|---------|-----------------------|-------|
| lint            | PASS    | `make ruff`           | 1984 files checked, all pass |
| build           | PASS    | `make build`          | codemie-0.8.0 wheel + sdist built |
| license-headers | PASS    | `make license-check`  | 1777 files checked, 0 missing headers |
| gitleaks        | FAIL    | `make gitleaks`       | 2 findings — both in **untracked, uncommitted** files not in this branch's diff (see below) |
| unit tests      | PASS    | `make test`           | 11819 passed, 115 skipped, 235 warnings |
| coverage        | SKIPPED | `make coverage`       | Not requested |
| sonar-local     | SKIPPED | `make sonar-local`    | Not requested |

## Failure detail

### gitleaks (exit 2)

Both findings are in files that are **untracked by git** (`git ls-files` returns error for both) and **not present in `git diff main..HEAD`**. They are pre-existing developer workspace files, not introduced or modified by this branch.

| File | Rule | Pre-existing? |
|------|------|---------------|
| `.codex/config.toml:15` | `generic-api-key` (CONTEXT7_API_KEY) | Yes — `.codex/` listed as untracked in `git status` |
| `.mcp.json.lock:11` | `generic-api-key` (BRAVE_API_KEY) | Yes — not tracked by git |

These keys appear to be personal developer tool credentials (Codex config, MCP lock file) that live in the working directory but are never committed to the repository. The gate technically fails because gitleaks scans the full working directory including untracked files.

**This branch introduces no new secrets.**

## Drift signal

no — spec, plan, and implementation are consistent. The formula `a.allocated_max_budget if enforce_limit else budget.max_budget` is applied at both sites as specified. No type or method signature drift observed.
