# QA Gate Report — EPMCDME-15515 fix-parallel-dispatch-cost

**Branch**: EPMCDME-15515-15512_fix-dispatch-cost-and-timeline
**Runner**: poetry (commands run directly; `make` is not installed on this workstation)
**Started**: 2026-10-06
**Status**: PASSED for the ticket delta (scoped run; full-suite gates not re-run in the check round)

## Gates

| Gate | Status | Command | Notes |
|------|--------|---------|-------|
| lint | PASS | `poetry run ruff format <changed files>`; `poetry run ruff check <changed files>` | reader.py, test_reader.py, test_reader_live_dispatches.py |
| unit (scoped) | PASS for delta | `poetry run pytest tests/codemie/repository/cli_analytics/ tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py tests/codemie/rest_api/models/test_cli_analytics_models.py -n0` | 784 passed, 16 failed; the 16 (test_dirty.py 14, test_settings.py 2) fail identically with the changes stashed |
| live dispatch tests | PASS (6/6) | same run with `TEST_LOCAL_PG_URL=postgresql://postgres:password@localhost:55432/postgres` | none skipped; test rows cleaned up |
| real-data check | PASS | new query over every session in the local analytics DB | 8 sessions, 35 agent dispatches, 0 with NULL cost |
| API check | PASS | `GET /v1/analytics/cli-analytics/sessions/52d226aa-da7e-4fa3-8903-fb9c81dc1ecf` | per-agent values equal their api_request groups; session total equals all api_request events |
| test harness | PASS | `uvx codemie-test-harness run sanity-api` (2026-10-06, local backend serving this branch) | 191 passed, 10 skipped, 1 rerun in 886.97s; summary reported by the user |
| license, build, secret scan | NOT RE-RUN | — | no dependency, build or secret-relevant change in this round |
| UI gate | SKIPPED | — | no UI files in the delta |

## Environment note

A host PostgreSQL also listens on `localhost:5432`, so the default `TEST_LOCAL_PG_URL` fails authentication and the
live tests skip silently. The run used a temporary proxy to the docker `postgres` service:

```bash
docker run -d --rm --name pg-proxy-15515 --network codemie-dev_codemie-network -p 55432:5432 \
  alpine/socat tcp-listen:5432,fork,reuseaddr tcp-connect:postgres:5432
```

## Drift signal

no

## After rebase onto main `dde2c5ad1` (2026-10-06)

Only overlap with main: `cli_analytics_handler.py` and its tests (EPMCDME-15228 `get_users`); merged without
conflicts, both sides kept. The branch patch is identical to the pre-rebase one apart from one context line.

| Gate | Status | Command | Notes |
|------|--------|---------|-------|
| lint | PASS | `poetry run ruff format --check` / `poetry run ruff check` on all changed `.py` | 7 files |
| license | PASS | `poetry run python scripts/license_headers/check_license_headers.py --check --quiet` | 2371 files, 0 missing |
| related suites | PASS for delta | same file list as above without `test_startup_integration.py`, `-n0`, live DB via the socat proxy | 896 passed, 16 failed (test_dirty 14, test_settings 2, pre-existing) |
| startup integration | ENV | `poetry run pytest tests/codemie/rest_api/test_startup_integration.py` | collection error: `websocket` missing in the local venv; file updated by main, untouched by this branch |
| session 52d226aa | PASS | rebased reader + `_build_dispatches` against the local analytics DB | identical to the pre-rebase API: 7661 / 57102 / $0.1317482 and 8597 / 27502 / $0.0631483 |
| test harness | PASS | `uvx codemie-test-harness run sanity-api` (rebased backend, serves `src` with `--reload`) | 197 passed, 4 skipped, 5 rerun in 1178.18s; summary reported by the user |

## After rebase onto main `34e714be5` (2026-10-06)

Main added EPMCDME-15158 (anyio), EPMCDME-15418 (PyJWT) and EPMCDME-15402 (script tool calls); none touches this
branch's files. Rebased without conflicts; every commit is identical in `git range-diff` and the branch patch is
byte-identical. The local venv was synced to the new lock (anyio 4.15.1, PyJWT 2.15.1; `pymssql` does not build on
Windows and is not used here).

| Gate | Status | Command | Notes |
|------|--------|---------|-------|
| lock | PASS | `poetry check --lock` | warnings about `[tool.poetry]` sections only |
| compile | PASS | `poetry run python -m compileall -q src` | |
| lint | PASS | `poetry run ruff format --check` on changed files; `poetry run ruff check src tests` | |
| license | PASS | `poetry run python scripts/license_headers/check_license_headers.py --check --quiet` | 2405 files, 0 missing |
| build | PASS | `poetry build` | codemie-0.8.0 wheel and sdist |
| related suites | PASS for delta | as in the previous section | 896 passed, 16 failed (test_dirty 14, test_settings 2, pre-existing); `test_startup_integration.py` does not collect (`websocket` missing locally) |
| session 52d226aa | PASS | rebased reader + `_build_dispatches` against the local analytics DB | 7661 / 57102 / $0.1317482 and 8597 / 27502 / $0.0631483, unchanged |
| test harness | CARRIED | — | last run on the `dde2c5ad1` base (197 passed, 4 skipped, 5 rerun); the newer main commits do not touch this branch's code |

## After rebase onto main `eb9523016` (2026-10-06)

Main added EPMCDME-15381 (conversation save), which touches no file of this branch but extends the shared
`tests/codemie/conftest.py`. Rebased without conflicts; all 10 commits identical in `git range-diff`, branch patch
byte-identical. The local venv got the locked `websocket-client` 1.9.0 and `yarl` 1.18.3 (missing locally; the new
conftest import chain needs `websocket`).

| Gate | Status | Command | Notes |
|------|--------|---------|-------|
| lock, compile, lint, license, build | PASS | `poetry check --lock`; `compileall -q src`; `ruff format --check` / `ruff check src tests`; license check; `poetry build` | 2409 files, 0 missing headers |
| cognitive complexity | PASS | `uvx complexipy` on the handler | `_build_dispatches` 5, `_bar_position` 4 |
| related suites | PASS for delta | cli_analytics repository, models, routers, handlers, jobs; live DB via the socat proxy | 896 passed, 16 failed (test_dirty 14, test_settings 2, pre-existing) |
| handlers | PASS | `poetry run pytest tests/codemie/service/analytics/handlers/ -n0` | 353 passed |
| startup integration | PRE-EXISTING | same file on a clean `origin/main` worktree | 9 failed, 15 errors on main as well; untouched by this branch |
| session 52d226aa | PASS | reader + `_build_dispatches` against the local analytics DB | unchanged: 7661 / 57102 / $0.1317482 and 8597 / 27502 / $0.0631483 |

## After rebase onto main `5cb93d53f` (2026-10-06)

Main added EPMCDME-12505 (assistants with a deleted datasource), which touches no file, dependency or shared test
setup of this branch. Rebased without conflicts; all 11 commits identical in `git range-diff`, branch patch
byte-identical. Same gates as the previous section, same results: lock, compile, lint, license (2411 files, 0
missing), build and complexity pass; related suites 896 passed with the 16 pre-existing failures; handlers 353
passed; session 52d226aa unchanged.

## After rebase onto main `1d91ef34f` (2026-10-07)

Main added 12 commits. One overlaps this branch: EPMCDME-15446 (CLI analytics repositories by project) changed
`reader.py` (project filter, outside `get_session_detail_dispatches`), `cli_analytics_handler.py` (repository
buckets moved to `_build_repository_rows`, away from the dispatch helpers) and appended two `window_params` tests to
`test_reader.py`. The first two merged without conflicts. `test_reader.py` conflicted at the end of the file in
commits 1 and 3 of the branch; every branch change to that file is appended after the base's last test, so it was
resolved as main's file followed by the branch's test section, unchanged.

| Check | Status | Notes |
|-------|--------|-------|
| main-only files (295) | PASS | byte-identical to `origin/main` |
| branch-only files (20) | PASS | byte-identical to the pre-rebase branch |
| overlap files | PASS | `test_reader.py` = main's file + the branch's unchanged test section; in `reader.py` and the handler both main's and the branch's +/- lines are identical to their sources |
| range-diff | PASS | 10 of 12 commits identical; commits 1 and 3 differ only in hunk context (main's tests precede the branch's) |
| lock, compile, lint, license, build, complexity | PASS | venv synced to the lock (datasets 5.0.1, fsspec 2026.6.0, GitPython 3.1.62, pypdf 6.19.0); license 2438 files, 0 missing; `_build_dispatches` 5 |
| related suites | PASS for delta | 906 passed (main's new filter, ordering and wiring tests included), 16 failed (test_dirty 14, test_settings 2, pre-existing) |
| handlers | PASS | 352 passed (main reshaped `test_cli_analytics_handler_ordering.py`) |
| session 52d226aa | PASS | unchanged: 7661 / 57102 / $0.1317482 and 8597 / 27502 / $0.0631483 |
