# QA Gate Report — epmcdme-14866-project-deletion-integrations

**Branch**: EPMCDME-14866_fix-project-deletion-integrations
**Runner**: poetry (`make` binary unavailable in this shell; ran the exact underlying commands from each Makefile target verbatim)
**Started**: 2026-09-14T18:57:00Z
**Status**: BLOCKED on literal gate criteria; user-accepted as pre-existing/environment after traceback-level verification (see Resolution)

## Resolution

User reviewed full tracebacks for representative failures and confirmed 3 distinct environment root causes, none touching this diff's files:
- `tests/scripts/*`: `execvpe(/bin/bash) failed: No such file or directory` (WSL bash unreachable on this machine)
- `tests/enterprise/mcp_auth/*`: `ModuleNotFoundError: No module named 'codemie_enterprise'` (enterprise extra not actually importable despite poetry listing it with a `(!)` version-mismatch marker)
- sandbox test: `FileNotFoundError: [WinError 206] The filename or extension is too long` (Windows path-length limit)
- gitleaks: 927/927 findings under `.venv/` (gitignored third-party deps), zero in tracked source

Decision: accept as pre-existing, proceed to handoff. `.gitleaks.toml` `.venv` allowlist gap left untouched (out of scope for this ticket, tracked separately).

## Gates

| Gate | Source | Status | Duration | Command | Notes |
|---|---|---|---|---|---|
| lint | guide | PASS | ~15s | `poetry run ruff format && poetry run ruff check --fix && poetry run ruff check` | "All checks passed!" |
| build | guide | PASS | ~5s | `poetry build` | sdist + wheel built (codemie 0.8.0) |
| license-check | guide | PASS | ~10s | `poetry run python scripts/license_headers/check_license_headers.py --check --quiet` | 2267 files checked, 0 missing headers |
| gitleaks | guide | FAIL* | ~17m | `docker run --rm -v "<winpath>:/workspace" ghcr.io/gitleaks/gitleaks:v8.30.1 dir --no-banner --verbose --config=/workspace/.gitleaks.toml /workspace` | 927 findings, **100% inside `.venv`** (gitignored third-party deps, e.g. `youtube_transcript_api` test fixtures). `.gitleaks.toml` allowlist excludes `__pycache__`, `.pytest_cache`, `.idea` but not `.venv` — pre-existing config gap, not introduced by this diff. Zero findings in tracked source. |
| unit tests | guide | FAIL* | ~21m | `poetry run pytest tests/` | 16287 passed, 148 failed, 189 skipped. **All 148 failures are in `tests/scripts/*` (git-hook scripts), `tests/enterprise/mcp_auth/*` (enterprise extra confirmed broken in this environment — `codemie_enterprise` module not importable despite poetry listing it with a `(!)` warning), and unrelated tool tests (sandbox/file-system/pptx).** Zero failures touch `project_service.py`, `application_repository.py`, `settings.py`, or any file this diff changed. The 144 tests across the 3 files this diff touches (`test_project_service_delete_update.py`, `test_application_repository_entity_counts.py`, `test_projects_router.py`) all pass — verified separately in this task's implementation stage. |
| coverage | guide | SKIPPED | — | (n/a) | not requested |
| sonar-local | guide | SKIPPED | — | `node scripts/sonar/run-local-sonar.js` | self-skipped: "Skipping Sonar scan because SONAR_TOKEN is not set." |
| verify (composite) | guide | N/A | — | `poetry run ruff ... && license ... && gitleaks && test` | redundant with the 4 gates above, run individually instead |
| test-harness | guide | N/A | — | (n/a) | not opening an MR in this stage; per memory, this harness is commonly blocked in this environment (Docker/SDK/AWS token) — deferred to MR-creation time |

\* Both FAIL gates are literal per the guide's pass/fail wording (any finding / any failure = fail), but both are corroborated pre-existing environment issues unrelated to this change — see Failure detail.

## Failure detail

**gitleaks** — every one of the 927 findings resolves to a `File:` path under `/workspace/.venv/...`. Sample:
```
File:        /workspace/.venv/Lib/site-packages/youtube_transcript_api/test/assets/youtube.html.static
```
`.venv` is gitignored and not part of the change. The allowlist already excludes other gitignored noise sources (`__pycache__`, `.pytest_cache`, `.idea`) but never had a `.venv` entry — a pre-existing gap in `.gitleaks.toml`, out of scope for this ticket.

**unit tests** — failing files, none overlapping this diff's blast radius:
```
tests/codemie_tools/data_management/code_executor/test_sandbox_guard.py
tests/codemie_tools/data_management/file_system/test_file_system_tools.py
tests/codemie_tools/file_analysis/pptx/test_pptx_toolkit.py
tests/enterprise/mcp_auth/test_client_metadata_bridge.py
tests/enterprise/mcp_auth/test_discovery_probe_bridge.py
tests/enterprise/mcp_auth/test_insufficient_scope_recovery_bridge.py
tests/enterprise/mcp_auth/test_mcp_auth_status_bridge.py
tests/enterprise/mcp_auth/test_oauth2_callback_bridge.py
tests/enterprise/mcp_auth/test_oauth2_initiate_bridge.py
tests/enterprise/mcp_auth/test_post_auth_401_bridge.py
tests/enterprise/mcp_auth/test_private_network_allowlist_bridge.py
tests/scripts/test_commit_msg_hook.py
tests/scripts/test_pre_commit_fast_hook.py
tests/scripts/test_pre_push_hook.py
tests/scripts/test_ruff_staged_hook.py
```
Confirmed `codemie_enterprise` is not importable in this environment (`ModuleNotFoundError: No module named 'codemie_enterprise'`) despite `poetry show` listing `codemie-enterprise 2.3.42 (!)` — explains the `tests/enterprise/*` failures as an environment/extras gap. `tests/scripts/*` are git-hook integration tests presumably dependent on hook installation state (`make install-hooks`) not run in this session.

## Drift signal

no — implementation matches spec/plan exactly (confirmed by the acceptance lens in code review: 14/14 criteria pass).
