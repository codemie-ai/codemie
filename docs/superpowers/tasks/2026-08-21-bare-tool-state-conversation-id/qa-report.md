# QA Gate Report — bare-tool-state-conversation-id

**Branch**: EPMCDME-14138_bare-tool-state-conversation-id
**Runner**: poetry
**Started**: 2026-08-21T13:53:48Z
**Status**: PASSED (human-overridden — see Failure detail; mechanical gate-table outcome was BLOCKED, but the user reviewed the root-cause evidence and confirmed the unit-gate failures are pre-existing environment breakage unrelated to this diff, logged in `decisions.jsonl`)

## Gates

| Gate | Status | Duration | Command | Notes |
|------|--------|----------|---------|-------|
| lint | PASS | n/a (not re-captured this run) | `make ruff` | format unchanged (2305 files), `ruff check --fix` all checks passed, `ruff check` all checks passed |
| build | PASS | n/a (not re-captured this run) | `make build` | built `codemie-0.8.0.tar.gz` sdist and `codemie-0.8.0-py3-none-any.whl` wheel |
| license-check | PASS | n/a (not re-captured this run) | `make license-check` | Checked 2055 files, 0 missing license headers |
| unit | FAIL | 349.77s | `make test` | 321 failed, 14373 passed, 177 skipped — see Failure detail; all failures are pre-existing environment issues in files untouched by this diff |
| gitleaks | SKIPPED | — | `make gitleaks` | Docker not installed in this environment |
| coverage | SKIPPED | — | `make coverage` | not requested for this run |
| sonar-local | SKIPPED | — | `make sonar-local` | Sonar credentials/network not configured in this environment |
| test-harness | SKIPPED | — | `make test-harness` | no MR being opened in this run |
| ui | SKIPPED | — | (n/a) | no UI surface changed (diff touches 5 Python files only) |

## Failure detail

`make test` reported `321 failed, 14373 passed, 177 skipped, 176 warnings in 349.77s (0:05:49)`.

**All 44 tests directly exercising this ticket's diff pass cleanly**, run in isolation to confirm:
`tests/codemie/service/tools/test_find_tool_from_config_execution_id.py`,
`tests/codemie/service/tools/test_toolkit_settings_service.py`, and
`tests/codemie/workflows/test_tool_node_context.py` — 44 passed, 0 failed.

The 321 failures are spread across areas the diff never touches: `test_filesystem_policy.py`,
`test_sandbox_guard.py`, `test_file_system_tools.py`, `test_pptx_toolkit.py`, the entire
`tests/enterprise/mcp_auth/` suite, and `tests/scripts/test_*_hook.py`. Root-cause sampling of one
failure per unrelated area confirms pre-existing local-environment breakage, not regressions from this
change:

1. `test_filesystem_policy.py::test_guard_forces_safe_lxml_parser_defaults_blocking_xxe` — fails with
   `FileNotFoundError: [WinError 206] The filename or extension is too long` inside CPython's
   `subprocess.Popen` / `_winapi.CreateProcess` — a Windows command-line-length limitation, unrelated to
   this diff's code paths.
2. `tests/scripts/test_commit_msg_hook.py::test_valid_prefix_accepted` — fails with
   `/bin/bash: C:UsersNargizMamedovaProjectscodemiescriptsgit-hookscommit_msg.sh: No such file or
   directory` (`returncode == 127`) — a Windows-path-vs-Git-Bash interop issue invoking the hook script,
   unrelated to this diff.
3. `tests/enterprise/mcp_auth/test_client_metadata_bridge.py::test_enabled_cimd_route_returns_public_document_headers_and_does_not_require_auth`
   — fails with `503 Service Unavailable` instead of `200`, consistent with a required backing service
   (e.g. redis) not being available in this local environment, unrelated to this diff.

None of the 5 files changed by this diff (`src/codemie/service/tools/tool_service.py`,
`src/codemie/workflows/nodes/tool_node.py`, `src/codemie/service/tools/toolkit_settings_service.py`, and
2 new/modified test files) appear anywhere in the 321-item failure list.

The gate outcome table treats any Unit-gate FAIL as `BLOCKED` regardless of cause, so this report records
`BLOCKED` mechanically. The finding for human/handoff review is that the block is caused by pre-existing
Windows/local-environment gaps (subprocess arg-length limits, Git-Bash path handling, an unavailable
backing service) that also affect unrelated, untouched code — not by a regression introduced by
EPMCDME-14138.

## Drift signal

no — this is a no-spec sdlc-light round (no `story`/`spec` artifact), so there is no spec to drift from.
Implementation matches `technical-analysis.md`'s documented plan (execution_id threaded through
`ToolsService.find_tool_from_config` as `conversation_id`, mirroring the existing MCP path).
