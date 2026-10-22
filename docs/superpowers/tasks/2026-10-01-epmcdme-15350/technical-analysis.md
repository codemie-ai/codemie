# Technical Research

**Task**: a2a agent-card protocol docs (EPMCDME-15350)
**Generated**: 2026-10-01
**Research path**: filesystem

---

## 1. Original Context

Jira EPMCDME-15350 (Story): "Add A2A support, verify CodeMie integration, and update documentation"
Description: Add an A2A interface to the test agents. Verify that CodeMie can call them and that an external A2A client can call a CodeMie assistant. Update the existing documentation with integration instructions and a guide for adding an A2A wrapper to an existing application. Record a new factory-based demonstration video.
Acceptance criteria:
1. Both agents expose valid Agent Cards and accept A2A requests using the protocol version supported by CodeMie.
2. CodeMie discovers and calls both agents. One returns a normal response; the other streams events to completion.
3. An external A2A client discovers and calls a CodeMie assistant.
4. Authentication and representative failure cases are verified, including an unavailable agent and an invalid Agent Card.
5. The wrapper guide explains how to map an A2A request to existing agent logic and its result back to an A2A response, with an example based on these agents.
6. The existing documentation is updated with setup, connection steps in both directions, supported operations, and known limitations.
7. A new factory-based video demonstrates the verified integration.
Linked: depends on EPMCDME-15348 (Revive the demo agents repository and create test agents), In Progress. No parent.
Notes: this repo (codemie backend) is on branch EPMCDME-15327_upgrade-a2a-protocol, which already did A2A wire-conformance work (see .ai-run/guides/integration/a2a-integration.md and docs/superpowers/tasks/2026-09-28-epmcdme-15327/). The "test agents" likely live in a separate demo-agents repo; determine what in THIS repo is relevant (A2A client/server code, existing docs, any demo agents or docs dir) and flag clearly what is out of repo scope.

---

## 2. Codebase Findings

### Existing Implementations
Python 3.12+/Poetry/FastAPI backend. A2A is an in-house implementation (not `a2a-sdk`), target spec v1.0/v1.0.1.
- `src/codemie/rest_api/a2a/version.py` - `A2A_PROTOCOL_VERSION = "1.0"`.
- `src/codemie/rest_api/a2a/types.py` - wire types (Task, Message, Part, AgentCard, events) with legacy-normalising before-validators.
- `src/codemie/rest_api/a2a/utils.py` - `assistant_to_agent_card`.
- `src/codemie/rest_api/a2a/server/` - `task_manager.py`, `codemie_task_manager.py`, `utils.py` (server side, DB-backed tasks).
- `src/codemie/rest_api/a2a/client/` - `card_resolver.py` (`A2ACardResolver`, fetches `/.well-known/agent-card.json`, sends `A2A-Version`, optional per-project credentials via `SettingsService.get_a2a_creds`), `client.py`, `remote_agent_connection.py`.
- `src/codemie/rest_api/routers/a2a.py` - prefix `/v1/a2a`: card GET `/assistants/{id}/.well-known/agent-card.json`, `POST /assistants/{id}` (JSON-RPC), `/assistants/fetch` (UI card fetch for remote assistants).
- `src/codemie/rest_api/handlers/assistant_handlers.py` - `A2AAssistantHandler` (CodeMie calling remote agents in chat; maps `AgentCardInterfaceError` / `A2AProtocolVersionError` to 400).
- `src/codemie/agents/assistant_agent.py`, `langgraph_agent.py`, `rest_api/models/assistant.py` - A2A assistant integration; Alembic `074efb996aac`, `8f68ee6d23b6` (a2a_tasks), `c9d8e7f6a5b4` (owner/context).

### Architecture and Layers Affected
For this ticket the backend is essentially already built; affected layers are documentation, verification/demo runs, and (only if defects surface) the router/client layers above. No new backend feature is implied by the ticket text.

### Integration Points
- Server methods in scope: `SendMessage`, `SendStreamingMessage`, `GetTask`, `CancelTask`, `GetExtendedAgentCard`. `ListTasks`, `SubscribeToTask`, push-notification CRUD answer `-32601`.
- Client always sends `A2A-Version: 1.0`; non-v1.0 remotes (`-32601/-32600/-32009`) raise `A2AProtocolVersionError` (400 in chat). Bedrock AgentCore (v0.3) remotes fail fast.
- Auth: server uses CodeMie JWT bearer (plus `user-id` header when `IDP_PROVIDER=local`); card endpoint requires auth unless assistant `is_global` (task `2026-09-14-a2a-agent-card-auth`, written against the old `agent.json` path; current path is `agent-card.json`). Outbound auth via `get_auth_header` from stored A2A credentials settings.

### Patterns and Conventions
Router -> handler -> a2a client/task manager; all JSON-RPC errors return HTTP 200 (auth errors keep 401/403/404); read-time normalisation of legacy shapes; tests assert spec-derived fixtures.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/integration/a2a-integration.md` - authoritative version decision, verified wire shapes, server/client behaviour, backward compat. This is an engineering guide, NOT user-facing integration documentation.
- `docs/superpowers/tasks/2026-09-28-epmcdme-15327/HANDOVER.md` - slice summary, known accepted gaps, out-of-scope list.
- `docs/superpowers/tasks/2026-09-28-epmcdme-15327/DEMO-GUIDE.md` (untracked, uncommitted) - an existing end-to-end demo runbook: CodeMie -> `text-insights-agent` (:10800, non-streaming) and `howto-guide-agent` (:10900, streaming); legacy agents (`currency_converter`, `research-agent`) for the incompatibility case; curl steps for external client -> CodeMie (card, SendMessage, GetTask, SSE, version gate); sections for known limits and troubleshooting. Its own header states the Docker-network layout and UI click path were not run end to end ("Rehearse first"). Strong base for AC 2, 3, 4, 6, 7.
- `docs/superpowers/tasks/2026-09-28-epmcdme-15327/a2a-wire-conformance/verification-report.md` - captured real requests/responses; 21/21 checks passed at `c52a66523` (SDK client -> CodeMie; CodeMie -> both agents; legacy agents error).

### Architectural Decisions
In-house upgrade, not `a2a-sdk` migration; v1.0 only, no v0.3 server translation, no `agent.json` route; `returnImmediately` rejected; text parts only.

### Derived Conventions
No user-facing (product) docs directory for A2A exists in this repo. `docs/` holds only a few topic docs (`webhook-configuration.md`, `jwks-configuration-reference.md`) plus `docs/superpowers/` working artifacts. README.md and CHANGELOG.md have no A2A mentions. "Existing documentation" for AC 6 is therefore likely NOT in this repo (probably the external CodeMie docs site/ai-run-demo-agents README); it must be confirmed.

---

## 4. Testing Landscape

### Existing Coverage
`tests/codemie/rest_api/a2a/` (client: card_resolver, client, remote_agent_connection; server: codemie_task_manager; test_types, test_utils, test_version, test_wire_contract + fixtures), `tests/codemie/rest_api/routers/test_a2a_router.py`, `tests/codemie/rest_api/handlers/test_a2a_assistant_handler.py`.

### Testing Framework and Patterns
pytest (+asyncio), unittest.mock; spec-derived JSON fixtures in `tests/codemie/rest_api/a2a/fixtures/`. Tests are run via WSL on Windows (per prior handover). Tests only on explicit request per AGENTS.md.

### Coverage Gaps
No live integration/e2e test harness: verification against real agents was manual. "Unavailable agent" (connection refused/timeout) and "invalid Agent Card" (malformed JSON, no interface) handler-level behaviour should be checked against `test_card_resolver.py`/`test_remote_agent_connection.py` before claiming coverage (not exhaustively verified here). The verification report covers legacy-protocol agent failure but I found no recorded run of an unreachable agent or an invalid-card scenario.

---

## 5. Configuration and Environment

### Environment Variables
`src/codemie/configs/config.py:433-436`: `A2A_AGENT_CARD_FETCH_TIMEOUT` (30.0), `A2A_AGENT_REQUEST_TIMEOUT` (30.0), `A2A_PROVIDER_ORGANIZATION`, `A2A_PROVIDER_URL`. Also `IDP_PROVIDER`, `ENABLE_USER_MANAGEMENT`, `HTTPS_VERIFY_SSL` (used by the client), LLM keys.

### Configuration Files
`config/customer/customer-config.yaml` has a `remoteAssistant` feature component; must be `enabled: true` for the UI "Create Remote Assistant" button (file is currently modified in the working tree, unrelated diff not inspected). `docker-compose.yml` has no A2A entries; the demo uses a parent-level `codemie-dev/docker-compose.yaml`.

### Feature Flags and Deployment Concerns
`remoteAssistant` customer-config toggle (60 s cache). Demo agents must join the CodeMie Docker network for container-to-container reach (DEMO-GUIDE section 3.1).

---

## 6. Risk Indicators

- Scope mismatch: the ticket's central deliverables (A2A interface on test agents, wrapper guide example, agents' docs) belong to the sibling repo `C:\Users\Artur_Kuznetsov1\codemie-dev\ai-run-demo-agents` (branch `EPMCDME-15348_Revive-the-demo-agents-repository-and-create-test-agents`; agents `text-insights-agent`, `howto-guide-agent` already on official `a2a-sdk[http-server]>=1.1.5`). Out of THIS repo's scope except for CodeMie-side docs.
- Dependency: EPMCDME-15348 is In Progress; the agents' final shape may change.
- AC 7 (factory-based video) is not a code deliverable and cannot be produced from the repo.
- AC 6 target docs location is ambiguous; no user-facing A2A docs exist in this repo.
- DEMO-GUIDE.md is untracked and flagged unrehearsed; its Docker network layout and UI click path are unverified.
- AC 4 coverage: "unavailable agent" and "invalid Agent Card" not found in the verification report; needs live verification.
- Stale guidance: task `2026-09-14-a2a-agent-card-auth` references `agent.json`; current route is `agent-card.json`.
- AC 1 "protocol version supported by CodeMie" is 1.0 only; v0.3 agents (`currency_converter`, `research-agent`) are intentionally unsupported; server limits (text-only, no `returnImmediately`, no push/ListTasks) must be listed as known limitations.
- Speculative: any defects found during live verification would reopen the A2A code on a branch whose work is unpushed with no MR; mixing doc work there could entangle MR scope.
- Speculative: Windows environment requires WSL for Docker/tests.

---

## 7. Summary for Complexity Assessment

In this repo the change surface is small and documentation-centric: CodeMie's A2A server/client (`src/codemie/rest_api/a2a/`, `routers/a2a.py`, `A2AAssistantHandler`) already implements A2A v1.0 and was verified against the two demo agents and the official SDK client in EPMCDME-15327. No A2A user-facing docs exist in the repo; existing material is the engineering guide `.ai-run/guides/integration/a2a-integration.md` and an uncommitted DEMO-GUIDE.md. Layers touched: documentation only, unless verification exposes defects.

Most of the ticket (A2A interface on agents, wrapper guide with agent-based example, agent docs, video) is out of repo scope: the agents live in the sibling `ai-run-demo-agents` repo and depend on in-progress EPMCDME-15348. Technical novelty is low; the work is mostly verification, failure-case runs (unreachable agent, invalid card are not yet recorded) and writing.

Test posture: unit coverage of A2A is solid (about 13 test files); live e2e is manual only. Key risks are ambiguity of the doc target, the unrehearsed demo runbook, and the cross-repo dependency.

---

## 8. External References

None named by the task. (Related sibling repo discovered: `C:\Users\Artur_Kuznetsov1\codemie-dev\ai-run-demo-agents`, resolved and inspected only at directory level; not a named source.)
