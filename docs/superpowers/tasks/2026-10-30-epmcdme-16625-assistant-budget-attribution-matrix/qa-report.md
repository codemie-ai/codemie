# QA Gate Report — epmcdme-16625-assistant-budget-attribution-matrix

**Branch**: EPMCDME-16625_assistant-budget-attribution-matrix (HEAD 8501afdec)
**Runner**: poetry (gates from `.ai-run/guides/quality-gates.md`)
**Started**: 2026-11-02T02:09:33Z
**Status**: BLOCKED (test gate fails; every failing test also fails on origin/main — see Failure detail). The user accepted this result for handoff on 2026-11-02 without rerunning chunk 3.

`make` is not on PATH in this shell, so each gate ran the exact command of its Makefile target.

## Gates

| Gate | Source | Status | Duration | Command | Notes |
|---|---|---|---|---|---|
| lint | guide | PASS | ~20s | `poetry run ruff format && poetry run ruff check --fix && poetry run ruff check` | 3156 files unchanged; all checks passed |
| build | guide | PASS | ~15s | `poetry build` | Built codemie-0.8.0-py3-none-any.whl |
| license | guide | PASS | ~10s | `poetry run python scripts/license_headers/check_license_headers.py --check --quiet` | 2844 files, 0 missing headers |
| secrets | guide | PASS | 2m13s | `docker run --rm -v "D:/Projects/codemie:/workspace" ghcr.io/gitleaks/gitleaks:v8.30.1 dir ... /workspace` | "scanned ~65.66 MB ... no leaks found". Mount changed from `./` to the absolute path (Docker on Windows rejects relative bind mounts) |
| unit (make test) | guide | FAIL | 10m19s | `poetry run pytest tests/` | Run 1 (before env repair): 214 failed, 25139 passed, 331 skipped, 203 errors; identical failure set on origin/main (d2d59a21e), 0 failures only on this branch. Rerun 2 (after `poetry install --only-root` restored the editable install), in chunks: `tests/codemie/service` 9 failed / 8320 passed / 2 errors; `tests/codemie --ignore=tests/codemie/service` 49 failed / 11306 passed / 7 errors; all 67 failing ids are in the origin/main failure set (0 only on this branch). Chunk 3 (`tests --ignore=tests/codemie`) was stopped at 91% because the machine ran low on memory: 150 F markers up to that point, against 153 origin/main failures in the same directories. Not confirmed id by id |
| cli-analytics-live | guide | SKIPPED | — | `make test-cli-analytics-live` | Skip if: diff does not touch `src/codemie/repository/cli_analytics/postgres/` (it does not) |
| coverage | guide | SKIPPED | — | `poetry run pytest tests/ --cov ...` | Skip if: coverage not requested |
| sonar | guide | SKIPPED | — | `node scripts/sonar/run-local-sonar.js` | self-skips: "Skipping Sonar scan because SONAR_TOKEN is not set." — no SONAR_TOKEN env and no `.env.local`; set SONAR_TOKEN to run |
| test-harness | guide | PASS | 11m46s | `uvx codemie-test-harness run sanity-api -n 2 --reruns 10` (run by the user) | Same `sanity-api` suite as `make test-harness`, with explicit worker/rerun limits per local-verification.md. Backend rebuilt from HEAD 8501afdec (`docker compose up -d --build codemie`); LiteLLM restarted first. Summary: `196 passed, 4 skipped, 1 rerun in 706.69s (0:11:46)` — paste into the MR `## Test harness` section |
| pre-commit | hook | PASS | — | `scripts/git-hooks/pre_commit.sh` (ruff + license) | Covered by the lint and license gates above |
| commit-msg | hook | PASS | — | `scripts/git-hooks/commit_msg.sh` | All 16 branch commit subjects match `^EPMCDME-[0-9]+:` |
| pre-push | hook | FAIL | — | `scripts/git-hooks/pre_push.sh` (pytest + sonar-local) | Same pytest result as the unit gate; sonar part self-skips (no token) |
| gitleaks-staged | hook | PASS | — | `scripts/git-hooks/validate_secrets.sh` | Nothing staged; the full-tree gitleaks scan above is a superset |
| pre-ci-guard | ci | N/A | — | `.gitlab-ci.yml` wait-ci-init | Waits for external `ci-pipeline` / `compliance-report` contexts; not runnable locally |
| ui | guide | SKIPPED | — | (n/a) | no UI surface changed |

## Failure detail

Both runs were the same command (`poetry run pytest tests/`) on this machine. Their sorted FAILED/ERROR id sets (417 ids each) are identical: `comm` shows 0 ids only on the branch and 0 only on origin/main. The largest groups:

- PostgreSQL unavailable (`alembic upgrade head failed`): marketplace_management services and routers, alembic marketplace migrations, router_guards guarded app, cli_analytics postgres settings/dirty, conversation pagination.
- Windows-only behaviour: code_executor filesystem policy and sandbox guard, git/svn loader MIME checks, git hook script tests (`tests/scripts/*`), Unix-only `resource` module.
- MCP auth bridge tests (`tests/enterprise/mcp_auth/*`) and a few OAuth/script-tool tests, also failing on origin/main.

None of these files is touched by this branch. The branch's own suites (budget, llm_service, enterprise/litellm, monitoring, usage_attribution, triggers, workflows, datasource, agents, security) pass.

## Drift signal

no — implementation matches spec.md and plan.md, apart from the spec sections already corrected in commit 8501afdec (IDP username workspace, `budget_fallback_from` as the target carrier) and the username identity decision recorded in decisions.jsonl.
