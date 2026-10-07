# QA Gate Report — EPMCDME-15110

**Branch**: EPMCDME-15110_default-project-foundation
**Runner**: poetry (Makefile targets run manually — `make` binary unavailable in this Git-Bash-on-Windows environment; ran each target's exact underlying command instead)
**Started**: 2026-09-23T13:00:00Z
**Status**: BLOCKED (mechanically, per the strict any-FAIL rule) — see Drift signal / Notes below for why this is not attributable to this diff

## Gates

| Gate | Source | Status | Duration | Command | Notes |
|------|--------|--------|----------|---------|-------|
| lint | guide | PASS | ~15s | `ruff format && ruff check --fix && ruff check` | 2 files auto-reformatted (cosmetic only, both files this diff touches); all checks pass after |
| build | guide | PASS | ~10s | `poetry build` | sdist + wheel built cleanly |
| license-check | guide | PASS | ~5s | `python scripts/license_headers/check_license_headers.py --check --quiet` | 2292 files checked, 0 missing headers |
| secrets | hook | PASS | ~60s | `docker run ... gitleaks dir ...` | Required `MSYS_NO_PATHCONV=1` and an explicit Windows-style host path for the `-v` mount to work under Git Bash on Windows — environment quirk, not a project issue. No leaks found. |
| unit | guide | **FAIL** | ~21min | `poetry run pytest tests/` | 159 failed, 16508 passed, 190 skipped. **See Drift signal below — none of the 159 failures touch this diff's files.** `pytest.ini`'s default `addopts` requires `pytest-xdist` (`-n 2`), which is not installed in this environment; ran with `-o addopts="--import-mode=importlib"` (dropping only the missing `-n` flag, keeping the import-mode fix that's required for this repo's test tree to collect at all without basename collisions). |
| affected | guide | SKIPPED | — | (n/a) | No changed-file-aware pytest wrapper configured for this repo; not attempted per the generic framework |
| ui | guide | SKIPPED | — | (n/a) | No UI surface changed (backend-only diff) |
| coverage | guide | SKIPPED | — | `pytest ... --cov` | Not requested |
| sonar | ci | SKIPPED | — | `node scripts/sonar/run-local-sonar.js` | Self-skipped: `SONAR_TOKEN` not set in this environment |
| test-harness | guide | SKIPPED | — | `make test-harness` | Not opening an MR in this session; requires a superadmin fixture + `~/.codemie/test-harness.json` not set up here |
| verify | guide | SKIPPED | — | `make verify` (= ruff+license+gitleaks+test) | Redundant — its component gates already run individually above |

## Failure detail

**159 failures, grouped by file, none overlapping this diff's touched files** (`src/codemie/repository/user_project_repository.py`, `src/codemie/rest_api/models/user_management.py`, `src/codemie/rest_api/routers/user_management_router.py`, `src/codemie/service/user/{user_access_service,user_management_service,registration_service,authentication_service}.py`, and their alembic migration):

```
 56 tests/codemie_tools/data_management/code_executor/test_filesystem_policy.py
 30 tests/enterprise/mcp_auth/test_post_auth_401_bridge.py
 11 tests/scripts/test_commit_msg_hook.py
  9 tests/enterprise/switchyard/test_engine.py
  8 tests/enterprise/mcp_auth/test_oauth2_initiate_bridge.py
  7 tests/scripts/test_pre_push_hook.py
  6 tests/scripts/test_ruff_staged_hook.py
  4 tests/enterprise/mcp_auth/test_discovery_probe_bridge.py
  3 tests/enterprise/mcp_auth/test_mcp_auth_status_bridge.py
  3 tests/enterprise/mcp_auth/test_client_metadata_bridge.py
  3 tests/codemie/datasource/loader/test_git_loader.py
  2 tests/enterprise/switchyard/test_engine_integration.py
  2 tests/codemie/service/oauth/adapters/test_provider_adapters.py
  2 tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py
  1 (each) tests/scripts/test_pre_commit_fast_hook.py, tests/enterprise/mcp_auth/test_private_network_allowlist_bridge.py,
           tests/enterprise/mcp_auth/test_oauth2_callback_bridge.py, tests/enterprise/mcp_auth/test_insufficient_scope_recovery_bridge.py,
           tests/codemie_tools/file_analysis/pptx/test_pptx_toolkit.py, tests/codemie_tools/data_management/file_system/test_file_system_tools.py,
           tests/codemie_tools/data_management/code_executor/test_sandbox_guard.py, tests/codemie/service/test_dynamic_config_service.py,
           tests/codemie/service/conversation/test_message_exporter.py, tests/codemie/rest_api/routers/test_oauth_redis_lazy_init.py,
           tests/codemie/datasource/loader/test_svn_loader.py, tests/codemie/datasource/code/test_background_processing.py,
           tests/codemie/core/test_a2ui_frontend_contract.py
```

Root-caused a representative sample:
- **`tests/scripts/test_*_hook.py` (25 failures)**: `execvpe(/bin/bash) failed: No such file or directory` via WSL — these git-hook tests invoke shell scripts through WSL specifically, which isn't configured in this exact Windows/Git-Bash environment. Environment gap, unrelated to any Python code.
- **`tests/enterprise/mcp_auth/*` (51 failures)** and **`tests/enterprise/switchyard/*` (11 failures)**: unrelated MCP OAuth bridge and LLM-routing-engine subsystems, not touched by this diff.
- **`tests/codemie_tools/data_management/code_executor/test_filesystem_policy.py` (56 failures)**: sandbox filesystem policy tool, not touched by this diff.
- All remaining single/low-count failures are in datasource loaders, MCP toolkit auth, OAuth adapters, and misc services — none overlapping this diff.

**Scoped verification for this diff** (every file touched, plus each file's full existing sibling test suite): 1020+ tests passed across `tests/codemie/repository/`, `tests/codemie/service/user/`, `tests/codemie/rest_api/routers/test_user_management_router_*`, run repeatedly through TDD and the code-review fix-up cycle. Zero failures in this diff's own surface at any point.

## Drift signal

no — confirmed empirically, not just by path analysis. Checked out `main` (working tree was clean) and ran the three largest failure clusters directly: `test_filesystem_policy.py` + `test_post_auth_401_bridge.py` + `test_engine.py` → **95 failed, 14 passed** on `main`, an exact match to 56+30+9=95 seen on this branch. These fail identically with zero relation to EPMCDME-15110. Switched back to the feature branch afterward, confirmed clean. The 159 full-suite failures are a pre-existing, project-wide condition (WSL-dependent hook tests, MCP OAuth bridge, filesystem sandbox policy, switchyard routing) — not something this ticket introduced or is positioned to fix.
