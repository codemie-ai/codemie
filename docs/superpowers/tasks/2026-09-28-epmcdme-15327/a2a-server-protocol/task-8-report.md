DONE

## Commit
`a195226ca` — EPMCDME-15327: Implement real A2A server-side streaming

## Changed files
- `src/codemie/rest_api/a2a/server/codemie_task_manager.py`
- `tests/codemie/rest_api/a2a/server/test_codemie_task_manager.py`

## Changes made

1. `on_send_streaming_message` implemented as an async generator: looks up
   (or upserts) the `Task`, then delegates to `_handle_a2a_stream`, yielding
   each event.
2. `_create_streaming_response` fixed — it now builds a `TaskStatusUpdateEvent`
   (`id=task.id`, `status=TaskStatus(state=state)`, `final=<terminal-state-check>`)
   instead of wrapping a full `Task` via `_append_task_history`, which is what
   `SendStreamingMessageResponse.result` actually accepts
   (`TaskStatusUpdateEvent | TaskArtifactUpdateEvent | None`).
   **Deviation from the plan's literal sample**: the sample used
   `status=task.status`. Verified via RED that this fails under the test's
   mocking — `_handle_a2a_stream` calls `self.update_store(...)` without
   capturing its return value in most branches, so `task.status` stays stale
   (e.g. `SUBMITTED`) even as WORKING/COMPLETED/FAILED events are yielded.
   Building `TaskStatus(state=state)` from the `state` parameter passed into
   `_create_streaming_response` is what actually reflects the event being
   reported, and is what the test (and the real caller) expects.
3. `_handle_a2a_stream` left unchanged — its try/except/yield shape needed no
   restructuring, only the helper it calls needed fixing.
4. Removed the now-unused `new_not_implemented_error` import (ruff-clean).
5. Added `TaskStatusUpdateEvent` to the existing `codemie.rest_api.a2a.types`
   import block (no duplicate import line).
6. Confirmed no remaining `SendTaskStreamingResponse`/`SendTaskStreamingRequest`
   references in this file (none found).

### Test file
- Deleted the obsolete `test_on_send_task_subscribe` (patched a
  nonexistent `on_send_task_subscribe` attribute — pre-existing ERROR).
- Added `test_on_send_streaming_message_yields_working_then_completed` and
  `test_on_send_streaming_message_yields_failed_on_agent_error`, matching the
  file's existing `self, task_manager, ...` pytest-async style used by
  `test_on_cancel_task_transitions_working_to_canceled`.
- Removed now-unused `AsyncMock` and `UnsupportedOperationError` imports;
  added `TaskStatusUpdateEvent` import.

## RED verification (pre-fix)

Ran the two new tests against the pre-edit `on_send_streaming_message`
(`raise new_not_implemented_error(...)` inside a plain `async def`, which
makes it a coroutine function, not an async generator). Both failed with:

```
TypeError: 'async for' requires an object with __aiter__ method, got coroutine
```

This is the expected RED failure mode — iterating a coroutine object with
`async for` cannot work before the method becomes an async generator.

After making `on_send_streaming_message` an async generator (step 1 above)
but before fixing `_create_streaming_response` to build `TaskStatus(state=state)`
(i.e. still using `task.status`), both tests failed again, this time with a
real assertion mismatch (`SUBMITTED` instead of `WORKING`/`FAILED`) — this
confirmed the plan sample's `status=task.status` was the bug, prompting the
deviation described above.

## GREEN verification

```
poetry run pytest tests/codemie/rest_api/a2a/server/test_codemie_task_manager.py -v
```
EXIT=0 — **29 passed**, 0 failed. Every test in the file is green (no
expected failures remain).

```
poetry run pytest tests/codemie/rest_api/a2a/test_types.py tests/codemie/rest_api/a2a/test_utils.py -v
```
EXIT=0 — **19 passed** (Tasks 1-5 regression unaffected).

```
poetry run ruff check src/codemie/rest_api/a2a/server/codemie_task_manager.py tests/codemie/rest_api/a2a/server/test_codemie_task_manager.py
```
"All checks passed!"

`git show --stat HEAD` lists exactly:
- `src/codemie/rest_api/a2a/server/codemie_task_manager.py`
- `tests/codemie/rest_api/a2a/server/test_codemie_task_manager.py`

## Concerns

None blocking. Note for a later task: `_handle_a2a_stream`'s WORKING-phase
`self.update_store(...)` call result is discarded (not reassigned to
`task`), so the `task` object used to build the WORKING event's `id` comes
from before that update. This didn't matter for `id` (stable) and no longer
matters for `status` (now built from the `state` param), so it's inert
under the current implementation — flagging only in case a future task
relies on `task` reflecting post-update state.
