# QA Gate Report — epmcdme-16835-router-max-output-tokens

**Branch**: EPMCDME-16835_router-max-output-tokens
**Runner**: poetry (Makefile targets run as their underlying commands; `make` is not available in Git Bash on this machine)
**Started**: 2026-11-05
**Status**: PASSED (nothing local blocks this change; full `make test` is not green on this Windows machine for reasons that also fail on `main`)

## Gates

| Gate | Source | Status | Duration | Command | Notes |
|------|--------|--------|----------|---------|-------|
| ruff | guide | PASS | — | `make ruff` | format + check clean |
| build | guide | PASS | — | `make build` | |
| license-check | guide | PASS | — | `make license-check` | |
| gitleaks | guide | PASS | — | `make gitleaks` (`docker run ... ghcr.io/gitleaks/gitleaks:v8.30.1 dir --no-banner --redact --config=/workspace/.gitleaks.toml /workspace`) | "no leaks found", exit 0 (first attempt self-skipped while Docker was down; rerun after Docker restart) |
| affected | guide | PASS | — | `poetry run pytest -n 0 tests/codemie/service/llm_service tests/enterprise/litellm` | 774 passed in the touched areas |
| test | guide | PASS (no regressions) | 46m51s | `make test` | 221 failed, 29078 passed, 661 skipped, 16 errors. Every failure re-run with `--lf` on `origin/main` also fails there (Windows/env: bash hook scripts, sandbox paths, MCP auth, live CLI-analytics Postgres) except one code-executor timing test that passed 3/3 when run alone (flaky). None touch the changed code. |
| test-harness | guide | PASS | 13m51s | `make test-harness` (`uvx codemie-test-harness run sanity-api -n 2 --reruns 10`) | Rerun after the AC extension and rebase onto `main` (HEAD `abb55260a`): 196 passed, 4 skipped, 2 rerun. First run (router fix only): 196 passed, 4 skipped, 1 rerun in 12m11s |
| test-cli-analytics-live | guide | N/A | — | `make test-cli-analytics-live` | change does not touch `src/codemie/repository/cli_analytics/postgres/` |
| coverage | guide | N/A | — | `make coverage` | not requested |
| sonar-local | guide | SKIPPED | — | `make sonar-local` | no Sonar token configured locally; enable by setting the Sonar token per the `sonar-local` target |
| codemie-gitleaks | hook | SKIPPED | — | `scripts/git-hooks/validate_secrets.sh` | pre-commit hooks not installed locally; the equivalent full-tree `make gitleaks` passed |
| ui | guide | SKIPPED | — | (n/a) | no UI surface changed |

## Scope extension (2026-11-06, AC1-AC4)

Gates re-run after the status-report extension (Tasks 4-7) and the review fixes:

| Gate | Status | Command | Notes |
|------|--------|---------|-------|
| ruff | PASS | `make ruff` | format + check clean on `src/` and `tests/` |
| build | PASS | `make build` | |
| license-check | PASS | `make license-check` | 2994 files, 0 missing headers |
| gitleaks | PASS | `make gitleaks` | "no leaks found", exit 0 |
| affected | PASS | `poetry run pytest tests/codemie/agents tests/codemie/core/test_exceptions.py tests/codemie/service/llm_service tests/enterprise/litellm <codemie_tools jira tests>` | 2088 passed, 119 skipped after the rebase onto `main` |
| test | NOT RE-RUN | `make test` | killed by Claude Code for low memory on this machine (no test failure); not restarted. Covered by the affected run above plus the first-run full suite |
| test-harness | PASS | `make test-harness` | 196 passed, 4 skipped, 2 rerun in 13m51s, backend rebuilt from HEAD `abb55260a` |

Code review (extension): `code-review.final` raised CR-001..CR-009 (report-call timeout, read calls treated as created, interrupted-call output, invented-key guard, run-log robustness, prompt size cap, empty-log wording, non-HTTP args shown to users, test/spec gaps); all fixed in commit `4439463c8`; `code-review.check` approved. Deferred: skip the report call when the listener has disconnected; end-to-end supervisor test.

## Failure detail

None attributable to this change. Full-suite failures are pre-existing on `main` on this machine (see `test` row).

## Local stack notes

- Backend rebuilt from this branch; healthy, loaded 42 LiteLLM chat models. No `router_output_limit` log line because the local LiteLLM config declares no auto-router.
- LiteLLM container reports `unhealthy` only because its Docker healthcheck calls `wget`, which the image lacks (`/bin/sh: wget: not found`); the backend reaches it fine.
- Local DB `codemie_13933`: EPMCDME-16625 migration `b5c6d7e8f901` rolled back (`idp_user_snapshots` dropped) and alembic stamped to `main` head `b7e2d4f6a8c1`.

## Owed to CI / reviewers

- `sonar-local` (Sonar quality gate) — not run locally.
- MR description must include the `## Test harness` summary above and ask a reviewer to post `/sanity` if required by the repository rule.

## Drift signal

no
