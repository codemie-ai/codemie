# MR notes — EPMCDME-13260

For `mr-creator` to fold into the MR description.

## Deploy sequencing — BLOCKING

**Do not merge this MR until MCP-Connect EPMCDME-14847 is merged, deployed, and fully rolled out
across every bridge bucket.** MCP-Connect runs as a StatefulSet and the backend routes requests
across buckets (`src/codemie/service/mcp/client.py:85-98`), so a partial rollout still leaves some
buckets on the old `extra="forbid"` `/bridge` that rejects `user_context`.

This change removes `user_context` from the `to_request_fields()` exclude set. Un-excluding the
field before EPMCDME-14847 is everywhere recreates the platform-wide EPMCDME-13546 regression that
reopened this ticket (every MCP `tools/call` fails against a bucket that still forbids the field).

There is no feature flag guarding this — sequencing is the only control.

### Two-release plan

1. **Release 1 (MCP-Connect, EPMCDME-14847):** bridge accepts and forwards `user_context`. Deploy
   and confirm rollout is complete on all buckets.
2. **Release 2 (this MR):** backend starts sending `user_context` on the MCP wire. Merge only after
   step 1's rollout is verified complete.

## Reviewer action

This change touches security-relevant code (PII on a new wire path, diagnostic logging of user
context). **Please post `/sanity`** on this MR — the repo has no pipeline config, so nothing
re-checks the MR automatically.

## Local QA evidence

- `make ruff` / `make build` / `make license-check`: **pass**.
- `make test` (full local suite): blocked only by pre-existing host-environment failures unrelated
  to this change (`codemie_enterprise` not installed in the OSS venv, POSIX-only sandbox tests and
  bash git-hook tests on Windows); the 5 branch-changed test files pass 238/238 in isolation.
- `codemie-test-harness --sanity-api` against a freshly-restarted local backend on this branch:
  **193 passed, 4 skipped, 0 failed** (5 reruns, 192s). Covers assistants, conversations, marketplace,
  skills, search, MCP config persistence, file-datasource indexing, and workflow nodes/context
  management end to end against the running API.
- Re-run of `codemie-test-harness run sanity-api` (`--reruns 5`) after merging latest `main` into this
  branch (picking up EPMCDME-13260 + EPMCDME-14984 together, plus the alembic multi-head fix that
  merge required): **193 passed, 4 skipped, 0 failed** (3 individual test reruns auto-recovered, 266s).
  Confirms the combined branch is still green after the main sync.
- Manual live verification of the MCP wire path against a locally-run `codemie-mcp-connect-service`
  (branch `EPMCDME-14847_accept-user-context-on-bridge`, `poetry run uvicorn` on port 3000) and a
  throwaway streamable-http MCP tool server: registered an MCP config with `allow_user_context: true`,
  created an assistant using it, and drove a real chat turn that invoked the tool. mcp-connect logged
  `Forwarding user_context to downstream tools/call as MCP _meta` and returned `200 OK` on `/bridge`
  (no rejection); the tool executed and its result round-tripped back through the assistant's reply
  correctly. This exercises the exact `tools/call` gating this MR and EPMCDME-14984 add.
