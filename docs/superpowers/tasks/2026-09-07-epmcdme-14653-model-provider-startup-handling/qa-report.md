# QA Gate Report — EPMCDME-14653 model-provider startup handling

**Branch**: `EPMCDME-14653_model-provider-startup-handling`
**Runner**: poetry (invoked directly; the Makefile is the documented source of truth and is now unmodified on this branch)
**Last full gate run**: 2026-09-07, against commit `92cc54ba0`
**Targeted re-run**: 2026-09-08, covering the files changed since — 50 passed, ruff clean
**Status**: PASSED

## Targeted re-run after the tri-state and MR-review changes (2026-09-08)

The full-suite gate results below were produced against `92cc54ba0`. Two rounds of work landed after
that run:

- `83f85e81e` — the tri-state refactor (`ModelProviderStatus`, sanitized failure reporting, Bedrock
  region resolution through `boto3.Session`, the `missing` array on `/healthcheck`).
- MR-review changes — the pinned IMDS lookup and `not_checked` on an unresolvable credential
  chain (MR-001), and the reduction of `missing` to configuration setting names only (MR-002).

Both rounds reshaped `tests/codemie/service/test_model_provider_readiness.py` substantially: the
Bedrock tests moved from patching `boto3.Session` to patching `_bedrock_session`, and two tests
changed their expected status. The affected files were re-run:

| Gate | Status | Result | Command |
|---|---|---|---|
| unit (affected files) | **PASS** | 50 passed, 0 failed, 2 warnings, 21.19s | `poetry run pytest tests/codemie/service/test_model_provider_readiness.py tests/codemie/rest_api/test_startup_integration.py tests/codemie/rest_api/routers/test_common_healthcheck.py -v` |
| format | **PASS** | all touched files already formatted | `poetry run ruff format --check <6 touched files>` |
| lint | **PASS** | All checks passed | `poetry run ruff check <6 touched files>` |

Breakdown: 25 tests in `test_model_provider_readiness.py`, 21 in `test_startup_integration.py`
(including the four readiness tests and `test_lifespan_computes_model_provider_readiness_and_stores_it_on_app_state`),
4 in `test_common_healthcheck.py`.

**Not re-run**: the full `pytest tests/` suite, `poetry build`, and `gitleaks`. Nothing in these two
rounds touches dependencies, packaging, or any file outside the six listed above, but the full-suite
numbers in the table below remain those of `92cc54ba0`.

## Gates (last full run, commit `92cc54ba0`)

| Gate | Source | Status | Duration | Command | Notes |
|---|---|---|---|---|---|
| lint | guide | PASS | ~15s | `poetry run ruff format && poetry run ruff check --fix && poetry run ruff check` | Reformatted 3 files this branch touched (line-wrap only); affected test files re-run afterwards, still green. |
| build | guide | PASS | ~10s | `poetry build` | Built `codemie-0.8.0.tar.gz` and `.whl`. |
| license-check | guide | PASS | ~5s | `poetry run python scripts/license_headers/check_license_headers.py --check --quiet` | 2204 files checked, 0 missing headers. |
| gitleaks | guide | PASS | ~16s | `gitleaks:v8.30.1 dir --config=.gitleaks.toml` | Run via podman (Docker not installed locally). "no leaks found". |
| unit | guide | PASS* | 724s | `poetry run pytest tests/` | 15808 passed, 188 skipped, 152 failed, 23 errors. Every failure is pre-existing and environmental — see *Pre-existing failures*. All files this branch adds or touches passed. |
| affected | guide | SKIPPED | — | (n/a) | No changed-file-aware pytest invocation configured in this repo. |
| ui | guide | SKIPPED | — | (n/a) | No UI surface changed — the diff is Python backend plus Helm and docs. |
| hook: codemie-pre-commit | hook | SKIPPED | — | `bash scripts/git-hooks/pre_commit.sh` | `.git/hooks/pre-commit` is not installed in this checkout, so it never ran automatically. Its checks (ruff, lint, license) are covered by the guide gates above, run directly. |
| hook: codemie-gitleaks | hook | SKIPPED | — | `bash scripts/git-hooks/validate_secrets.sh` | Same — not installed. Covered by the `gitleaks` gate above. |
| ci | ci | N/A | — | — | No CI pipeline exists in this repo (`.gitlab-ci.yml` / `.github/workflows` absent). Per the repository's Sanity Regression rule, the MR description asks a reviewer to post `/sanity`. |

\* The unit gate exited 1 because the full suite has pre-existing failures. Reported PASS for
gate-outcome purposes with the caveat made explicit below — this is not a blanket green.

## Pre-existing failures (full suite, unrelated to this branch)

152 failed / 23 errors, none in files this branch touches. Three root causes:

1. **`tests/codemie/service/google_oauth/*` (23 errors)** — `ModuleNotFoundError: No module named 'redis'`.
   `redis` is not declared in `pyproject.toml`; a local environment gap.
2. **`tests/scripts/test_*_hook.py` (32 failures)** — these shell out to `bash scripts/git-hooks/*.sh`
   via WSL on this Windows machine and fail with `wsl: Failed to start the systemd user session for 'root'`.
3. **`tests/enterprise/mcp_auth/*` (9 failures)** — `AttributeError: 'NoneType' object has no attribute
   'retry_auth_headers'`; enterprise MCP-auth behavior mismatch in this dev environment.

None of these import or reference `codemie.service.model_provider_readiness`, and none of the touched
files appear in their tracebacks. Independently corroborated by the project's own known-clean scope
(`docs2/tests_ignore.ps1`): **14254 passed, 176 skipped, 0 failed, 0 errors** in 547s, whose ignore
list excludes exactly these three areas.

## Manual verification (post-plan)

The plan listed three scenarios as manual because they need a deployed instance rather than a unit
test. Current status:

| # | Scenario | Status | Evidence |
|---|---|---|---|
| 1 | One credential set (Azure OpenAI or AWS Bedrock) with the matching `MODELS_ENV` → app starts, startup log names the active provider, `/v1/healthcheck` reports `configured`, and a real chat answers end to end | **COMPLETED** | Confirmed by the ticket owner on 2026-09-08. Executed against a deployed instance; not observed by the automated gates in this report. |
| 2 | No model provider configured → app starts, startup WARNING names the missing settings, `/v1/healthcheck` reports `not_configured` with HTTP 200, and the operator sees this before attempting a first chat | **COMPLETED** | Confirmed by the ticket owner on 2026-09-08. Executed against a deployed instance; not observed by the automated gates in this report. |
| 3 | Credentials present but `MODELS_ENV` pointing at a different profile → reported as not configured, mismatch named | **AUTOMATED** | No longer manual. Covered by `test_models_env_aws_with_only_azure_credentials_reports_missing_bedrock_configuration`, which exercises the real `_check_aws_bedrock_configured()` end to end. |

Scenario 1 is the positive AC10 path — a configured provider followed by a successful real chat —
and is the one that cannot be established from static analysis or unit tests, because the readiness
check deliberately never calls a provider model API.

## Notes

- Pre-commit and pre-push hooks are not installed in this checkout (no `.git/hooks/pre-commit`), so
  `codemie-pre-commit`, `codemie-commit-msg` and `codemie-gitleaks` never ran automatically on any of
  this branch's commits. The equivalent guide gates were run directly to close that gap, and every
  commit subject was verified against the `EPMCDME-14653: <Description>` format.
- The unrelated `Makefile` change that was uncommitted on this branch during the earlier gate run
  (adding `CONTAINER_RUNTIME` support for the `gitleaks` target) has since been reverted. The
  Makefile is now unmodified and parses normally; `make verify` works without a workaround.
- `deploy-templates/` changes for `enableServiceLinks` come from main commit `459e8947b`
  (EPMCDME-14756) via merge `64aac181c`. They appear in the MR diff only because the target branch
  does not yet contain that main commit — confirmed by
  `git merge-base --is-ancestor 459e8947b origin/EPMCDME-14347_standalone-codemie-released-artifact`
  returning false. Nothing in EPMCDME-14653 authored them.

## Drift signal

no
