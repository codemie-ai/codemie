# QA Gate Report — epmcdme-15558-skill-scripts-workspace

**Branch**: EPMCDME-15558_skill-scripts-workspace (HEAD dd10ef920)
**Runner**: poetry (gates from `.ai-run/guides/quality-gates.md`, run verbatim)
**Compared against**: 40fc9c99c, the commit before this run's work (clean worktree)
**Status**: BLOCKED by the strict guide criteria — the only failing tests are pre-existing and identical on the base commit (see below)

## Gates

| Gate | Status | Duration | Command | Notes |
|------|--------|----------|---------|-------|
| lint | PASS | 3s | `make ruff` | format, `ruff check --fix` and final `ruff check` all clean |
| build | PASS | 4s | `make build` | sdist and wheel built (`codemie-0.8.0`) |
| license | PASS | 1s | `make license-check` | 2456 files, 0 missing headers |
| secret scan | PASS | 26s | `make gitleaks` | Docker available; 124 MB scanned, no leaks |
| unit | FAIL (pre-existing) | 173s | `make test` | 96 failed, 19,285 passed, 198 skipped, 6 errors |
| affected | FAIL (pre-existing) | 22s | `poetry run pytest <24 test files changed since 40fc9c99c> -q` | 432 passed, 19 failed — all 19 in `test_skill_service.py` |
| ui | SKIPPED | — | (n/a) | no UI surface changed |
| sonar-local, coverage, test-harness | N/A | — | — | not requested; the harness needs a running docker stack and is an MR-compliance step |

## Failure detail

**unit (`make test`)** — 96 failures and 6 errors, all in files this work does not touch: `rest_api/handlers/test_assistant_handlers_a2ui.py`, `rest_api/models/test_conversation_a2ui_history.py`, `rest_api/routers/test_analytics_*`, `test_local_auth_router.py`, `service/script_tool_calls/test_exclusions.py`, `test_context.py`, `service/oauth/adapters/test_provider_adapters.py`, `service/mcp/test_toolkit_service_auth_resolver.py`, `service/test_assistant_service_methods.py` (2), `service/test_workspace_script_bridge.py`, `service/user/test_authentication_service.py`, `workflows/test_tool_node_script_step.py`, `tests/enterprise/mcp_auth/*`, `tests/enterprise/switchyard/*`, and collection errors in the a2ui tests. Causes seen: modules missing in this environment (`greenlet`, `a2ui`, `codemie_enterprise.mcp_auth.MockTokenManagementSystem`) and unrelated assertions.

Evidence that none is caused by this work:
- the failing set is identical to the full run made before the review fix-up;
- every failing file was re-run on a clean checkout of `40fc9c99c` and fails the same way (one error, `test_logout_token_cleanup_mock_tms`, appears only in a full run and the file is skipped when run alone, with or without this work);
- the count of passing tests rose from 19,276 to 19,285 with the review fix-up's new tests.

**affected** — the 19 failures are `TestAccessControl`, `TestAssistantSkillOperations`, `TestMarketplaceOperations`, `TestExportSkill`, `TestEdgeCases` access-control assertions in `tests/codemie/service/test_skill_service.py` (`ability.can(...)` returns True where False is expected). They were recorded before any source change, are the same 19 test names, and also fail on the clean `40fc9c99c` checkout. They pass or fail by test order (0 failures in the full run).

Everything else in the affected set passes, including every new or rewritten test of this work (seeder, repository incl. concurrency, policy, reserved paths, download rules, workspace service, preparation step, builders, migration).

## Drift signal

no — the spec and plan were updated during the run (skill-tool rework in phase 1, `RUNTIME_SDK_FILE_NAME` derived from the SDK module's file); the implementation matches the approved spec.

## Addendum 2026-10-13: review round 4

| Gate | Status | Command | Notes |
|------|--------|---------|-------|
| lint | PASS | `make ruff` | no tracked files changed |
| build | PASS | `make build` | wheel built |
| license | PASS | `make license-check` | 2460 files, 0 missing headers |
| gitleaks | PASS | `make gitleaks` | no leaks found |
| tests | FAIL (pre-existing) | `make test` | after section A: 95 failed, 19,327 passed, 198 skipped, 6 errors; after section B: 95 failed, 19,342 passed, 198 skipped, 6 errors |

The failing and erroring test ids are identical before and after both sections. The 27 failing files were also run with
`src` and `tests` at `5a53028e5` (the commit before round 4) with the same result: 95 failed, 534 passed, 5 errors.
None are in files round 4 touches. Accepted by the user as pre-existing at both QA gates.
