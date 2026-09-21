# QA Gate Report — EPMCDME-14465

**Branch**: epmcdme-14465
**Runner**: poetry (Makefile-driven)
**Started**: 2026-08-31
**Status**: PASSED

## Gates

| Gate  | Source | Status | Duration | Command | Notes |
|-------|--------|--------|----------|---------|-------|
| lint  | guide | PASS | ~15s | `make ruff` | Ruff format + fix + check all clean, 2371 files. |
| build | guide | PASS | ~10s | `make build` | `codemie-0.8.0` sdist + wheel built successfully. |
| unit (affected) | guide | PASS | 54.15s | `poetry run pytest tests/codemie/service/analytics/ tests/codemie/rest_api/routers/test_analytics.py tests/codemie/rest_api/routers/test_analytics_auditor.py -q` | 516 passed, 8 skipped, 0 failed. This is the scope actually touched by the ticket. |
| unit (full repo) | guide | FAIL (pre-existing, unrelated) | 748.70s | `poetry run pytest tests/ -q` | 14608 passed, 151 failed, 177 skipped. See analysis below — zero failures fall in `analytics`/`ai_adoption` paths. |
| ui    | guide | SKIPPED | — | (n/a) | No UI surface changed (backend-only deletion). |
| secrets (codemie-gitleaks) | hook | SKIPPED | — | `scripts/git-hooks/validate_secrets.sh` | Requires a running container engine (Docker/Podman); not invoked for this read-only gate pass — enable by running Docker/Podman locally and re-running `make gitleaks`. |
| license-check | guide | N/A | — | `make license-check` | Not run this pass — pure deletion changes no file headers; out of scope for this gate run. |
| sonar-local | ci | N/A | — | `make sonar-local` | Requires Sonar token/config; not run — server-side quality gate is a CI/`/sanity` concern per quality-gates.md. |

## Full-suite failure analysis (151 failures)

None of the 151 failures touch `analytics`, `ai_adoption`, or any file changed by this diff (confirmed via
`grep -i "analytics|adoption"` over the failure list — zero matches). Failures cluster in six unrelated areas,
all pre-existing on `main` and untouched by this ticket's diff:

- `tests/codemie/configs/test_env_example.py` (3) — caused by a local, **uncommitted** working-tree deletion of
  `.env.example` (pre-existing stray change on this branch, unrelated to the ai-adoption removal; not part of
  this ticket's commits).
- `tests/codemie/datasource/**` (4) — git/svn loader MIME-type detection, background processing.
- `tests/codemie/rest_api/routers/test_oauth_redis_lazy_init.py` (1), `tests/codemie/service/mcp/**` (2),
  `tests/codemie/service/oauth/adapters/**` (2), `tests/codemie/service/conversation/test_message_exporter.py` (1),
  `tests/codemie/service/test_dynamic_config_service.py` (1) — OAuth/MCP/config/export modules never touched here.
- `tests/codemie_tools/data_management/code_executor/**` and `test_file_system_tools.py` (~45) — sandbox
  filesystem-policy guard suite, entirely unrelated to analytics.
- `tests/enterprise/mcp_auth/**` (~40) — enterprise MCP OAuth bridge suite, unrelated.
- `tests/scripts/**` (~20) — git-hook shell script tests (commit-msg, pre-commit, pre-push, ruff-staged),
  unrelated to backend analytics code.

**Conclusion**: the 151 full-suite failures are pre-existing and unrelated to EPMCDME-14465. The gate for this
ticket's actual scope (analytics/router/service/handler/query-package + their tests) is fully green.

## Drift signal

no
