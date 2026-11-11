# QA gates — EPMCDME-14217 part 2 (codemie)

Branch `EPMCDME-14217_mcp-server-instructions`, base `main` (merge-base `bb8a230a7`).

| Gate | Command | Result |
|---|---|---|
| Lint | `poetry run ruff check src tests` | PASS — all checks passed |
| Format | `poetry run ruff format` | PASS — enforced by the repo pre-commit hook on every commit |
| Secret scan | gitleaks pre-commit hook | PASS on every commit |
| MCP + assistant suites | `poetry run pytest tests/codemie/service/mcp tests/codemie/service/test_assistant_service_mcp_instructions.py` | 506 passed, 2 failed (pre-existing) |
| Service + agents | `poetry run pytest tests/codemie/service tests/codemie/agents` | 6073 passed, 6 failed (pre-existing), 129 skipped |
| Test-harness | `make test-harness` | PASS — 193 passed, 4 skipped, exit 0 |

## The pre-existing failures

Six tests fail on this branch. All six fail identically on the merge-base with
the branch stashed, and all six come from optional packages missing in the local
environment, not from this change:

- `tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py` (2) — `ModuleNotFoundError: No module named 'codemie_enterprise'`
- `tests/codemie/service/oauth/adapters/test_provider_adapters.py` (2) — same missing module
- `tests/codemie/service/test_assistant_service_methods.py::TestInteractivePromptGating` (2) — `ModuleNotFoundError: No module named 'a2ui'`

Three further files cannot be collected at all locally for the same `a2ui`
reason and were excluded from the full run: `test_a2ui_intake.py`,
`test_interactive_turn_end.py`, `test_request_user_input.py`.

## Test-harness

Run against a stack rebuilt from this branch (`docker compose up -d
--force-recreate --renew-anon-volumes`), both before and after the code-review
fixes. Both runs: 193 passed, 4 skipped, exit 0.

The harness does not exercise this feature: `MCP_CONNECT_URL` is not wired into
the local compose stack, so it is a no-regression signal rather than proof. End
to end, the chain needs the paired MCP-Connect MR deployed alongside this one.

## Test-first evidence

Every task was driven RED before GREEN. Three mutation checks are worth naming
because they prove the assertions have teeth:

- Disabling the prompt injection fails 5 of 6 acceptance tests.
- Hoisting `_prepare_system_prompt` above `ToolkitService.get_tools` inside
  `build_agent` fails the ordering test — the invariant the whole side-channel
  design rests on.
- Reverting the record-after-filter change fails the test that a server whose
  tools were all filtered out contributes nothing to the prompt.

## Not covered

- No end-to-end run against a live MCP server declaring instructions; that needs
  MCP-Connect deployed and is the first thing to check on a shared environment.
- Workflow-hosted assistants deliberately do not inject instructions. They load
  MCP tools, so they record — and the workflow prompt builder drains the channel
  explicitly so nothing leaks. Injecting there is a follow-up ticket.
