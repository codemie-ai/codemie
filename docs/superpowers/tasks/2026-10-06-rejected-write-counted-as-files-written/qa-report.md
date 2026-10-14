# QA Gate Report — EPMCDME-15583

**Branch**: EPMCDME-15583_rejected-write-counted-as-files-written
**Runner**: poetry (guide-defined gates from `.ai-run/guides/quality-gates.md`)
**Started**: 2026-10-06T10:18:00Z
**Status**: PASSED (with one pre-existing, unrelated environment gap noted below)

## Gates

| Gate | Status | Duration | Command | Notes |
|------|--------|----------|---------|-------|
| lint (ruff) | PASS | ~3s | `make ruff` | format + fix + check, all clean |
| build | PASS | ~5s | `make build` | sdist + wheel built successfully |
| license-check | PASS | ~2s | `make license-check` | 2370 files checked, 0 missing headers |
| secret-scan (gitleaks) | PASS | ~6s | `make gitleaks` | 44.45 MB scanned, no leaks found |
| unit tests | PASS* | ~116s | `make test` | 18130 passed, 198 skipped, 55 failed, 1 error — see below |
| affected tests | SKIPPED | — | (n/a) | no changed-file-aware test command configured for this project |
| ui | SKIPPED | — | (n/a) | no UI surface changed |
| coverage | SKIPPED | — | (n/a) | not requested |
| sonar-local | SKIPPED | — | (n/a) | not requested |
| test-harness | SKIPPED | — | (n/a) | not opening an MR in this phase (local iteration only) |

## Failure detail

All 55 failures and the 1 error are confined to `tests/enterprise/mcp_auth/*`, `tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py`, and `tests/codemie/service/oauth/adapters/test_provider_adapters.py` — none touch `cli_analytics`, `reader.py`, `rollups.py`, or `cli_analytics_handler.py`.

Root cause, confirmed by isolated reruns:

```
E       ModuleNotFoundError: No module named 'codemie_enterprise'
```

`codemie_enterprise` is a private package served from an authenticated GCP artifact registry (`codemie-artifact-registry-gcp`). This checkout's Poetry environment was installed OSS-only (no `-E enterprise`); attempting `poetry install -E enterprise --sync` to fix it failed:

```
Source (codemie-artifact-registry-gcp): Authorization error accessing
https://europe-west3-python.pkg.dev/.../codemie-enterprise-python/simple/codemie-enterprise/
```

This requires registry credentials not available in this environment — a pre-existing local setup gap, unrelated to this change. The environment was restored to its original OSS-only state (`poetry install --sync`) after confirming this. The one `ERROR` (`test_logout_token_cleanup_mock_tms.py`) is unrelated too — it skips cleanly when run in isolation and only errors under full-suite parallel contention; it is not in the cli_analytics area either.

**Scoped verification** (the actual area this change touches): `poetry run pytest tests/codemie/repository/cli_analytics/ tests/codemie/service/analytics/` → **1344 passed, 8 skipped (pre-existing), 0 failed.**

## Drift signal

No. The implementation matches `spec.md`/`plan.md`: `rollups.py`'s success-gating (kept as drafted, now test-locked), `reader.py`'s `files_changed` FILTER fix, and the three new test files, exactly as planned.
