# Spec: Fix workflow thought-persistence regression (EPMCDME-14850)

## Problem

Commit `91a7f49fa` (EPMCDME-14083, Switchyard model routing) coupled two changes that together
break workflow thought persistence for the default (non-Switchyard-routed) code path:

1. `AgentStreamingCallback.on_llm_end` (`src/codemie/agents/callbacks/agent_streaming_callback.py:247`)
   now retains a finalized thought in `ThoughtInMemoryStorage` instead of always deleting it, so that
   a later call carrying resolved Switchyard routing metadata can still stamp it. When
   `model_resolved` is `False` — the default, since `SWITCHYARD_ENABLED=False` out of the box — the
   thought is never deleted on that call.
2. `LangGraphEventAdapter` (`src/codemie/agents/langgraph_event_adapter.py:178` and `:199`) now calls
   `_on_llm_end` for the same stop-turn `message.id` from both the `"messages"` and `"updates"` stream
   modes (non-supervisor path only). This is intentional — it's how late-arriving routing metadata
   gets a chance to reach the thought — but it means `on_llm_end` can run twice per turn.

Because `on_llm_end` sends the thought (`_send_thought`, line 244) *before* checking whether to
delete it, and the retained thought is still present in storage on the second call, the second call
re-sends the same thought id with `in_progress=False`. `ThoughtConsumer.consume()`
(`thought_consumer.py:69-79`) persists each such item via an insert-only `save()`
(`base.py:500-516`); the second insert of the same primary key raises `IntegrityError`, uncaught,
which silently kills the consumer thread (its `Future`, returned by `run_consumer_in_thread_pool`
in `rest_api/routers/utils.py:106`, is never observed) — so no further thoughts persist for that
execution.

## Approach

Fix at three layers, root cause plus defense in depth:

**Callback (`agent_streaming_callback.py`, `on_llm_end`) — root cause, callback-only.** The dual
adapter invocation is intentional (late Switchyard metadata) and is not being changed. The actual
defect is that the callback lost its own idempotency: previously, a redundant `on_llm_end` call
found no thought in storage and returned early (`update_thought` returns `None` when the id is
missing) — a natural no-op. Restore that guarantee without losing the Switchyard late-metadata
case: when `on_llm_end` is invoked for a run_id whose thought was already sent once with
`in_progress=False` (i.e. this is a second/redundant finalize), it must not re-send unless this
call carries routing metadata not already captured — and it must always leave the thought removed
from storage afterward, so a third call cannot repeat the problem. Calls that resolve the model or
return a plain string on their first invocation are unaffected. This is scoped to
`agent_streaming_callback.py`; `langgraph_event_adapter.py` is not touched — its supervisor path has
no analogous double-stop branch, so it needs no corresponding change, and this fix doesn't depend on
adapter behavior either way.

**Consumer (`thought_consumer.py`, `consume()`) — defensive backstop.** Regardless of how many
finalize events reach the queue, `consume()` must not crash on a duplicate `in_progress=False`
item for an id it has already persisted. Detect and gracefully skip (with a log line per
`.ai-run/guides/development/error-handling.md` conventions — contextual, not silent) rather than
letting a second insert raise. A save failure that is *not* a duplicate-id conflict (a real DB
error) must still propagate/log per existing behavior — this is a narrow guard, not a blanket
try/except around `save()`.

**Thread pool observability (`rest_api/routers/utils.py`, `run_consumer_in_thread_pool`) — visibility.**
Attach a done-callback to the submitted `Future` so any unhandled exception in the consumer thread —
this bug's or a future one — is logged with context instead of disappearing silently. This does not
change `consume()`'s behavior; it only makes failures observable.

## Acceptance Criteria

- A workflow LLM turn that never resolves a Switchyard-routed model (the default/common path)
  results in exactly one persisted `WorkflowExecutionStateThought` row for that turn, not zero and
  not an `IntegrityError`.
- A workflow LLM turn that *does* resolve Switchyard routing metadata on a later finalize call still
  gets that metadata stamped onto the persisted thought (no regression to the existing routed-model
  test).
- `ThoughtConsumer.consume()` receiving two `ThoughtQueueItem`s with the same id and
  `in_progress=False` does not raise and does not stop the consumer loop; subsequent items are still
  processed.
- A genuinely unexpected `save()` failure (not a duplicate-id conflict) still propagates/logs as an
  error, per existing convention.
- An exception raised inside `consume()` (of any kind) is now logged via the thread-pool `Future`'s
  done-callback rather than silently vanishing.
- `tests/codemie/service/workflow_execution/test_thought_consumer.py::test_error_handling_during_save`
  is narrowed to a genuinely unexpected save failure and continues to assert it propagates; a new,
  separate test covers the duplicate-id case asserting graceful handling.
- New tests: a callback-level test asserting a redundant `on_llm_end` call for an already-finalized
  run_id does not re-send duplicate content and removes the thought from
  `ThoughtInMemoryStorage`; a thread-pool-level test asserting a `consume()` exception is observed
  via the `Future`; an integration-style test (reusing the `real_thought_queue` fixture pattern from
  `tests/codemie/agents/callbacks/test_agent_streaming_callback.py`) wiring a real `ThoughtQueue` to
  a real `ThoughtConsumer`, simulating the duplicate-finalize scenario, and asserting exactly one
  persisted row with the consumer loop still alive afterward.

## Non-Goals

- No change to `LangGraphEventAdapter`'s dual stop-branch structure (`langgraph_event_adapter.py:178`,
  `:199`) — it is intentional and not the root cause; the supervisor-path asymmetry noted during
  investigation requires no corresponding fix since the adapter isn't being touched.
- No change to `BaseModelWithSQLSupport.save()`/`update()` semantics generally (`base.py:500-518`) —
  the consumer-layer fix is a narrow duplicate-id guard local to `ThoughtConsumer`, not a switch to
  upsert semantics for all SQL-backed models.
- No general retry/backoff or resilience framework for the consumer thread pool — the thread-pool
  change is limited to observing/logging the existing `Future`'s exception, not restarting failed
  consumers or adding retries.
- No attempt to identify or restore the specific originally-failing test(s) referenced by the
  ticket's "triggered_tools" terminology — it does not appear in this repository; regression coverage
  is the new tests listed above instead.
- No end-to-end test through a real `WorkflowExecutor` execution — the integration-style test targets
  the `ThoughtQueue` → `ThoughtConsumer` → persisted-row boundary directly, not full workflow
  orchestration.

## Open Risk (accepted)

If, in some execution, only a single stream-mode chunk ever arrives for a turn (per the adapter's
own comment, the `"messages"` stream doesn't always emit a dedicated stop chunk) and that one call
never resolves the model, the thought is sent once and retained without a second call ever arriving
to trigger cleanup. This is a bounded in-memory leak for the life of that callback instance, not a
crash or a persistence failure, and is not addressed by this fix — flagged for a follow-up if it
proves material in practice.
