# QA Gate Report — epmcdme-14467-retire-aiadoption-flag

**Branch**: epmcdme-14467
**Runner**: poetry (Makefile targets)
**Started**: 2026-09-02
**Status**: PASSED

## Gates

| Gate  | Source | Status | Duration | Command | Notes |
|-------|--------|--------|----------|---------|-------|
| lint  | guide | PASS | ~15s | `make ruff` | 2393 files unchanged, all checks passed |
| build | guide | PASS | ~5s | `make build` | sdist + wheel built successfully |
| license-check | guide | PASS | ~10s | `make license-check` | 2131 files checked, 0 missing headers |
| unit  | guide | PASS (treated) | 1413s (23:33) | `make test` | 150 failed, 14802 passed, 177 skipped. All 150 failures confirmed pre-existing and unrelated to this diff (see Failure detail); user directed treating this gate as PASSED for this sub-task. |
| secrets | ci | PASS | 43.3s | `docker run --rm -v "${PWD}:/workspace" ghcr.io/gitleaks/gitleaks:v8.30.1 dir --no-banner --verbose --config=/workspace/.gitleaks.toml /workspace` (run via PowerShell) | Git Bash mistranslates the `/workspace` mount path on Windows (`open C:/Program Files/Git/workspace/.gitleaks.toml: no such file or directory`), so the `make gitleaks` invocation fails there. Running the identical docker command via PowerShell (no POSIX path rewriting) succeeded: scanned ~39.68 MB in 43.3s, no leaks found. |
| sonar | ci | N/A | — | `make sonar-local` | Not run — scope did not require full verification; local Sonar prerequisites (Node runtime, credentials) not confirmed available. |
| test-harness | guide | PASS | 2286.55s (38:06) | `make test-harness` (`uvx codemie-test-harness run sanity-api -n 2 --reruns 2`) | 189 passed, 4 skipped, 5 rerun, 0 failed. Local stack: `docker compose up -d elasticsearch postgres codemie`. See `test-harness-evidence.json`. |

## Failure detail

150 failures fall into exactly two pre-existing, environment-caused buckets — **neither touches any file this diff changed** (`config/customer/customer-config.yaml`, `docs/ai-adoption-framework.md`, `tests/codemie/configs/test_customer_config.py`):

1. **`tests/enterprise/mcp_auth/*` (majority of failures)** — `ModuleNotFoundError: No module named 'codemie_enterprise'` / `AttributeError: <module 'codemie_enterprise.mcp_auth'> has no attribute '...'`. Confirmed directly: `poetry run python -c "import codemie_enterprise"` fails with `ModuleNotFoundError: No module named 'codemie_enterprise'` in this checkout. The enterprise package is simply not installed in this local environment — an environment gap, not a code regression from this change.

2. **`tests/scripts/test_commit_msg_hook.py`, `test_pre_commit_fast_hook.py`, `test_pre_push_hook.py`, `test_ruff_staged_hook.py`** — failures show paths with stripped path separators, e.g. `/bin/bash: C:UsersEgor_PopovSourcecodemiescriptsgit-hooksco...` (backslashes silently dropped). This is a Windows/Git-Bash path-handling issue in how these shell-script tests invoke paths, unrelated to this diff — these tests exercise git-hook shell scripts, not config loading or documentation.

`tests/codemie/configs/test_customer_config.py` (the file this diff modified, including the new `test_load_config_tolerates_retired_ai_adoption_component` test) shows **zero failures** in this run — confirmed via `grep` against the full run output.

## Drift signal

no
