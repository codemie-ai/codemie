# Technical Research

**Task**: workflow conversation hydrate DualQueue
**Generated**: 2026-08-14
**Research path**: filesystem

---

## 1. Original Context

Use this file as the `task_description` for an **`sdlc-light`** run in the **`codemie`** repository
(or **`sdlc-standard`** if you prefer a written spec). Do not start work in `codemie-ui` in this run.

Companion how-to (validated 2026-08-14): `docs/fix/backend-fix.md`

## Mandatory SDLC process rules

1. Run the chosen SDLC entry point (`sdlc-light`) end-to-end.
2. **Complete every required stage** of that flow through handoff. Do not stop after research, plan, or implementation. For `sdlc-light` that means Stages 0–7 (pre-flight → research → clarity → plan → TDD implement → code review → qa-gates / feature verification → handoff). For `sdlc-standard`, complete that flow’s equivalent full stage sequence through handoff.
3. Honor HITL gates (branch guard, clarity questions, code-review decisions, feature verification). Do not skip review or QA.
4. Follow repo guides under `.ai-run/guides/` (git workflow, quality gates, testing patterns).
5. Reference ticket **EPMCDME-14075** in commits / branch naming per repo conventions.

## Independent analysis mandate

The companion `backend-fix.md` is a **validated root-cause write-up**, not a license to skip research.

You MUST:

- Re-read the current code (it may have drifted).
- Confirm or correct the findings in `backend-fix.md`.
- If research finds a **different root cause** or a **better fix**, prefer your findings and document them in run artifacts.
- Do **not** implement SSE stream-rejoin unless you prove it is the only correct approach.

Treat file paths in `backend-fix.md` as starting points.

## Ticket

- **Key:** EPMCDME-14075
- **Type:** Bug
- **Summary:** Workflow execution progress becomes empty after page reload
- **Labels:** Frontend, AI/Run, Horizon1, codemie_feedback (backend owns persistence/hydrate and disconnect behavior)

## Problem (product)

When a user runs a **workflow chat**, sends a message, and **reloads the browser while execution is still in progress**, the conversation no longer shows the live workflow progress. After reload the answer / progress UI is empty (or otherwise fails to restore the current step), instead of continuing to reflect the running execution.

Desired behavior after refresh:

- Observe the **step currently in progress** (same kind of progress UI as before refresh).
- When that step completes, observe its **result**.
- Continue observing **later steps** until a terminal status — as if the page had not been refreshed.
- Intra-step character-by-character token streaming after reload is **not** required.

Reproduce:

1. Authenticated user opens a runnable workflow chat.
2. Send a message (e.g. `hi`) to start execution.
3. While a workflow step is **IN PROGRESS**, reload the page.
4. Progress/status should still be visible and should keep updating as the run continues.

## Scope for THIS run (backend only)

In **`codemie` only**, fix hydrate + disconnect so the client can obtain accurate workflow-execution progress after reload and the run keeps going without the original SSE client.

Out of scope:

- UI / `codemie-ui` changes (sibling run).
- Unrelated workflow-editor or non-chat execution features, unless they share the same broken path and a small shared fix is safer.
- Full SSE stream rejoin.
- Persisting intra-step token chunks in `ThoughtConsumer`.

If backend alone cannot fully satisfy the product outcome, still ship the contract the frontend needs, document it in handoff, and do not expand into the UI repo.

## Validated findings (re-verify)

These were confirmed against `codemie` on 2026-08-14. Re-check before implementing.

1. **CONFIRMED — stub history.** Chat writes `workflow_execution_ref` stubs (`workflow_service.py`); thoughts/message are materialized on GET.
2. **CONFIRMED — materializer hides the current step.** `_get_execution_thoughts` keeps only states with `output` or `ABORTED`, and hardcodes `in_progress: False`. In-progress rows exist (`start_state` saves `IN_PROGRESS` with `output=None`).
3. **PARTIAL — live UI is stream-only for tokens; states are persisted.** Empty chat after reload is mainly the materializer filter, not missing state rows. Nested `in_progress` thoughts are still skipped by `ThoughtConsumer` (acceptable).
4. **CONFIRMED — Details APIs already return in-progress states.** Chat hydrate does not.
5. **CONFIRMED extra — disconnect can stop the agent.** `DualQueue.is_closed()` follows the streaming queue. Workflow agents use DualQueue as `thread_generator`, so tab reload can stop the node (`LangGraphAgent._should_stop_streaming`). Persistence was meant to continue. **Fix this** so later steps can still run; otherwise FE polling will show a stuck/failed step.

Recommended implementation details: `backend-fix.md` (materializer + `execution_status` on the turn + DualQueue `is_closed` semantics).

## Acceptance criteria (backend contribution)

- After reload during/after workflow chat execution, conversation GET exposes enough accurate state for the UI to show current progress (not an empty assistant turn when execution state exists).
- In-progress states appear as thoughts with `in_progress: true`.
- The hydrated assistant message exposes execution identity **and** overall status so the UI can tell a run is still alive between steps.
- Client SSE disconnect does not, by itself, stop/abort a workflow execution.
- If state truly cannot be restored, the API must not look like a successful blank turn when an active execution exists.
- No regression for normal execution without reload, or for completed/aborted/interrupted executions.
- Tests cover in-progress hydrate and DualQueue disconnect-vs-persist behavior (TDD per SDLC).

## Coordination

- In handoff, document the exact JSON fields (`executionId`, `executionStatus`, thought `in_progress`) the frontend run must consume.

## Done means

SDLC run finished through **handoff**, with code + tests on the feature branch according to the flow, QA/review gates passed or explicitly resolved with the user, and a clear contract note for the frontend follow-up.

---

## 2. Codebase Findings

### Re-verification of `docs/fix/backend-fix.md`

Current source matches the companion write-up. **No different root cause was found.** SSE stream-rejoin is **not** required: `WorkflowExecutionState` rows already exist at step start, and Details REST already returns them. Chat hydrate must use that persisted state.

| Claim in `backend-fix.md` | Verdict against current code |
|---|---|
| Chat writes `workflow_execution_ref` stubs | **Confirmed** — `workflow_service.py` create (~272–281) and resume (~478–487) |
| Materializer drops current IN_PROGRESS step | **Confirmed** — `_get_execution_thoughts` filter + hardcoded `in_progress: False` |
| Live tokens are stream-only; states are persisted | **Confirmed** — `start_state` saves `IN_PROGRESS` with `output=None`; `ThoughtConsumer` skips `in_progress` thoughts |
| Details APIs return in-progress states | **Confirmed** — states index has no status filter on the Details route |
| `DualQueue.is_closed()` follows streaming queue and can stop the agent | **Confirmed** — contradicts DualQueue class docstring |
| `GeneratedMessage` has no overall execution status | **Confirmed** — only `workflow_execution_ref` + `execution_id` |

### Existing Implementations

- **`src/codemie/service/conversation/history_materializer.py`**
  - `materialize_workflow_conversation` — replaces stubs when `workflow_execution_ref` and `execution_id` are set; on failure keeps the original stub.
  - `_materialize_execution_reference` — loads execution via `WorkflowService.find_workflow_execution_by_id`, builds thoughts, sets `message` from `execution.output` then last thought then last completed state output. **Does not copy `execution.overall_status`.**
  - `_get_execution_thoughts` — loads `WorkflowExecutionState` by `execution_id`, optionally filters by `history_index`, then:

```156:170:src/codemie/service/conversation/history_materializer.py
        return [
            {
                "id": state.id,
                "author_name": state.name,
                "author_type": "WorkflowState",
                "message": state.output or "",
                "input_text": None,
                "children": [],
                "in_progress": False,
                "interrupted": state.status == WorkflowExecutionStatusEnum.INTERRUPTED,
                "aborted": state.status == WorkflowExecutionStatusEnum.ABORTED,
            }
            for state in states
            if state.output or state.status == WorkflowExecutionStatusEnum.ABORTED
        ]
```

  IN_PROGRESS rows with `output=None` are dropped. FAILED-without-output is also dropped (same class of gap as ABORTED handling). `input_text` is always `None` even though `WorkflowExecutionState.task` is populated in `start_state`.

- **`src/codemie/service/workflow_service.py`** — on chat create/resume appends a user message plus assistant stub: `workflow_execution_ref=True`, `execution_id=...`, `thoughts=[]`, `message=None`. Execution is created with `overall_status=IN_PROGRESS`.

- **`src/codemie/rest_api/models/conversation.py`**
  - `GeneratedMessage` workflow fields: `workflow_execution_ref`, `execution_id` only. **No `execution_status`.**
  - `Conversation.get_by_id` / `find_by_id` always call `materialize_workflow_conversation`.
  - `ChatMessage` extends `ConfiguredModel` (`alias_generator=to_camel`) so new `execution_status` serializes as JSON `executionStatus` when aliases are used.
  - Nested `Thought` (`src/codemie/chains/base.py`) is a **plain `BaseModel` without `to_camel`**. Thought JSON may stay snake_case (`in_progress`, `author_name`) even when parent message fields are camelCase. Handoff must document the actual serialized names, not assume the example in `backend-fix.md`.

- **`src/codemie/rest_api/routers/conversation.py`** — `GET /conversations/{conversation_id}` → `get_conversation_by_id` (paginated slice or `Conversation.find_by_id`). Both paths materialize.

- **`src/codemie/service/conversation_service.py`** — `get_conversation_history_slice` also materializes (~1498–1499).

- **`src/codemie/core/dual_queue.py`**
  - Class docstring: streaming loss should leave persistence unaffected.
  - `send()` always writes both queues (no skip if streaming is closed). `ThreadedGenerator.send` still `queue.put` after `close()`, so unread items can accumulate.
  - `is_closed()` returns `self.streaming_queue.is_closed()`.
  - `close()` closes both. `.queue` exposes the persistence queue for `ThoughtConsumer`.
  - **Sole production constructor:** `src/codemie/rest_api/routers/workflow_executions.py` (stream create and stream resume). Changing DualQueue semantics does not affect plain assistant `ThreadedGenerator` chats.

- **`src/codemie/rest_api/routers/utils.py`**
  - `_handle_streaming_execution` registers `on_disconnect` on the **streaming** `ThreadedGenerator` only (not DualQueue).
  - `_handle_client_disconnect` closes that generator. This is correct for DualQueue if `is_closed()` is fixed; do **not** call `DualQueue.close()` from disconnect.

- **`src/codemie/agents/langgraph_agent.py`** — `_should_stop_streaming` returns True when `thread_generator.is_closed()`, logs `"user is disconnected"`. Workflow agents receive DualQueue as `thread_generator` via `workflows/utils/utils.py` `initialize_assistant` → `AssistantService.build_agent_for_workflow`.

- **`src/codemie/agents/assistant_agent.py`** — `AIToolsAgent._agent_streaming` uses the same `is_closed()` stop (used when `ENABLE_LANGGRAPH_AITOOLS_AGENT` is false). Same DualQueue coupling.

- **`src/codemie/service/workflow_execution/workflow_execution_service.py`** — `start_state` persists `status=IN_PROGRESS`, `task=str(task)`, default `output=None`, then streams a `STATE_START` event. `finish_state` sets `output` and terminal status.

- **`src/codemie/service/workflow_execution/thought_consumer.py`** — `if thought_data.in_progress: continue`. Nested token thoughts are not stored. **Acceptable / out of scope.** Step cards after reload come from `WorkflowExecutionState`, not thought trees.

- **`src/codemie/core/workflow_models/workflow_execution.py`**
  - `WorkflowExecutionStatusEnum.IN_PROGRESS = "In Progress"` (and Succeeded, Aborted, Failed, Interrupted, …).
  - Tables: `workflow_executions` (JSONB `history`, `overall_status`, `conversation_id`), `workflow_execution_states` (`output` optional, `status`, `history_index`, `task`), `workflow_execution_state_thoughts`.
  - `workflow_execution_ref` is **not** a DB column; it lives on `GeneratedMessage` inside JSONB `conversations.history`.

- **Details APIs** (`src/codemie/rest_api/routers/workflow_executions.py`)
  - `GET .../executions/{execution_id}` returns `overall_status`.
  - `GET .../executions/{execution_id}/states` → `WorkflowExecutionStatesIndexService.run` with **no** `states_status_filter` — IN_PROGRESS rows with `output=None` are returned.
  - Conversation hydrate synthesizes flat thoughts from **state outputs**; Details returns real states plus persisted thought trees. Chat polling of GET conversation is enough **if** materializer + `execution_status` land.

### Architecture and Layers Affected

| Layer | Components |
|---|---|
| **API / models** | `GeneratedMessage` (add `execution_status`); conversation GET already hydrates; no new route required |
| **Service** | `history_materializer._get_execution_thoughts` and `_materialize_execution_reference` (primary hydrate fix) |
| **Core** | `DualQueue.is_closed` / `send` (disconnect-vs-persist) |
| **Agents** | No source change if DualQueue semantics change; `LangGraphAgent._should_stop_streaming` and `AIToolsAgent._agent_streaming` consume `is_closed()` |
| **Workflow orchestration** | `WorkflowExecutor`, `initialize_assistant`, `start_state` — already persist IN_PROGRESS; leave as-is |
| **DB / persistence** | No migration. `ThoughtConsumer` skip stays. `WorkflowExecutionState` already has the rows |
| **Routers / disconnect** | `utils._handle_client_disconnect` stays streaming-only |

Estimated production change surface: **3 files** (`history_materializer.py`, `conversation.py` model field, `dual_queue.py`) plus **2 test files**. Disconnect handler and agents should not need edits.

### Integration Points

- Stream create/resume builds DualQueue in `workflow_executions.py`; HTTP iterates the inner `ThreadedGenerator`; ThoughtConsumer reads DualQueue `.queue`.
- Disconnect → `_handle_client_disconnect(generator_queue)` → streaming half closed → today DualQueue reports closed → agents stop.
- Hydrate: GET conversation / history slice → materializer → `WorkflowExecutionState.get_all_by_fields`.
- Parallel Details: execution + states endpoints already expose IN_PROGRESS / `overall_status`.
- In-process only: `queue.Queue` (no Redis/Kafka). PostgreSQL via SQLModel. No feature flag or env toggle for this behavior.

### Patterns and Conventions

- **Stub on write, materialize on read** — do not persist full thoughts on the conversation row; resolve from execution state at GET.
- **Dual-queue fan-out** — stream vs persist; disconnect must close streaming only.
- **Agent stop gate** — `thread_generator.is_closed()` at chunk boundaries. For DualQueue this must mean “workflow cancelled / DualQueue.close()”, not “SSE client gone”.
- **State lifecycle** — `start_state` → IN_PROGRESS / null output → `finish_state` / `abort_state`.
- **Active-record models** — `state.save()`, `get_all_by_fields`; no separate DAO on this path.
- **API models** — typed fields on `GeneratedMessage` under `rest_api/models/`; camelCase via `ConfiguredModel` for message-level fields.
- **Tests** — mirror `src/` under `tests/codemie/`; mock `WorkflowExecutionState.get_all_by_fields` for materializer; real `ThreadedGenerator` + `ThoughtQueue` for DualQueue. Do **not** mock `_get_execution_thoughts` for the new hydrate cases.

### Recommended implementation (confirmed, not SSE rejoin)

1. **Materializer** — include `IN_PROGRESS` (and FAILED-without-output, same as ABORTED). Set `in_progress` / `interrupted` / `aborted` from status. Prefer `input_text: state.task`. Update docstring.
2. **`execution_status` on `GeneratedMessage`** — copy `execution.overall_status` in `_materialize_execution_reference` so between-steps polls still show a live run.
3. **DualQueue** — `is_closed()` follows `persistence_queue.is_closed()` (or True only after `DualQueue.close()`). `send()` still writes persistence; skip/no-op streaming if that half is closed. Do not change plain `ThreadedGenerator` disconnect for non-workflow chats.
4. Leave `_handle_client_disconnect` as-is. Leave `ThoughtConsumer` skip as-is.

---

## 3. Documentation Findings

### Guides and Architecture Docs

`.ai-run/guides/` exists (37 files). Relevant but **none** document DualQueue hydrate or disconnect semantics:

- `.ai-run/guides/workflows/langgraph-workflows.md` — extend `WorkflowExecutor` / nodes
- `.ai-run/guides/architecture/layered-architecture.md` — hydrate belongs in service, not routers
- `.ai-run/guides/architecture/service-layer-patterns.md` — orchestration in services
- `.ai-run/guides/api/endpoint-conventions.md` — typed response fields (`execution_status` on `GeneratedMessage`)
- `.ai-run/guides/testing/testing-patterns.md` and `testing-service-patterns.md` — mirror paths; mock data-access boundaries
- `.ai-run/guides/standards/git-workflow.md` — `EPMCDME-####` in branch/commit
- `.ai-run/guides/quality-gates.md` — `make ruff` and related gates

Authoritative domain write-up: **`docs/fix/backend-fix.md`** (validated 2026-08-14). Process brief: **`docs/fix/01-backend-sdlc-light.md`**. No ADRs. CHANGELOG has no EPMCDME-14075 entry. Product `docs/workflows/*.md` do not cover DualQueue or hydrate.

### Architectural Decisions

- DualQueue **intent** (class docstring): persistence continues if streaming is lost.
- DualQueue **implementation**: `is_closed()` = streaming closed. Tests currently encode that as correct (`test_is_closed_reflects_streaming_queue`, `test_client_disconnect_scenario`).
- Stub history + materialize-on-GET is the chat persistence model.
- Token-level in-progress thoughts are intentionally not persisted (`ThoughtConsumer`).
- Producer / assistant / ThoughtConsumer thread pools are separated (`utils.py`) to avoid starvation — unrelated to this bug.

### Derived Conventions

- Do not add a new conversation endpoint; extend existing GET hydrate.
- Do not persist intra-step tokens for this ticket.
- DualQueue is workflow-stream-only; keep assistant-chat `ThreadedGenerator` stop-on-disconnect.
- Frontend contract belongs in handoff: `executionId`, `executionStatus`, thought `in_progress` (confirm snake vs camel on nested `Thought`).

---

## 4. Testing Landscape

### Existing Coverage

- **`tests/codemie/service/test_history_materializer.py`** — SUCCEEDED materialization, missing execution, exception fallback, tokens, mixed history. `_get_execution_thoughts` is **always patched to `[]`**. `_mock_execution` defaults to SUCCEEDED. **No IN_PROGRESS case. No assertion on thought `in_progress`. No `execution_status`.**
- **`tests/codemie/core/test_dual_queue.py`** — dual send/close, `is_closed` tracks **streaming**, disconnect scenario continues via **`persistence_queue.send`** (not `dual_queue.send` after stream close). Encodes current (wrong for this ticket) semantics.
- **`tests/codemie/service/workflow_execution/test_thought_consumer.py`** — `test_consume_skips_in_progress_thoughts` (keep; out of scope to change).
- **`tests/codemie/agents/test_assistant_agent/test_agent_streaming.py`** — AIToolsAgent stops when generator closed. **Zero tests** for `LangGraphAgent._should_stop_streaming`.
- Conversation GET tests cover auth/metadata, not workflow_execution_ref hydrate.
- `_handle_client_disconnect` is **untested**. `start_state` tests do not assert `IN_PROGRESS` / `output=None`.
- Details states tests do not assert IN_PROGRESS payload parity with chat hydrate.

### Testing Framework and Patterns

- pytest (+ pytest-asyncio, pytest-mock). Tests under `tests/codemie/` mirroring `src/`.
- Materializer: patch `WorkflowService`; helper builders `_plain_message` / `_execution_ref` / `_mock_execution`.
- DualQueue: real `ThreadedGenerator` + `ThoughtQueue`; JSON `StreamedGenerationResult`.
- Guide: mock provider/repo boundaries; do not mock the unit under test (`_get_execution_thoughts` must be exercised for new cases).

### Coverage Gaps

New tests required (TDD, as named in `backend-fix.md`):

1. IN_PROGRESS state with `output=None` included, `in_progress=True`.
2. Completed state with output still included, `in_progress=False`.
3. Aborted with empty output still included, `aborted=True`.
4. Materialized message carries `execution_status` from `overall_status`.
5. `history_index` filter still scopes thoughts to the current turn.
6. Streaming-only close does **not** make `DualQueue.is_closed()` true.
7. `send()` after streaming close still reaches persistence.
8. `DualQueue.close()` still closes both and `is_closed()` is true.

Optional but valuable: FAILED-without-output included; `input_text` from `state.task`; `dual_queue.send` after stream close does not require bypassing DualQueue.

---

## 5. Configuration and Environment

### Environment Variables

None control DualQueue disconnect or hydrate. Adjacent only:

- `WORKFLOW_MAX_CONCURRENCY` / `WORKFLOW_DEFAULT_CONCURRENCY`
- `THREAD_POOL_MAX_WORKERS` / `ASSISTANT_THREAD_POOL_MAX_WORKERS` (stream producer + ThoughtConsumer pools in `utils.py`)
- `WORKFLOW_EXECUTION_INDEX` / `WORKFLOW_EXECUTION_STATE_INDEX` / `WORKFLOW_EXECUTION_STATE_THOUGHTS_INDEX`
- `ENABLE_LANGGRAPH_AITOOLS_AGENT` (default True) — both agent classes stop on `is_closed()`

### Configuration Files

- `src/codemie/configs/config.py` — central settings; DualQueue/hydrate unused.
- `.env.example`, `tests/.env.test` — no DualQueue/hydrate vars.
- `deploy-templates/values.yaml` — ingress `proxy-read-timeout: "600"` for long NDJSON streams (orthogonal to browser reload).

### Feature Flags and Deployment Concerns

**No feature flag** for this fix; behavior change is global for workflow streaming DualQueue. Secrets/Vault are encryption-only. No migration. Stream responses already set `Cache-Control: no-cache` and `X-Accel-Buffering: no`.

---

## 6. Risk Indicators

- **Materializer filter is the empty-UI root cause** — `_get_execution_thoughts` drops IN_PROGRESS/`output=None` and hardcodes `in_progress: False`. Existing materializer tests never exercise this function (always mocked).
- **Between-steps gap** — without `execution_status` on the turn, a still-running execution with no current IN_PROGRESS state hydrates as an empty/finished-looking assistant message (`message=""` from empty `execution.output`).
- **DualQueue contract vs implementation** — class docstring says persistence continues after stream loss; `is_closed()` follows streaming; agents treat that as user disconnect. Tests encode the broken semantics (`test_is_closed_reflects_streaming_queue`).
- **`DualQueue.send` after streaming close** still `queue.put`s on the closed `ThreadedGenerator` (no consumer). Fixing `is_closed` without skipping the streaming send can grow an unread queue while the workflow continues.
- **Disconnect can stop later steps** — if DualQueue is not fixed, FE polling of a corrected hydrate still shows a stuck/failed step. Both hydrate and DualQueue are required for the product outcome.
- **SSE rejoin is unnecessary and out of scope** — Details APIs already return in-progress states; do not add stream resume / last-event-id.
- **JSON naming mismatch risk for FE** — `GeneratedMessage` uses `ConfiguredModel`/`to_camel` (`executionStatus`, `executionId`, `workflowExecutionRef`). Nested `Thought` has no alias generator; `in_progress` / `author_name` may remain snake_case. Document actual GET JSON in handoff.
- **FAILED-without-output currently dropped** — same filter class as IN_PROGRESS; include alongside ABORTED to avoid a silent empty thought for failed steps.
- **`_should_stop_streaming` and `_handle_client_disconnect` have no tests** — DualQueue unit tests must carry the disconnect-vs-persist contract; agent tests only cover AIToolsAgent + plain generator.
- **ThoughtConsumer skip of `in_progress` thoughts** — accepted; intra-step token UI after reload will not appear. Do not expand scope to persist token chunks.
- **No config/flag rollback** — DualQueue `is_closed` change applies to all workflow stream create/resume paths. Scope is still DualQueue-only (not assistant chats).
- **Hedging cancellation** — `LangGraphAgent._should_stop_streaming` also treats `is_closed()` as stop for `FAST_PATH_WON`. DualQueue is not used for hedging; do not change `ThreadedGenerator` itself.
- **codegraph MCP unavailable** — research used filesystem Explore threads plus direct reads of the files named in `backend-fix.md`. Findings were re-checked against current source on 2026-08-14.
- **Backend cannot complete the product UI alone** — this run ships the GET contract; `codemie-ui` must consume `executionId` / `executionStatus` / thought `in_progress` in a sibling run.

---

## 7. Summary for Complexity Assessment

The task touches three architectural layers with a small, well-localized file surface: **service** (`history_materializer` thought filter and status copy), **API model** (`GeneratedMessage.execution_status`), and **core** (`DualQueue.is_closed` / `send`). Agents, disconnect handlers, persistence models, and Details APIs stay as-is. Likely **3 production files and 2 test files**; no migration, no new endpoint, no env/flag.

Technical novelty is low: this restores DualQueue’s documented contract and makes chat hydrate match data Details already returns. It does **not** introduce SSE rejoin, ThoughtConsumer token persistence, or a new polling API. Current source **confirms** `docs/fix/backend-fix.md`; prefer that approach over stream-rejoin.

Test coverage is mixed: SUCCEEDED hydrate and DualQueue dual-send exist, but IN_PROGRESS hydrate and “disconnect must not stop the workflow” are untested or tested as the opposite. TDD should add those cases first (mock `get_all_by_fields`, not `_get_execution_thoughts`; rewrite DualQueue `is_closed` tests). Key scoring risks: two coupled bugs (empty hydrate + agent stop on reload), JSON alias mismatch for nested thoughts, and the unread streaming-queue leak if `send()` is not gated after stream close.
