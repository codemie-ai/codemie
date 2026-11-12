# QA Gate Report: epmcdme-16993-cli-analytics-reopen-redo

**Branch**: EPMCDME-16993_cli-analytics-reopen-redo
**Runner**: poetry (gates from `.ai-run/guides/quality-gates.md`, Makefile recipes run directly: `make` is not installed in Git Bash)
**Merge base**: bfdd2dcc1ee3a9e8779e7fc583d5c9b69fecbc56 (changes are uncommitted in the working tree)
**Status**: PASSED

## Gates

| Gate | Status | Command | Notes |
|------|--------|---------|-------|
| lint | PASS | `poetry run ruff format`; `poetry run ruff check --fix`; `poetry run ruff check` | 3590 files left unchanged, all checks passed; the set of changed files is the same before and after |
| build | PASS | `poetry build` | codemie-0.8.0 sdist and wheel built (dist/ is git-ignored) |
| license headers | PASS | `poetry run python scripts/license_headers/check_license_headers.py --check --quiet` | 3268 files, 0 missing |
| secret scan | PASS | gitleaks v8.30.1 `dir` with `.gitleaks.toml` (same image as `make gitleaks`, run with a Windows path mount) | no leaks found |
| unit (affected area) | PASS vs baseline | `poetry run pytest -n0 tests/codemie/repository/cli_analytics tests/codemie/service/analytics tests/codemie/rest_api/routers/test_cli_analytics*.py -q -p no:cacheprovider` | 19 failed, 4313 passed, 62 skipped; the failed set is identical to `baseline.txt` (17 `test_dirty.py` year 0001/9999 timestamps and 2 `test_settings.py`, Windows only) |
| live PostgreSQL 17 | PASS | `TEST_LOCAL_PG_URL=... pytest -n0 test_live_session_reopen.py test_live_route_pricing.py test_live_session_subagent_cost.py` | 63 passed |
| live PostgreSQL 14 | PASS | same, port 35414 | 63 passed |
| ui | SKIPPED | n/a | no UI surface changed (backend only); feature-verification not needed |

Not run: the full `tests/` suite (`make test`), `make test-harness` (needs the docker stack and fixtures; MR compliance gate) and `sonar-local`. The change is confined to `codemie.repository.cli_analytics`; the other suites were not exercised, so they are unverified, not passed. No Alembic migration was added.

## Drift signal

no
