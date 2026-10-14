# QA Gate Report — EPMCDME-15512 fix-subagent-duration-timeline

**Branch**: EPMCDME-15512_fix-subagent-duration-timeline
**Runner**: poetry (Makefile targets run manually: `make` is not installed; commands copied verbatim from the Makefile)
**Started**: 2026-10-06T00:02:28Z
**Status**: PASSED (for the ticket delta; full-suite failures outside the delta are pre-existing)

## Gates

| Gate | Status | Command | Notes |
|------|--------|---------|-------|
| lint | PASS | `poetry run ruff format`; `poetry run ruff check --fix`; `poetry run ruff check` | 2646 files unchanged; no violations |
| license | PASS | `poetry run python scripts/license_headers/check_license_headers.py --check --quiet` | 2356 files, 0 missing headers |
| build | PASS | `poetry build` | codemie-0.8.0-py3-none-any.whl |
| secret scan | PASS | `docker run … ghcr.io/gitleaks/gitleaks:v8.30.1 dir --config=/workspace/.gitleaks.toml` | no leaks (MSYS_NO_PATHCONV=1 needed on Windows) |
| unit | PASS for delta | `poetry run pytest tests/` | 11296 passed; 444 failed and 446 errors are in modules outside the delta; 16 cli_analytics failures (test_dirty 14, test_settings 2) are identical to the pre-change baseline |
| live dispatch tests | PASS (4/4) | `pytest tests/codemie/repository/cli_analytics/postgres/test_reader_live_dispatches.py` in a temporary container on codemie-dev_codemie-network | run against the analytics DB; rows cleaned up |
| test harness | PASS | `uvx codemie-test-harness run sanity-api` | 197 passed, 4 skipped, 4 rerun, exit 0 |
| UI gate | SKIPPED | — | no UI files in the Python delta; UI behaviour verified manually by the user in codemie-ui |

## Failure detail

Full-suite failures are in tests/codemie/triggers, tests/codemie/service/project, tests/codemie/service/test_sharepoint_token_refresh.py, tests/codemie_tools/git, tests/enterprise, tests/codemie/agents/aws_bedrock and others. None of these modules is in the delta.

## Drift signal

no

## Check round (2026-10-06, agent.tool.start anchor)

| Gate | Status | Command | Notes |
|------|--------|---------|-------|
| lint | PASS | `poetry run ruff format <changed files>`; `poetry run ruff check <changed files>` | reader.py, test_reader.py, test_reader_live_dispatches.py |
| unit (scoped) | PASS for delta | `poetry run pytest tests/codemie/repository/cli_analytics/ tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py tests/codemie/rest_api/models/test_cli_analytics_models.py -n0` | 784 passed; 16 pre-existing failures (test_dirty 14, test_settings 2), identical without the change |
| live dispatch tests | PASS | same run with `TEST_LOCAL_PG_URL=postgresql://postgres:password@localhost:55432/postgres` (temporary socat proxy to the docker `postgres` service; a host PostgreSQL also listens on 5432) | none skipped |
| API check | PASS | `GET /v1/analytics/cli-analytics/sessions/52d226aa-da7e-4fa3-8903-fb9c81dc1ecf` | offsets 7661 / 8597, durations 57102 / 27502 ms, equal to `SubagentStop − agent.tool.start` |
| related suites | PASS for delta | `poetry run pytest tests/codemie/repository/cli_analytics/ tests/codemie/rest_api/models/test_cli_analytics_models.py tests/codemie/rest_api/routers/test_cli_analytics_ingest.py tests/codemie/rest_api/routers/test_cli_analytics_storage_wiring.py tests/codemie/rest_api/test_startup_integration.py tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py tests/codemie/service/analytics/handlers/test_cli_analytics_handler_ordering.py tests/codemie/service/analytics/test_cli_analytics_jobs.py -n0` | 894 passed, 20 failed, 14 errors, all pre-existing: test_dirty 14, test_settings 2, test_startup_integration 4 failed + 14 errors (missing `yarl`/`websocket` in the local venv; identical with the changes stashed) |
| overlap tests (AC3) | PASS | the run above includes the 2 new live and 2 new handler tests | mutation-checked: fail with the original bug |
| test harness | PASS | `uvx codemie-test-harness run sanity-api` (2026-10-06, local backend serving this branch) | 191 passed, 10 skipped, 1 rerun in 886.97s; summary reported by the user |
| license, build, secret scan | NOT RE-RUN | — | SQL and test changes only; see the gates above |

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

## Sonar quality gate (2026-10-06)

The MR pipeline's Sonar gate failed with one new critical code smell: `LocalAnalyticsHandler._build_dispatches`
cognitive complexity 16 > 15 (it was 9 on main; the agent bar placement added the rest). The placement moved to a
module helper `_bar_position` with the same behaviour.

| Gate | Status | Command | Notes |
|------|--------|---------|-------|
| cognitive complexity | PASS | `uvx complexipy src/codemie/service/analytics/handlers/cli_analytics_handler.py` | `_build_dispatches` 5, `_bar_position` 4 |
| lint | PASS | `poetry run ruff format` / `poetry run ruff check` on the handler | |
| handler tests | PASS for delta | `poetry run pytest tests/codemie/service/analytics/handlers/ -n0` | 352 passed, 1 failed: `test_budget_usage_service.py::TestRefreshFromLitellmSpendDedup::test_skips_insert_when_spend_unchanged`, which fails identically without the refactor |
| related suites | PASS for delta | as above, live DB via the socat proxy | 896 passed, 16 failed (test_dirty 14, test_settings 2, pre-existing) |
| session 52d226aa | PASS | reader + `_build_dispatches` against the local analytics DB | unchanged: 7661 / 57102 / $0.1317482 and 8597 / 27502 / $0.0631483 |

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
