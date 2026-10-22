# EPMCDME-15350: A2A integration docs, wrapper guide and live verification (codemie backend slice)

## Context

CodeMie's A2A v1.0 server and client are already implemented and were verified in EPMCDME-15327
(21/21 checks at `c52a66523`, see `docs/superpowers/tasks/2026-09-28-epmcdme-15327/a2a-wire-conformance/verification-report.md`).
No user-facing A2A documentation exists in this repo; the only material is the engineering guide
`.ai-run/guides/integration/a2a-integration.md` and the untracked `DEMO-GUIDE.md` runbook.
The two demo agents (`text-insights-agent` non-streaming, `howto-guide-agent` streaming) live in the sibling repo
`ai-run-demo-agents` under EPMCDME-15348 (In Progress).

## Scope of this repo's slice (documentation plus verification only)

1. New user-facing doc `docs/a2a-integration.md`, alongside `webhook-configuration.md`. It is built by promoting the
   content of `DEMO-GUIDE.md` and covers:
   - Setup: `remoteAssistant` customer-config flag, `A2A_*` env vars, auth (CodeMie JWT bearer, `user-id` header when `IDP_PROVIDER=local`, card auth unless the assistant is `is_global`, stored outbound credentials).
   - Connection steps in both directions: CodeMie calling a remote agent, and an external A2A client calling a CodeMie assistant (card, `SendMessage`, `SendStreamingMessage`, `GetTask`).
   - Supported operations: `SendMessage`, `SendStreamingMessage`, `GetTask`, `CancelTask`, `GetExtendedAgentCard`.
   - Known limitations: v1.0 only (v0.3 agents such as `currency_converter`, `research-agent`, Bedrock AgentCore fail fast); text parts only; no `returnImmediately`; `ListTasks`, `SubscribeToTask` and push notifications return `-32601`; JSON-RPC errors come back as HTTP 200.
   - Failure behavior observed in the live runs (see 3).
   - Links to the agents repo for the agents' own A2A interface; no agent code is duplicated here.
2. Wrapper guide (AC 5), as a section of `docs/a2a-integration.md` or a sibling `docs/a2a-wrapper-guide.md`. It explains how to map an A2A request to existing agent logic and map the result back to an A2A response (Message/Task, artifacts, streaming events). The worked example is based on the two demo agents and links to their source in `ai-run-demo-agents`. Code appears only where it pins the request-to-response mapping.
3. Live failure runs (AC 4), recorded in `<task_dir>/verification-report.md` with real requests and responses:
   - Agent stopped (connection refused or timeout).
   - Malformed Agent Card (invalid JSON).
   - Agent Card with no supported interface.
   - Auth: missing or invalid bearer token on the card and on JSON-RPC.
   - Re-run of the happy path for both agents (non-streaming returns a response; streaming runs to completion) and for an external client against a CodeMie assistant. This rehearses the unrehearsed Docker network layout and UI click path from `DEMO-GUIDE.md`.
   The observed behavior is then written into the doc as known behavior.

## Acceptance criteria

1. AC1 and AC2: the report shows both agents' cards fetched and both called by CodeMie over protocol 1.0, one returning a normal response and one streaming to completion.
2. AC3: the report shows an external A2A client discovering and calling a CodeMie assistant.
3. AC4: the report records the auth failure cases plus the three failure runs above, each with the actual error surfaced.
4. AC5: the wrapper guide exists with the agent-based example and covers both directions of mapping.
5. AC6: `docs/a2a-integration.md` covers setup, both connection directions, supported operations and known limitations, and every command in it was executed during verification.
6. AC7: not produced here. The doc and report give the video its script and verified steps.
7. The diff contains only new files under `docs/` and `docs/superpowers/tasks/2026-10-01-epmcdme-15350/`.

## Non-goals

- No changes to `src/` or `tests/`, and no new unit tests. A defect found during verification is recorded in the report as an open risk and escalated, not fixed here.
- No agent-side A2A implementation in `ai-run-demo-agents` (owned by EPMCDME-15348).
- No video recording or editing.
- No edits to the `.ai-run/guides/` engineering guide, to the existing task folders (including the stale `agent.json` references in `2026-09-14-a2a-agent-card-auth`), or to the already-modified files (`customer-config.yaml`, `customer_config.py`, `doctor.json`, `codemie-cli.config.json`).
- No `a2a-sdk` migration, no v0.3 compatibility, and no new A2A server methods.
- No README or CHANGELOG edits.

## Risks

- EPMCDME-15348 is unfinished, so agent names, ports and commands may change. The doc must reference the agents repo rather than copy its details.
- If live runs expose CodeMie defects, the documented behavior may need a follow-up ticket.
