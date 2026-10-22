# Handover — EPMCDME-15327 A2A protocol upgrade (Slice 2 in progress)

Written 2026-09-30 to resume in a fresh session. Read this before touching anything.

## Ticket and split

EPMCDME-15327 ("Upgrade CodeMie A2A protocol support") scored XXL (34/36) and was split into
4 sequential slices, run one at a time through `sdlc-standard`, **without creating separate Jira
tickets** (user's explicit choice — all commits carry the `EPMCDME-15327` prefix regardless of
which slice they belong to):

1. `a2a-version-decision` — **DONE**
2. `a2a-server-protocol` — **IN PROGRESS** (this file's subject)
3. `a2a-client-protocol` — not started
4. `a2a-backward-compat` — not started

All work happens on branch `EPMCDME-15327_upgrade-a2a-protocol` (already checked out, based on
`main`). Do not create a new branch.

## Critical context: the version correction

Slice 1's first pass wrongly inferred the target wire shape from `BedrockAdapter` and the
transitively-installed `a2a-sdk==0.3.26` (an unrelated A2UI dependency pin), landing on "kind
discriminator" as the target marker and version `0.3.0`. **This was wrong.** External
verification against https://a2a-protocol.org/latest/specification/ and
https://github.com/a2aproject/A2A/releases confirmed the actual latest published spec is
**v1.0.1** (v1.0.0 shipped 2026-03-12 with real breaking changes — including removing the `kind`
discriminator entirely; v1.0.1 on 2026-05-28 is bug-fix-only). This was corrected in commit
`2829cb404` and slice 2's `technical-analysis.md` was redone from scratch afterward (not just
patched) to avoid carrying the wrong assumption forward.

**The authoritative source of truth for the target shape is now
`.ai-run/guides/integration/a2a-integration.md`.** Read that file, not `BedrockAdapter`, not
`a2a-sdk==0.3.26` — both of those are the *older* v0.3.x shape and are explicitly called out in the
guide as "not the target." `src/codemie/rest_api/a2a/version.py` has `A2A_PROTOCOL_VERSION = "1.0"`
confirmed and committed.

## Slice 1 status: DONE

Task dir: `docs/superpowers/tasks/2026-09-28-a2a-version-decision/` (state: `phase: done`).
Delivered: the decision (in-house upgrade, not `a2a-sdk` migration), the corrected
`a2a-integration.md` guide, and `src/codemie/rest_api/a2a/version.py` scaffold. Nothing to do here.

## Slice 2 status: IN PROGRESS — 10 of 11 plan tasks done, Task 11 stalled mid-implementation

Task dir: `docs/superpowers/tasks/2026-09-29-a2a-server-protocol/`. Read `spec.md` and `plan.md`
there for full scope — do not re-derive them. State file says `stage: 6` (code review) but
**do not proceed to code review** — `implementation.jsonl` in this same directory proves only
tasks 1-10 are done; Task 11 never got a ledger entry. Per the `sdlc-standard` skill's own resume
rule: *"stage 6 with no `code-review.local.diff` means Stage 5 was interrupted, not finished"* —
re-enter Stage 5, not Stage 6.

### What's committed (tasks 1-10, all green, do not re-touch)

```
6b034b211 Remove type discriminator from A2A Part union
fa8044458 Rename A2A JSON-RPC methods to v1.0 names and add GetExtendedAgentCard
7743d3960 Alias Task and event id fields as taskId on the wire
6f19cabb7 Reshape AgentCard to v1.0 supportedInterfaces shape
efa59b96c Update A2A conversion layer for v1.0 wire shape
6307fbf7c Rename A2A task manager methods and types to v1.0 names
aed68d35c Implement A2A cancel task and extended agent card
a195226ca Implement real A2A server-side streaming
26231b80c Rename A2A discovery route to agent-card.json
1fee06155 Wire A2A router dispatch table for SendMessage/GetTask/CancelTask/GetExtendedAgentCard
```

Full task-by-task rulings (including two legitimate design gaps found along the way — see below)
are in `docs/superpowers/tasks/2026-09-29-a2a-server-protocol/implementation.jsonl`.

### What's NOT done: Task 11 — `SendStreamingMessage` → SSE `StreamingResponse`

Per `plan.md` Task 11. The implementer got as far as:
- Import scaffolding in `src/codemie/rest_api/routers/a2a.py`: added `StreamingResponse` (from
  `fastapi.responses`) and `SendStreamingMessageRequest` imports. **Uncommitted.**
- A RED-phase test in `tests/codemie/rest_api/routers/test_a2a_router.py`:
  `test_send_streaming_message_returns_sse_frames` — posts a `SendStreamingMessage` JSON-RPC
  request and asserts `content-type: text/event-stream`, then parses `data:` SSE frames expecting
  the last frame's `result.status.state` to be a terminal state. **Uncommitted, currently failing**
  (the dispatch wiring that would make it pass was never written).

Then it stalled — no commit, no report, no running process, over an hour with no progress. The
implementer agent (dispatched via the `Agent` tool, subagent-driven mode) was stopped by the prior
session before handover; there is nothing to resume in a fresh session — **re-dispatch a new
implementer for Task 11 only** (tasks 1-10 already have ledger entries and commits; a fresh
`implementation-subagent` or `implementation-inline` run will skip them).

**Check `git status` first** — the uncommitted RED test + import scaffolding may still be sitting
in the working tree exactly as described above. If so, it's safe to build on directly (don't
discard it, it's real progress); if the working tree is clean instead, treat Task 11 as starting
from nothing.

**What Task 11 actually needs**: intercept `SendStreamingMessage` in the router's dispatch (likely
*before* the general dispatch-table lookup added in Task 10, similar to how the test comment says
"intercepted ahead of the dispatch table") and return a `StreamingResponse` that iterates
`CodemieTaskManager._handle_a2a_stream` (wired up in Task 8, already committed), formatting each
yielded event as an SSE `data: <json>\n\n` frame, ending when a terminal task state is reached.

### Two legitimate design gaps found during tasks 6 and 10 — read before code review

1. **Task 6** (already resolved by Task 7, no action needed): adding the two new abstract methods
   to `TaskManager` ABC temporarily made `CodemieTaskManager` uninstantiable between Task 6 and
   Task 7's commits. Self-healing, already closed.
2. **Task 10** (still open, needs attention at code review — do not silently "fix" without
   re-reading `spec.md` lines 78-79 and the AC at lines 116-119 first): `A2ARequest`'s
   `Field(discriminator="method")` union only covers the 5 in-scope v1.0 methods. Any method name
   outside that set (`ListTasks`, `SubscribeToTask`, push-notification CRUD) fails
   `A2ARequest.validate_python` with a `ValidationError` *before* the router's dispatch table is
   ever consulted, and gets mapped to JSON-RPC `-32600` (Invalid Request) by the unchanged
   `_handle_exception`, not the AC-specified `-32601` (Method Not Found). The weaker AC framing
   ("not a 500") is satisfied — it's a real but narrow interop nuance, not a functional break.
   Task 10's implementer adjusted its own `MethodNotFound` test to target `SendStreamingMessage`
   instead of `ListTasks` for this reason; **once Task 11 lands, that test target also gets a
   handler, so the `MethodNotFoundError` dispatch-table branch becomes dead code for every real
   request.** This is worth flagging explicitly in the code-review round for Slice 2 — it may be
   accepted as-is (per the plan's own scope, out-of-scope methods correctly return a structured
   error, just not the most precise code) or may need spec.md revisited. Don't fix it unilaterally.

### Environment note — do not mistake this for a code problem

Running the **full** test suite (`poetry run pytest tests/` / `make test`) on this Windows/py3.13
machine hangs on collection for 15+ minutes and was previously aborted — the local poetry venv is
missing native-toolchain packages (`pyroscope-io`, `tree-sitter`, `tree-sitter-languages`) that
couldn't be pip-installed without a C/Rust build chain, and `pyroscope` is imported at module load
by much of the app. **Use scoped test paths instead**, e.g.:

```
poetry run pytest tests/codemie/rest_api/a2a/ tests/codemie/rest_api/routers/test_a2a_router.py -v
```

This was already accepted once (Slice 1's QA gate) as sufficient evidence given the change surface
doesn't touch dependencies. The same reasoning applies here — don't burn time trying to fix the
venv unless the user asks.

On this same host, `pytest-xdist`'s default `-n 2` occasionally hangs when invoked via the Bash
tool specifically (not PowerShell) — Task 10's implementer worked around it with
`-n 0` under PowerShell. Pre-existing, unrelated to this slice's changes.

## Once Task 11 is actually done

Resume the `sdlc-standard` flow at **Stage 6 (code review)** for
`docs/superpowers/tasks/2026-09-29-a2a-server-protocol/`:
1. `sdlc-diff freeze <task-dir> branch`, run `sdlc-review-profile` with complexity size `L` — likely
   routes to `balanced` or `full` given the diff size (11 tasks across 4+ files) and the wire-shape
   change is public-contract-touching; do not under-route.
2. Dispatch `code-review-orchestrator` — make sure the Task 10 open gap above and the "accept
   as-is" JSONB-row decision from `spec.md`'s open_risks are both visible to the reviewer via
   `original_task`/ArtifactRefs, since they're exactly the kind of thing a lens would otherwise
   flag as a new finding when they're actually already-triaged, deliberate decisions.
3. Continue Stages 7-10 as normal (QA gates, actual complexity, commit artifacts, handoff).

## After Slice 2 fully lands

Slices 3 (`a2a-client-protocol`) and 4 (`a2a-backward-compat`) still need their own full
`sdlc-standard` pass each — same pattern as slices 1-2 (fresh Stage 1 research scoped to the
client/backward-compat surface respectively, Stage 2 complexity, brainstorm, plan, implement,
review, QA). Slice 4 depends on Slice 2 (backward-compat needs the server's final v1.0 shape to
define the compat path against) — do not start it before Slice 2 is done. Slice 3 (client) can run
independently of Slice 4 but should also come after Slice 2, since
`a2a/client/adapter.py::BedrockAdapter` currently has a hardcoded `method="message/stream"`
literal that Slice 2's dispatch-table rename already made stale (a known, accepted residual gap
noted in Slice 2's plan) — Slice 3 needs to fix that as part of its own scope.

## Pre-existing, unrelated state — leave alone

- `git status` shows `.ai-run/sdlc-factory/doctor.json` and `.codemie/codemie-cli.config.json`
  modified — both pre-existing, unrelated to this ticket (tool-cache/config side-effects from
  running various SDLC Factory and `codemie` CLI commands). Do not commit or revert them as part of
  this work.
- Two stashes exist (`git stash list`): `stash@{0}` ("EPMCDME-15327 pre-flight stash: unrelated
  codemie-cli config change") and `stash@{1}` ("WIP on main: ... EPMCDME-13983 ..."). Neither is
  related to this ticket. Do not pop or drop either without the user's explicit say — `stash@{1}`
  in particular looks like someone else's in-progress work from before this session started.
