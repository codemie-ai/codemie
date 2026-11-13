# QA Gate Report — epmcdme-16566-personal-project-case-insensitive

**Branch**: EPMCDME-16566_personal-project-case-insensitive
**Runner**: poetry (guide-first: `.ai-run/guides/quality-gates.md`; `make` is not on PATH in bash, so the Makefile commands were run directly)
**Started**: 2026-10-29
**Status**: INCOMPLETE — the full unit-test gate did not finish (process killed for low system memory). Everything else that ran passed.

## Gates

| Gate | Source | Status | Command | Notes |
|------|--------|--------|---------|-------|
| lint (format) | guide | PASS | `poetry run ruff format --check` | 3129 files already formatted. Non-mutating form of `make ruff` (which rewrites files). |
| lint (check) | guide | PASS | `poetry run ruff check` | All checks passed. |
| license headers | guide | PASS | `poetry run python scripts/license_headers/check_license_headers.py --check --quiet` | Checked 2819 files, 0 missing headers. |
| secret scan | guide | PASS | `docker run ... gitleaks:v8.30.1 dir --config=/workspace/.gitleaks.toml /workspace` | Really ran: "scanned ~64277574 bytes (64.28 MB) in 1m51s", "no leaks found". |
| build | guide | PASS | `poetry build` | Built codemie-0.8.0 sdist and wheel; `dist/` is gitignored. |
| commit subjects | hook (commit-msg) | PASS | regex `^EPMCDME-[0-9]+: ` over `git log origin/main..HEAD` | All 5 commits conform. |
| unit tests (full) | guide | NOT COMPLETED | `poetry run pytest tests/` | Killed by the system for low memory ("system is running low on memory"); no result. Not restarted. |
| unit tests (affected areas) | guide (scoped) | PASS with unrelated failures | `pytest tests/codemie/rest_api/security tests/codemie/service/project tests/codemie/service/llm_service tests/codemie/service/user tests/codemie/repository tests/enterprise/litellm tests/enterprise/migration tests/codemie/rest_api/routers/test_user_router.py tests/codemie/rest_api/routers/test_user_profile_router.py` | 3296 passed, 241 skipped, 17 failed — run before the fix-up commit. All 17 failures are in `tests/codemie/repository/cli_analytics/postgres/` (`test_dirty.py` edge-of-calendar cases: `OSError: [Errno 22] Invalid argument` for year 1 and 9999 timestamps on Windows; `test_settings.py` pool settings). They do not import any changed module. Not compared against `origin/main`. |
| new/updated tests after fix-up | guide (scoped) | PASS | `pytest tests/codemie/service/user/test_user_management_service_effective_default.py tests/codemie/service/project/test_personal_project_service.py tests/codemie/rest_api/security/test_user.py -n0` | 96 passed. |
| CLI analytics live SQL | guide | N/A | `make test-cli-analytics-live` | Change does not touch `src/codemie/repository/cli_analytics/postgres/`. |
| static analysis (sonar) | hook (pre-push) / guide | N/A | `make sonar-local` | Needs Sonar server and credentials; not available locally. CI settles it. |
| test harness | guide (MR gate) | N/A | `make test-harness` | Run by the user before the MR; budget routing code is touched, so the MR asks a reviewer to post `/sanity`. |

## Failure detail

None of the gates that completed failed. The full-suite run produced no output because the process was reaped.

## Drift signal

no
