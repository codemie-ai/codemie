# QA Gate Report — 2026-07-30-epmcdme-13747-pre-push-heavy-hooks

**Branch**: EPMCDME-13747_pre-push-heavy-hooks
**Runner**: poetry
**Started**: 2026-07-31T00:00:00Z
**Status**: BLOCKED (pre-existing failures — see notes)

## Gates

| Gate    | Status  | Duration | Command                  | Notes |
|---------|---------|----------|--------------------------|-------|
| lint    | PASS    | ~5s      | `make ruff`              | All checks passed! |
| build   | PASS    | ~30s     | `make build`             | codemie 0.8.0 built successfully |
| license | PASS    | ~10s     | `make license-check`     | 1987 files checked, 0 missing |
| secrets | FAIL    | ~5s      | `make gitleaks`          | **Pre-existing**: `.env:3 AZURE_OPENAI_API_KEY` — `.env` is gitignored, not tracked, not touched by this branch. CLAUDE.md explicitly forbids modifying `.env`. |
| unit    | FAIL    | ~120s    | `make test`              | **Pre-existing**: 47 failures + 23 errors in `tests/codemie/service/google_oauth/` — none of those files are touched by this branch. All 22 new `tests/scripts/` tests **PASS**. |
| ui      | SKIPPED | —        | (n/a)                    | no UI surface changed |

## Failure detail

### secrets gate — gitleaks

```
Finding:     AZURE_OPENAI_API_KEY
File:        .env
Line:        3
Commit:      (unstaged local file — not in git history)
```

**Assessment**: `.env` is listed in `.gitignore`. The key is a developer's local Azure credential. It is **not part of this branch's diff** (zero `.env` lines appear in `git diff f5de037329173dc8a9d696615d7ca05a6e61f659...HEAD`). This is an environment condition present before this branch was cut. CLAUDE.md rule: "NEVER commit, stash, or rollback files that are modified outside the current SDLC process" — `.env` is out of scope.

### unit gate — pre-existing test failures

```
FAILED tests/codemie/service/google_oauth/test_populate_credentials.py::... (47 instances)
ERROR  tests/codemie/service/google_oauth/test_populate_credentials.py::... (23 instances)
```

**Assessment**: All failures are in `tests/codemie/service/google_oauth/test_populate_credentials.py`. This branch touches zero files under `tests/codemie/` or `src/codemie/service/google_oauth/`. The failures are pre-existing in the repository baseline and are not regressions introduced by EPMCDME-13747.

**Branch-scoped test result**: 22/22 tests in `tests/scripts/` **PASS**:
- `tests/scripts/test_commit_msg_hook.py` — 8 tests PASS
- `tests/scripts/test_pre_push_hook.py` — 5 tests PASS
- `tests/scripts/test_pre_commit_fast_hook.py` — 2 tests PASS
- (7 additional pre-existing tests in `tests/scripts/` that existed before this branch also pass)

## Drift signal

no — sdlc-light no-spec run; no spec to drift from. Plan (`plan.md`) accurately reflects the delivered implementation.
