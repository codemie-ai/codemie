# QA Gate Report — EPMCDME-15111

**Branch**: EPMCDME-15110_default-project-foundation
**Runner**: poetry (Makefile targets run manually — `make` binary unavailable in this Git-Bash-on-Windows environment; ran each target's exact underlying command instead)
**Started**: 2026-09-23T20:00:00Z
**Status**: BLOCKED (mechanically, per the strict any-FAIL rule) — see Drift signal / Notes below for why this is not attributable to this diff

## Gates

| Gate | Source | Status | Duration | Command | Notes |
|------|--------|--------|----------|---------|-------|
| lint | guide | PASS | ~15s | `ruff format && ruff check --fix && ruff check` | 2 files auto-reformatted (cosmetic only, this diff's own files); all checks pass after |
| build | guide | PASS | ~10s | `poetry build` | sdist + wheel built cleanly |
| license-check | guide | PASS | ~5s | `python scripts/license_headers/check_license_headers.py --check --quiet` | 2292 files checked, 0 missing headers. New migration file under `src/external/alembic/versions/` correctly exempt (generated/external path per `.ai-run/guides/standards/code-quality.md`) |
| secrets | hook | PASS | ~79s | `docker run ... gitleaks dir ...` | Required `MSYS_NO_PATHCONV=1` for the `-v` mount under Git Bash on Windows — environment quirk, not a project issue. No leaks found. |
| unit | guide | **FAIL** | ~16min | `poetry run pytest tests/` | **159 failed, 16521 passed, 190 skipped.** Exact same 159 failures, same files/categories, as EPMCDME-15110's QA run (already empirically confirmed pre-existing and unrelated via `git stash`/`main` comparison in that run). 16521 passed = 16508 (EPMCDME-15110's passing count) + 13 (new tests added across this ticket's 6 plan tasks + 2 code-review fix-ups) — exact arithmetic match confirms zero new failures introduced. `pytest.ini`'s default `addopts` requires `pytest-xdist` (`-n 2`), not installed; ran with `-o addopts="--import-mode=importlib"` (same override as EPMCDME-15110, dropping only the missing `-n` flag). |
| affected | guide | SKIPPED | — | (n/a) | No changed-file-aware pytest wrapper configured for this repo |
| ui | guide | SKIPPED | — | (n/a) | No UI surface changed (backend-only diff) |
| coverage | guide | SKIPPED | — | `pytest ... --cov` | Not requested |
| sonar | ci | SKIPPED | — | `node scripts/sonar/run-local-sonar.js` | Self-skipped: `SONAR_TOKEN` not set in this environment |
| test-harness | guide | SKIPPED | — | `make test-harness` | Not opening a new MR in this session (landing on EPMCDME-15110's already-open MR !4315); superadmin fixture + `~/.codemie/test-harness.json` not set up here |
| verify | guide | SKIPPED | — | `make verify` (= ruff+license+gitleaks+test) | Redundant — its component gates already run individually above |

## Failure detail

**159 failures — identical set to EPMCDME-15110's own QA run**, none overlapping this ticket's touched files (`src/codemie/rest_api/security/user.py`, `src/codemie/rest_api/security/idp/local.py`, `src/codemie/enterprise/idp/dependencies.py`, `src/codemie/service/llm_service/utils.py`, `src/codemie/enterprise/litellm/proxy_router.py`, `src/codemie/service/user/authentication_service.py`, and the new backfill migration):

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
  1 (each) remaining single-count files, same list as EPMCDME-15110's qa-report.md
```

Root cause (already established in EPMCDME-15110's QA run, re-confirmed here by identical failure identity):
- **`tests/scripts/test_*_hook.py` (25 failures)**: `execvpe(/bin/bash) failed` via WSL — git-hook tests need WSL, unavailable in this Windows/Git-Bash environment. Environment gap.
- **`tests/enterprise/mcp_auth/*` (51 failures)** and **`tests/enterprise/switchyard/*` (11 failures)**: unrelated MCP OAuth bridge and LLM-routing-engine subsystems.
- **`tests/codemie_tools/data_management/code_executor/test_filesystem_policy.py` (56 failures)**: sandbox filesystem policy tool, unrelated.
- Remaining single/low-count failures: datasource loaders, MCP toolkit auth, OAuth adapters — none overlapping this diff.

**Scoped verification for this diff**: every touched file's full test suite run individually through TDD (Tasks 1-6) and both code-review fix-ups (CR-001, CR-002) — 231 tests across `tests/codemie/rest_api/security/test_user.py`, `tests/codemie/service/llm_service/test_utils_litellm_context.py`, `tests/enterprise/litellm/test_proxy_router.py`, `tests/codemie/service/user/test_authentication_service.py`, `tests/codemie/rest_api/security/idp/test_local_idp.py`, `tests/enterprise/idp/test_enterprise_idp_wrapper.py` — with exactly 2 known, individually-confirmed-pre-existing-and-environment-only failures (`test_load_user_for_auth_found`, `test_authenticate_db_success_with_user_management_enabled`, both caused by `config.ENV=="local"` forcing `is_admin=True`/short-circuiting to a dev-stub user in this specific dev machine's environment — confirmed via `git stash` showing identical failures with this ticket's changes fully reverted). Zero failures in this diff's own logic at any point.

## Drift signal

no — implementation matches the (twice-corrected, transparently-documented) plan and spec exactly. Two corrections were made and documented inline: (1) plan.md's "Correction versus the approved spec" — no new async repository method needed, `default_project` derived in-place from already-fetched rows; (2) plan.md's "Correction versus the approved plan (post-review, CR-002 fix-up)" — a new backfill migration was added after code review found the sorted() fallback would silently reassign billing for pre-existing users at deploy, which the original plan's "no new DB migration" constraint didn't anticipate. Both corrections are recorded, not silent.

## Migration verification note

`d2c276d4390e_backfill_default_project_for_existing_users.py` (data-only, idempotent) was verified against the live dev Postgres (`projects-postgres-1`) with 3 seeded users covering all 3 cases (multi-project/no-default → backfilled to alphabetically-first; multi-project/has-default → untouched; single-project → untouched) plus a second run confirming idempotency (0 rows changed) — all within a rolled-back transaction. Additionally, the real `alembic upgrade head` run during this QA pass applied it for real against the shared dev DB, confirmed via `alembic current` showing head `d2c276d4390e` with no errors.
