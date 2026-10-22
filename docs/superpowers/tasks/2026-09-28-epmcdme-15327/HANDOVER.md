# Handover — EPMCDME-15327 A2A protocol upgrade (all 5 slices DONE)

Written 2026-09-30, updated after slice 5. Supersedes the per-slice handovers (`a2a-server-protocol/HANDOVER.md`, `HANDOVER-slice-3.md`, `a2a-client-protocol/HANDOVER-slice-4.md`), which are historical.

**Slice 5 (`a2a-wire-conformance/`) exists because slices 1-4 left the body shapes a v0.3/v1.0 hybrid** (found by reviewing source against `a2a.proto`). It brought the five in-scope methods to exact v1.0.1 wire shapes. Read its `spec.md`, `verification-report.md` and the slice 5 section below before touching protocol code.

Branch: `EPMCDME-15327_upgrade-a2a-protocol` (based on `main`). **Nothing is pushed, no MR exists.** All commits use the `EPMCDME-15327:` prefix (no separate Jira tickets per slice). Remaining work is release-side: verify the migration, open the MR, `/sanity`.

## Target protocol

Authoritative: `.ai-run/guides/integration/a2a-integration.md` — A2A v1.0 / v1.0.1 (`src/codemie/rest_api/a2a/version.py`, `A2A_PROTOCOL_VERSION = "1.0"`). In-house upgrade, not an `a2a-sdk` migration. Do not infer the target from the installed `a2a-sdk==0.3.26` (v0.3, `kind` discriminator).

## Slices

Artifacts per slice live in subfolders of this directory (spec, plan, technical-analysis, complexity, code-review, gate-run, decisions/events).

| # | Slice | Folder | Summary | Size (initial → actual) |
|---|---|---|---|---|
| 1 | `a2a-version-decision` | `a2a-version-decision/` | Decided target = v1.0; guide + `version.py` constant; registered in AGENTS.md | — |
| 2 | `a2a-server-protocol` | `a2a-server-protocol/` | Server: JSON-RPC `SendMessage`, `GetTask`, `CancelTask`, `GetExtendedAgentCard`, `SendStreamingMessage` (SSE); no Part discriminator; `taskId`/`contextId`; card at `/v1/a2a/assistants/{id}/.well-known/agent-card.json`; task ownership; Alembic `c9d8e7f6a5b4` | — |
| 3 | `a2a-client-protocol` | `a2a-client-protocol/` | Client: URL from `AgentCard.supportedInterfaces` via `get_jsonrpc_url`; `A2A-Version: 1.0` header everywhere; `by_alias` dumps; tolerant SSE parser; removed `BedrockAdapter`, push-notification methods, `SendTask*` aliases | M (20) → M (19) |
| 4 | `a2a-backward-compat` | `a2a-backward-compat/` | See below | M (18) → M (15) |

### Slice 4 outcome (commits `c48d605a5`, `72ae4d0d6`, `204228205`, `eef2aa197`, review fix `de4eb6233`)

User decisions (spec): read-time normalisation only, no data migration; v0.3 method names keep `-32600` plus a "1.0" hint; no `.well-known/agent.json` route; legacy NULL-owner `a2a_tasks` rows fail closed (not-found); typed exception for unusable cards and client accepts v0.3 remote cards.

- `AgentCard` before-validator (`a2a/types.py`) maps legacy `url` → `supportedInterfaces` (respects `preferredTransport` and `additionalInterfaces`; non-empty `supportedInterfaces` wins). Applies to stored cards (assistants, versions, clones) and remote cards.
- `AgentCardInterfaceError(ValueError)` from `get_jsonrpc_url`; `A2AAssistantHandler.__init__` maps it to a 400 (was a 500).
- Router `-32600` message hints version 1.0. Legacy task characterization tests added.
- Review: full profile, one blocking round (CR-001 preferredTransport, CR-002 unreachable 400 mapping), fixed, check round approved. QA gates passed (WSL, scoped).

### Slice 5 outcome (`a2a-wire-conformance`, size L 24 -> 23, head `c52a66523`)

- Wire types are now the proto shapes: `TASK_STATE_*` / `ROLE_*` enums, `Message{messageId,taskId,contextId}`, `Part{text|raw|url|data,mediaType,filename}`, `Task{id,...}`, `Artifact{artifactId}`, events with `taskId`/`contextId` and no `final`, `SendMessage` -> `{task|message}`, stream frames `{task|message|statusUpdate|artifactUpdate}`, card `supportedInterfaces[{url,protocolBinding,protocolVersion}]` + `securitySchemes` (bearer; `user-id` apiKey only when `IDP_PROVIDER=local`).
- Legacy stored JSON and remote cards load through nested before-validators (must never raise: `PydanticListType` silently drops invalid items; SQLModel table models skip validators).
- Router takes the raw body and does JSON-RPC validation itself. `A2A-Version` gate (header or query) runs after auth, before body validation; missing header = 0.3 = `-32009`. Unknown/out-of-scope methods `-32601`. **All JSON-RPC errors are HTTP 200** (spec FR-S6 amended after the official SDK client lost error codes on HTTP 400); auth failures keep 401/403/404.
- Public card hides internal fields at the serving edge only; `/assistants/fetch` keeps them (UI stores that card and reads `bedrock_agentcore`).
- Client raises `A2AProtocolVersionError` for -32601/-32600/-32009 (legacy `tasks/send` agents such as `currency_converter`, `research-agent`).
- Review: full profile, 8 findings (CR-001..007 fixed, CR-008 = earlier slices' commit subjects, skipped: needs history rewrite); check round approved. Two later fixes were made after review approval and were NOT re-reviewed: `644177b70` (BUG-1: DB-loaded `Task` failed to serialize because of a pydantic private attr; regression tests use the ORM instance path) and `c52a66523` (HTTP 200 for JSON-RPC errors).
- Verified for real (see `verification-report.md`): Alembic `c9d8e7f6a5b4` upgrade/downgrade/re-upgrade on Postgres (scratch DB); CodeMie client -> `text-insights-agent` and `howto-guide-agent` (official `a2a-sdk` 1.x); official SDK client -> CodeMie assistant (card, SendMessage, streaming, GetTask, CancelTask, GetExtendedAgentCard, all error codes); legacy agents give a clear `A2AProtocolVersionError`. 21/21 re-verification checks PASS at `c52a66523`. QA gates: lint, license-check, scoped unit PASS; build and secrets SKIPPED (tools unavailable).

## Removed / out of scope (do not silently reintroduce)

- Bedrock AgentCore (A2A v0.3) remotes: URLs containing `bedrock-agentcore` fail fast with `ValueError("Bedrock AgentCore (A2A v0.3) is not supported")`. `BedrockAdapter` is deleted; SigV4 header helper kept. A follow-up story is the route if it is needed.
- No v0.3 server translation, no `A2A-Version` negotiation, no `agent.json`.

## Known accepted gaps — do not re-litigate

- `ListTasks`, `SubscribeToTask`, push-notification CRUD are not implemented (now correctly answered with `-32601`); `returnImmediately=true` is rejected with `-32004`.
- OBS-5: the agent invocation is synchronous and blocks the server event loop, so streaming frames arrive only at the end of the run and `CancelTask` on a running task waits, then returns `-32002`. Fix is a worker thread in `codemie_task_manager.py`; not done.
- `invoke_with_a2a_output` maps any agent exception to `require_user_input=True`, so real agent failures surface as `INPUT_REQUIRED`, not `FAILED`.
- Cancel only flips stored status, does not interrupt the running invocation; cancel check not atomic.
- Slice-3 implementer did not observe RED (noted in review).
- Legacy `a2a_tasks` rows written before slice 2 keep NULL owner columns and cannot be read or cancelled through the API (fail closed).

## Open items before release

1. ~~Alembic `c9d8e7f6a5b4` never run on a real database~~ — done in slice 5 verification (upgrade, downgrade, re-upgrade OK).
2. Open the MR (`mr-creator`). The description must ask a reviewer to post `/sanity` (dependency/security-relevant changes; the repo has no pipeline config). Mention that the two fixes after review approval (`644177b70`, `c52a66523`) were not re-reviewed, and that the branch is large (5 slices).
3. Consider a short re-review (or `/code-review`) of `644177b70` and `c52a66523`.
4. Deferred review findings: `a2a-client-protocol/code-review-deferred.md`, `a2a-server-protocol/code-review-deferred.md` (some, such as the A2A-Version header and `-32601`, are now resolved by slice 5).
5. Earlier slices' commit subjects `3ca97406d` etc. lack the `EPMCDME-15327:` prefix (CR-008); only fixable by rewriting history — decide before pushing.
6. Optional follow-up: OBS-5 worker thread for agent invocation (real streaming and cancel).

## How to run tests on this machine

The native Windows poetry venv is broken and the full suite hangs on collection. Use WSL Ubuntu, scoped paths, `-n 0`:

```
wsl -d Ubuntu -- bash -lc 'cd /mnt/c/Users/Artur_Kuznetsov1/codemie-dev/codemie && /home/art_k/.local/bin/poetry run pytest tests/codemie/rest_api/a2a tests/codemie/rest_api/routers/test_a2a_router.py tests/codemie/rest_api/handlers/test_a2a_assistant_handler.py tests/codemie/rest_api/handlers/test_assistant_handlers_streaming.py -n 0 -q -p no:cacheprovider'
```

Scoped runs take ~8 minutes; use long timeouts. Run ruff in WSL too (`poetry run ruff check|format --check <paths>`).

## Pre-existing, unrelated state — leave alone

- Modified, uncommitted: `.ai-run/sdlc-factory/doctor.json`, `.codemie/codemie-cli.config.json`.
- Two git stashes: `stash@{0}` (EPMCDME-15327 pre-flight stash, unrelated codemie-cli config change) and `stash@{1}` (WIP on main, EPMCDME-13983 — someone else's work). Do not pop or drop without the user's say-so.
- Path note: slice folders were originally dated folders (`2026-09-28-a2a-version-decision`, `2026-09-29-a2a-server-protocol`, `2026-09-30-a2a-client-protocol`, `2026-09-30-a2a-backward-compat`) and were moved here in commit `aae299b32`. Some artifacts still reference the old paths.
