# Handover — EPMCDME-15327 A2A upgrade: Slice 3 DONE, start Slice 4 `a2a-backward-compat`

Written 2026-09-30. Start Slice 4 in a fresh session with `sdlc-standard`. Supersedes `docs/superpowers/tasks/2026-09-29-a2a-server-protocol/HANDOVER-slice-3.md` (historical).

## Ticket and slices

EPMCDME-15327 was split into 4 sequential slices run through `sdlc-standard`, **without separate Jira tickets** — all commits carry the `EPMCDME-15327:` prefix.

1. `a2a-version-decision` — DONE
2. `a2a-server-protocol` — DONE (`docs/superpowers/tasks/2026-09-29-a2a-server-protocol/`)
3. `a2a-client-protocol` — DONE, `phase: done` (`docs/superpowers/tasks/2026-09-30-a2a-client-protocol/`)
4. `a2a-backward-compat` — **NEXT** (this handover)

Work on the existing branch `EPMCDME-15327_upgrade-a2a-protocol` (based on `main`). Do not create a new branch. Nothing is pushed and no MR exists; do not push or open an MR unless asked.

## Target protocol

Authoritative: `.ai-run/guides/integration/a2a-integration.md` — A2A v1.0 / v1.0.1 (`src/codemie/rest_api/a2a/version.py`, `A2A_PROTOCOL_VERSION = "1.0"`). In-house upgrade, not an `a2a-sdk` migration. Do not infer the target from the installed `a2a-sdk==0.3.26` (v0.3 shape, `kind` discriminator).

## What is already done

- **Server (slice 2):** JSON-RPC `SendMessage`, `GetTask`, `CancelTask`, `GetExtendedAgentCard`, `SendStreamingMessage` (SSE). Other methods return `-32600`. No Part discriminator; `taskId` + `contextId`. Discovery at `/v1/a2a/assistants/{id}/.well-known/agent-card.json`. Alembic migration `c9d8e7f6a5b4` (adds `contextId`, `assistant_id`, `user_id` to `a2a_tasks`; down_revision `u2v3w4x5y6a7`) — **never run against a real database**.
- **Client (slice 3), commits `542b5508c`, `ca6ecc119`, `eb2d5b95f`, `f4a8ddaa8`, `e5f31d2d6`, review fix `2347a1265`, artifacts `5b6bfba11`:**
  - `A2AClient` takes its URL from `AgentCard.supportedInterfaces` via `get_jsonrpc_url` (in `types.py`); `agent_card.url` no longer exists.
  - Card discovery only at `.well-known/agent-card.json`; `A2A-Version: 1.0` header on every request incl. card fetch; `verify` from config on all calls.
  - Requests dumped `by_alias=True` (`taskId`/`contextId`); request param models accept old names (`id`/`sessionId`) on input via `AliasChoices`.
  - Streaming: `httpx.AsyncClient` + `aconnect_sse`; tolerant parser accepts bare frames and the v1.0 `statusUpdate`/`artifactUpdate` envelope; `ValidationError`/`SSEError` mapped to `A2AClientJSONError`.
  - **Removed:** `BedrockAdapter` (`adapter.py` deleted), the `bedrock_agentcore` A2A path (URLs containing `bedrock-agentcore` fail fast with `ValueError("Bedrock AgentCore (A2A v0.3) is not supported")` in resolver and client; SigV4 header helper kept), push-notification client methods, `SendTask*` aliases.
  - `A2AAssistantHandler` (`src/codemie/rest_api/handlers/assistant_handlers.py`, ~line 1360) now resolves the URL through `get_jsonrpc_url`.
- QA at slice 3 end: ruff check + format, license-header check, and 139+ scoped tests passed (`tests/codemie/rest_api/a2a`, `handlers/test_a2a_assistant_handler.py`, `routers/test_a2a_router.py`).

## Slice 4 scope — backward compatibility

Fresh Stage 1 research needed; these are the known inputs, not a complete analysis.

1. **Legacy stored `Assistant.agent_card` rows** (JSONB, old v0.3/pre-1.0 shape: `url`, no `supportedInterfaces`, possibly `kind` parts). Today `get_jsonrpc_url` raises a raw `ValueError` for url-only or bedrock cards, surfacing as a **500 at chat time** (`assistant_handlers.py:~1367`; deferred finding from the slice-3 review). Decide: read-time normalisation (map `url` → a synthesized `supportedInterfaces` entry), a data migration, or a clear typed error. Also check where else stored cards are validated (`models/assistant.py`, `routers/assistant.py` ~774 `fetch_remote_assistant`, `service/settings/settings.py`).
2. **Legacy `a2a_tasks` JSONB rows** written before slice 2 (old part/message shape, no `contextId`/owner columns populated). Slice 2 explicitly did not migrate them.
3. **Server accepting v0.3 clients**: the guide says a missing `A2A-Version` header means 0.3 to the server. Determine what the guide requires for old clients (method names like `message/send`, `tasks/get`; `kind` on parts; `.well-known/agent.json` path) and whether the server should accept/translate or reject clearly. Slice 2 returns `-32600` for unknown methods (accepted decision), so old method names currently get `-32600`.
4. **Old remotes**: Bedrock AgentCore (v0.3) remotes stopped working in slice 3 by user decision (Option A, drop). Slice 4 may decide whether a follow-up/compat story is warranted; do not silently reintroduce `BedrockAdapter`.
5. **Alembic**: the `c9d8e7f6a5b4` migration is unverified against a real DB — verify (upgrade + downgrade) before release. Any new revision id must be unique: check `src/external/alembic/versions/` before adding one.

## Known accepted gaps — do not re-litigate

- `-32600` (not `-32601`) for out-of-scope methods (`ListTasks`, `SubscribeToTask`, push-notification CRUD) — CR-006.
- Server SSE frames lack the v1.0 `statusUpdate`/`artifactUpdate` envelope; client parser is deliberately tolerant. Tighten only if slice 4 changes the server.
- Cancel only flips stored status, does not interrupt the running invocation; cancel check not atomic.
- Slice-3 implementer did not observe RED before implementing (noted in review).
- **Unverified:** `tests/codemie/rest_api/handlers/test_assistant_handlers_streaming.py` builds `AgentCard(url=...)` with no `supportedInterfaces`; it was never run in slices 2–3 and may fail. Run it first in slice 4 and check it against `main` to see whether it is a regression.

## How to run tests on this machine

The native Windows poetry venv is broken; the full suite hangs on collection. Use WSL Ubuntu, scoped paths, `-n 0`:

```
wsl -d Ubuntu -- bash -lc 'cd /mnt/c/Users/Artur_Kuznetsov1/codemie-dev/codemie && poetry run pytest tests/codemie/rest_api/a2a/ tests/codemie/rest_api/routers/test_a2a_router.py tests/codemie/rest_api/handlers/test_a2a_assistant_handler.py -n 0 -q -p no:cacheprovider'
```

Scoped runs take ~2–8 minutes; use timeouts up to 10 minutes. `poetry` is at `/home/art_k/.local/bin/poetry`. Run ruff in WSL too (`poetry run ruff check|format --check <paths>`). Alembic/DB work needs a real Postgres — none is available by default, so ask the user how to verify migrations.

## sdlc-standard notes for the new session

- Invoke: `sdlc-standard` with e.g. "EPMCDME-15327 slice 4 a2a-backward-compat: add backward compatibility for legacy (pre-v1.0) stored agent cards, a2a_tasks rows and old-shape A2A clients per .ai-run/guides/integration/a2a-integration.md; server (slice 2) and client (slice 3) are done." Suggested slug `a2a-backward-compat`.
- Stage 0 will report a dirty tree (unrelated, below). Previous answer: `proceed-dirty`.
- `codegraph` is absent for this repo → `tech-analyst-filesystem`.
- Slice 3 sizes: initial M (20/36), actual M (19/36). Slice 4 touches migrations/data contracts, so expect a full review profile.
- Jira: at slices 2 and 3 you chose **skip, record `skipped`** for the complexity score on the shared ticket. Ask again rather than assume.
- All gates are ask-and-record (HITL): spec, plan, code-review final/check. Never approve for the user.
- **Lesson (slice 3):** `sdlc-diff freeze <task-dir> branch` defaults to the merge-base with `main`, which pulled slices 1–2 into the review diff (3,211 lines). Re-freeze with `--base <sha of the last commit before this slice's first task commit>` so the review covers only this slice. Do this at Stage 6 step 1, and pass the same sha as `diff_base`.
- **Lesson (slice 3):** the check-round diff (`sdlc-diff freeze <dir> check`) includes unrelated dirty working-tree files (`doctor.json`, `codemie-cli.config.json`); tell the check-round reviewer to ignore them, or stash beforehand.
- **Lesson (slice 3):** the implementer skipped observing RED for T1–T5. Tell it explicitly that RED must be run and seen before implementing.
- **Lesson (slice 3):** a stopped QA-gates runner returns no digest; re-dispatch it (the user was asked and chose re-run). qa-gates takes ~8 minutes in WSL.
- Review-fix and QA-fix rounds go through the implementer agent (`implementation-inline`, `mode: review-fix` / `qa-fix`); never edit source from the orchestrating context. Max one review fix-up round, max 3 qa-fix attempts.
- Pass user decisions to the check round via `fix_evidence` mapped by finding id (see `references/code-review/check-round.md`), otherwise they are marked unresolved.

## Pre-existing, unrelated state — leave alone

- Modified, uncommitted: `.ai-run/sdlc-factory/doctor.json`, `.codemie/codemie-cli.config.json`. Untracked: `docs/superpowers/tasks/2026-09-28-epmcdme-15327/` (earlier, superseded task dir) and `docs/superpowers/tasks/2026-09-29-a2a-server-protocol/HANDOVER-slice-3.md`.
- Two git stashes: `stash@{0}` (EPMCDME-15327 pre-flight stash, unrelated codemie-cli config change) and `stash@{1}` (WIP on main, EPMCDME-13983 — someone else's work). Do not pop or drop without the user's say-so.
- This file (`HANDOVER-slice-4.md`) is untracked and uncommitted; commit only if the user asks.
