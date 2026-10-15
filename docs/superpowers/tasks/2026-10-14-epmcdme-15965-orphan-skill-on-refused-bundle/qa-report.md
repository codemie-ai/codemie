# QA Gate Report — epmcdme-15965-orphan-skill-on-refused-bundle

**Branch**: EPMCDME-15965_no-orphan-skill-on-refused-bundle
**Runner**: poetry (gates from `.ai-run/guides/quality-gates.md`)
**Started**: 2026-10-14
**Status**: BLOCKED (strict guide criteria: `make test` exit code 2). All failures are pre-existing and unrelated to this change, see below.

## Gates

| Gate | Status | Duration | Command | Notes |
|------|--------|----------|---------|-------|
| lint | PASS | 16s | `make ruff` | format, check --fix, check all clean |
| build | PASS | 43s | `make build` | poetry build ok |
| license-check | PASS | 7s | `make license-check` | |
| secret scan | PASS | 92s | `make gitleaks` | Docker available |
| tests | FAIL | 268s | `make test` | 64 failed, 20597 passed, 198 skipped, 1 error |
| coverage | N/A | — | `make coverage` | not requested |
| static analysis | N/A | — | `make sonar-local` | no Sonar credentials / not requested |
| test harness | N/A | — | `make test-harness` | MR-time gate, needs docker stack + fixtures |
| ui | SKIPPED | — | (n/a) | no UI surface changed |

## Failure detail

Failing files (none touch skill code): `tests/enterprise/mcp_auth/*` (53), `tests/codemie/repository/cli_analytics/postgres/test_reader_live_dispatches.py` (7), `tests/codemie/service/oauth/adapters/test_provider_adapters.py` (2), `tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py` (2), `tests/codemie/service/test_workspace_script_bridge.py` (1), `tests/codemie/rest_api/router_guards/test_openapi_snapshot.py` (1), plus 1 error in `tests/codemie/service/security/test_logout_token_cleanup_mock_tms.py`.

Baseline comparison: with `src/codemie/service/skill_service.py` reverted to origin/main and the same files re-run, the result is the same 64 failures (378 passed). The 1 error did not reproduce in that targeted re-run (it is likely order/environment dependent). So the failures pre-exist this change.

All tests this change touches pass: `TestValidateCompanionPayloads`, `TestUploadCompanionFiles`, `TestCreateSkillWithCompanionFiles`, `TestCreateSkillNoOrphan`, `TestUpdateSkillCompanionFiles`, and the skill router tests.

## Drift signal

no
