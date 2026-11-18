# QA Gate Report — epmcdme-17201-skill-slash-command-classification

**Branch**: EPMCDME-17201_skill-slash-command-classification
**Runner**: poetry (gate commands from `.ai-run/guides/quality-gates.md`)
**Host**: Windows 11, Git Bash; `make` is not installed, so Makefile recipes were run directly
**Started**: 2026-11-17T12:17Z
**Status**: BLOCKED — the full `tests/` run is red on this host for Windows-only reasons. No failure traces to this change.

## Gates

| Gate | Status | Command | Notes |
|------|--------|---------|-------|
| lint | PASS | `poetry run ruff format --check` and `poetry run ruff check` | 3587 files formatted, all checks passed. Non-mutating form of `make ruff`, so no unrelated files were reformatted. |
| build | PASS | `poetry build` | sdist and wheel for codemie 0.8.0 built; working tree unchanged. |
| license | PASS | `check_license_headers.py --check --quiet` | 3268 files checked, 0 missing headers. |
| secret scan | PASS | gitleaks v8.30.1 via Docker, repo `.gitleaks.toml` | No leaks found in 77.9 MB (2m53s). First attempt hit a Git Bash path rewrite of `/workspace`; rerun with `MSYS_NO_PATHCONV=1`. |
| unit (`make test`) | FAIL (environmental) | `poetry run pytest tests/` with `TEST_LOCAL_PG_URL` set | 30254 passed, 206 skipped, 5 xfailed, **253 failed, 202 errors** across 138 files (11m35s). Detail below. |
| affected (live DB) | PASS | `pytest -n0 tests/codemie/repository/cli_analytics/postgres/test_live_*.py` | 17 passed on PostgreSQL 17 (:35417) and 17 passed on PostgreSQL 14 (:35414), run before this report. |
| coverage / sonar / test-harness | SKIPPED | n/a | Not requested, no Sonar credentials, and no MR is being opened. |
| ui | SKIPPED | n/a | No UI surface changed. |

## Failure detail (unit gate)

The dominant error lines in the log (counts are lines in `test.log`, not tests) are all Windows-specific, and none touches the changed code:

- 197 lines: `ModuleNotFoundError: No module named 'fcntl'` (POSIX-only module; marketplace, startup and guarded-app tests).
- 28 lines: `module 'codemie.rest_api' has no attribute 'main'`, a knock-on of the same import failure.
- About 60 lines: `HostedSettings ... 'charmap' codec can't decode byte 0x8f`, a cp1252 default encoding on Windows (hosted_applications).
- 12 lines: `module 'os' has no attribute 'getuid'`.
- 17 lines: `OSError: [Errno 22] Invalid argument`, matching the 17 failures in `test_dirty.py` on year 0001 and 9999 timestamps. With 2 in `test_settings.py` these are the 19 analytics failures already known before this change.
- Other failing files (git hook scripts, sandbox guard, git loader, workspace path rules) were not each traced to a root cause; they are in areas unrelated to CLI Analytics.

Evidence that this change is not the cause:

- `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py`, `tests/codemie/repository/cli_analytics/postgres/test_reader.py` and `test_live_route_pricing.py` (the three test files this change touches) have no failures or errors in the full run.
- None of the 138 failing files references `cli_analytics_handler`, `PostgresCliAnalyticsReader`, `_dispatch_label_and_kind`, `get_invocations` or `get_class_lists`.
- A same-baseline run of the failing set was not performed. The attribution rests on the deterministic Windows-only causes above and on the import check.

Logs: `gate-logs/test.log`, `gate-logs/gitleaks.log`, `gate-logs/build.log`, `gate-logs/failing-files.txt`.

## Drift signal

no
