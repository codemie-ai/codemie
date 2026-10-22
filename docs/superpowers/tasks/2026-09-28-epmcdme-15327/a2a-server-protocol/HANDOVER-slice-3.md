# Handover — EPMCDME-15327 A2A upgrade: Slice 2 DONE, start Slice 3 `a2a-client-protocol`

Written 2026-09-30. Start Slice 3 in a fresh session with `sdlc-standard`. This file supersedes `HANDOVER.md` (same dir), which described Slice 2 mid-flight and is now historical.

## Ticket and slices

EPMCDME-15327 ("Upgrade CodeMie A2A protocol support") was split into 4 sequential slices, run one at a time through `sdlc-standard`, **without separate Jira tickets** — all commits carry the `EPMCDME-15327:` prefix.

1. `a2a-version-decision` — DONE (`docs/superpowers/tasks/2026-09-28-a2a-version-decision/`)
2. `a2a-server-protocol` — DONE, `phase: done` (`docs/superpowers/tasks/2026-09-29-a2a-server-protocol/`)
3. `a2a-client-protocol` — **NEXT** (this handover)
4. `a2a-backward-compat` — after slice 3 (needs the server's final v1.0 shape, which now exists)

Work on the existing branch `EPMCDME-15327_upgrade-a2a-protocol` (based on `main`). Do not create a new branch. Nothing is pushed and no MR exists yet; do not push or open an MR unless asked.

## Target protocol

Authoritative source: `.ai-run/guides/integration/a2a-integration.md` — **A2A v1.0 / v1.0.1** (`src/codemie/rest_api/a2a/version.py`: `A2A_PROTOCOL_VERSION = "1.0"`). Do NOT infer the target from `BedrockAdapter` or the transitively installed `a2a-sdk==0.3.26`; those are the older v0.3.x shape (with the `kind` discriminator) and the guide calls them "not the target". Decision: in-house upgrade, not an `a2a-sdk` migration.

## What Slice 2 delivered (server, committed)

Commits, oldest first, all on the branch: `6b034b211` Part discriminator removed; `fa8044458` method renames; `7743d3960` `taskId` wire alias; `6f19cabb7` AgentCard v1.0 `supportedInterfaces`; `efa59b96c` conversion layer; `6307fbf7c` task-manager renames; `aed68d35c` cancel + extended card; `a195226ca` real streaming; `26231b80c` discovery route renamed to `agent-card.json`; `1fee06155` router dispatch table; `a0fa15a71` SSE `SendStreamingMessage`; `30d4623a8` review fixes; `c19c93d62` unique alembic revision id; `2210a1918` ruff fixes; `828779309` planning artifacts.

Server wire shape now:
- JSON-RPC methods: `SendMessage`, `GetTask`, `CancelTask`, `GetExtendedAgentCard`, `SendStreamingMessage`. Anything else (e.g. `ListTasks`, `SubscribeToTask`, push-notification CRUD) returns `-32600` (accepted user decision, CR-006), not `-32601`.
- No `kind`/`type` discriminator on `Part`. Task/events use `taskId`, plus `contextId`.
- `SendStreamingMessage` returns `text/event-stream`; each `data:` frame is a JSON-RPC response carrying a status-update or artifact-update event (`final` on terminal states).
- Discovery: `/v1/a2a/assistants/{id}/.well-known/agent-card.json`.
- Tasks are owned per assistant/user; sending to a terminal task returns `UnsupportedOperationError`; a completed/failed write cannot overwrite a cancel.
- Alembic migration `c9d8e7f6a5b4` adds `contextId`, `assistant_id`, `user_id` to `a2a_tasks` (down_revision `u2v3w4x5y6a7`). It was added by the review fix-up although spec.md listed migrations as out of scope, and it has **never been run against a real database** — verify before release (relevant to slice 4).
- Known accepted gaps from Slice 2 (do not re-litigate): pre-existing `a2a_tasks` JSONB rows are not migrated (slice 4 territory); cancel only flips stored status and does not interrupt the running invocation; the cancel check is not atomic; the SSE stream lacks the v1.0 `statusUpdate`/`artifactUpdate` envelope wrapper (open design gap in the final review's `business_review`).

## Slice 3 scope — the client side (`src/codemie/rest_api/a2a/client/`)

Files: `adapter.py`, `client.py`, `card_resolver.py`, `remote_agent_connection.py`. Tests: `tests/codemie/rest_api/a2a/client/{test_client,test_card_resolver,test_remote_agent_connection}.py`.

Known stale spots (found by grep, not yet analysed — do fresh Stage 1 research, don't trust this as complete):
- `adapter.py`: `BedrockAdapter` builds `SendTaskStreamingRequest(method="message/stream", ...)` (line ~88) — hardcoded old method literal; also converts `kind` <-> `type` on parts (lines ~47, ~80-83) which is the v0.3 discriminator model.
- `client.py`: uses `SendTaskRequest`, `GetTaskRequest`, `CancelTaskRequest`, `SetTaskPushNotificationRequest`, `GetTaskPushNotificationRequest`; slice 2 renamed these types/methods to the v1.0 names in `types.py` (a `SendTaskStreamingResponse = SendStreamingMessageResponse` alias exists at `types.py:~269`; check what other old names still resolve). Push-notification CRUD is not implemented server-side in v1.0 scope.
- `card_resolver.py`: `_build_url` tries `.well-known/agent.json` and `agent-card.json`; align with `agent-card.json` as the discovery path (keep tolerant fallback only if the spec/guide says so).
- Payload normalisation in `client.py` `send_task_streaming` (`normalized` dict, ~line 85-104) likely assumes old event shapes.

Failing tests observed at the end of Slice 2 (scoped run of `tests/codemie/rest_api/a2a/`): all under `tests/codemie/rest_api/a2a/client/` — 6 failures + 23 errors (`test_card_resolver.py` fetch tests, `test_client.py::TestA2AClientGetHeader::test_get_header_aws_signature`, and most of `test_client.py` and `test_remote_agent_connection.py`, which error at setup). These were **not** investigated and **not** compared against a clean `main` checkout — first check how many fail on `main` vs. on this branch so you know which are regressions from slice 2's renames and which pre-existed. They are excluded from Slice 2's QA gate on the grounds that they are Slice 3 scope; Slice 3's own QA gate must include them.

Other consumers of A2A to check in research: `src/codemie/agents/assistant_agent.py`, `src/codemie/agents/langgraph_agent.py`, `src/codemie/rest_api/handlers/assistant_handlers.py`, `src/codemie/rest_api/models/assistant.py`, `src/codemie/service/settings/settings.py` (they reference a2a; whether they touch the client wire shape is unverified).

Out of scope for slice 3: server code (done), backward compatibility with old-shape remote agents / stored rows (slice 4), Alembic. If the client must still talk to v0.3.x remote agents (e.g. Bedrock AgentCore), that is a design question to raise in brainstorming — the slice-4 compat story is the natural home, but the client can't simply drop `BedrockAdapter` if Bedrock remotes are in use. Decide this explicitly at the spec gate.

## How to run tests on this machine

The Windows native poetry venv is broken (missing `pyroscope-io`, `tree-sitter`, no C/Rust toolchain); the full suite hangs on collection. **Use WSL Ubuntu**, scoped paths, and `-n 0` (xdist `-n 2` can hang):

```
wsl -d Ubuntu -- bash -lc 'cd /mnt/c/Users/Artur_Kuznetsov1/codemie-dev/codemie && poetry run pytest tests/codemie/rest_api/a2a/ tests/codemie/rest_api/routers/test_a2a_router.py -n 0 -q -p no:cacheprovider'
```

A scoped run takes ~2-4 minutes (mostly startup) — use timeouts up to 10 minutes. `poetry` is at `/home/art_k/.local/bin/poetry` inside WSL; no `.venv` in the repo. Run ruff in WSL too (`poetry run ruff check|format --check <paths>`). Router tests have no Postgres, so they patch `Task.find_by_id` / `Task.update`. Slice 2 QA gates that passed: ruff check + format, license-header check, and 85 scoped unit tests (server, types, utils, version, router).

## sdlc-standard notes for the new session

- Start the skill fresh: `sdlc-standard` with a task description for Slice 3, e.g. "EPMCDME-15327 slice 3 a2a-client-protocol: upgrade the CodeMie A2A client (adapter/client/card_resolver/remote_agent_connection) to protocol v1.0 per .ai-run/guides/integration/a2a-integration.md; server side is done." Suggested slug `a2a-client-protocol`.
- Stage 0 will report a dirty tree (3 unrelated items, below). At Slice 2 the answer was `proceed-dirty`; the handover recommends the same.
- Stage 1 (research) is mandatory and must be scoped to the client surface. `codegraph` was reported absent for this repo, so `tech-analyst-filesystem` will be used.
- Slice 2 initial complexity was L (23/36), actual L (22/36); expect Slice 3 to be smaller (M-ish) since it is client-only, but let Stage 2 decide.
- Jira: Stage 2/8 adapter writes (`record_complexity_score`) target the shared ticket EPMCDME-15327 (`Labels` field, t-shirt). At Slice 2's Stage 8 the user chose **skip Jira, record as `skipped`**. Ask again rather than assume; one score for all four slices sitting on one ticket is the concern.
- Every gate is ask-and-record (HITL): spec, plan, code-review final/check. Never approve for the user.
- Stage 6 code review router will likely pick `balanced` or `full` for a public-contract-touching change; do not under-route. Pass the already-triaged decisions in `original_task`/ArtifactRefs so lenses don't re-raise them: (a) `-32600` for out-of-scope methods accepted, (b) no compat shim for legacy JSONB rows, (c) server-side gaps listed above.
- Review-fix and QA-fix rounds are done by the implementer agent (`implementation-inline`, `mode: review-fix` / `qa-fix`), never by editing source yourself in the orchestrating context. Max one review fix-up round and max 3 qa-fix attempts.
- Lesson from Slice 2: implementers append to `implementation.jsonl`; when the orchestrator does the last task inline it must add the ledger line itself (task_id, status, commit, test_command, ruling). `commit-artifacts` at Stage 9 commits the task dir.
- Lesson from Slice 2: a check round marks findings "unresolved" if a user decision (e.g. "accepted, not fixing") isn't passed as `fix_evidence` mapped by finding id — pass decisions explicitly next time (see `references/code-review/check-round.md`).
- Lesson from Slice 2: Alembic revision ids must be unique — verify against `src/external/alembic/versions/` before adding a migration (slice 4 may need one).

## Pre-existing, unrelated state — leave alone

- Modified, uncommitted, not part of this work: `.ai-run/sdlc-factory/doctor.json`, `.codemie/codemie-cli.config.json`. Untracked: `docs/superpowers/tasks/2026-09-28-epmcdme-15327/` (an earlier, superseded task dir; not committed — leave it).
- Two git stashes exist: `stash@{0}` (EPMCDME-15327 pre-flight stash, unrelated codemie-cli config change) and `stash@{1}` (WIP on main, EPMCDME-13983 — someone else's in-progress work). Do not pop or drop either without the user's say-so.
- This file (`HANDOVER-slice-3.md`) is untracked (not gitignored) and uncommitted; the older `HANDOVER.md` is tracked. Commit this file only if the user asks.
