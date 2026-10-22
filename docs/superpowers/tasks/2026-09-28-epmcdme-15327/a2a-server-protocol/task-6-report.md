DONE_WITH_CONCERNS

## Commit
6307fbf7c — "EPMCDME-15327: Rename A2A task manager methods and types to v1.0 names"

## Changed files (exact `git show --stat HEAD` list)
- src/codemie/rest_api/a2a/server/codemie_task_manager.py
- src/codemie/rest_api/a2a/server/task_manager.py
- tests/codemie/rest_api/a2a/server/test_codemie_task_manager.py

## Changes made
- `task_manager.py`: retyped `on_get_task`/`on_send_task` to `SendMessageRequest`/`SendMessageResponse`;
  renamed abstract method `on_send_task_subscribe` → `on_send_streaming_message` with new type names;
  added two new abstract methods `on_cancel_task` and `on_get_extended_agent_card` (both `pass` bodies),
  per the plan's explicit instruction.
- `codemie_task_manager.py`: swapped imports to canonical v1.0 names (`SendMessageRequest`,
  `SendMessageResponse`, `SendStreamingMessageRequest`, `SendStreamingMessageResponse`); retyped every
  annotation site (`on_send_task`, `_invoke_agent`, `_validate_request`, `_setup_agent`,
  `_process_agent_response`, `_process_error_response`, `_handle_a2a_stream`,
  `_create_streaming_response`); renamed `on_send_task_subscribe` → `on_send_streaming_message` (body
  unchanged: `raise new_not_implemented_error(request.id)`). Did NOT add `on_cancel_task` or
  `on_get_extended_agent_card` implementations, and did NOT touch `_create_response_parts` — both
  explicitly out of scope for this task (Task 7's job).
- `test_codemie_task_manager.py`: swapped imports to canonical names; updated the three request
  fixtures (`send_task_request`, `get_task_request`, `send_task_streaming_request`) to the new method
  literals (`SendMessage`, `GetTask`, `SendStreamingMessage`); replaced all `SendTaskResponse` class
  references in test bodies with `SendMessageResponse`. Left `test_on_send_task_subscribe` referencing
  the now-removed `on_send_task_subscribe` method name, as instructed (Task 8's job to replace).

## Test commands and results

1. `poetry run pytest tests/codemie/rest_api/a2a/server/test_codemie_task_manager.py -v`
   → EXIT=1 (pytest exit code 1: failures present)
   Result: 11 passed, 1 failed, 12 errors (24 total).

2. `poetry run pytest tests/codemie/rest_api/a2a/test_types.py tests/codemie/rest_api/a2a/test_utils.py -v`
   → EXIT=0
   Result: 19 passed (8 in test_types.py + 11 in test_utils.py) — Tasks 1-5's suite is fully unaffected.

## Concern — verification result differs from the task brief's own prediction

The task brief's VERIFICATION section asked me to confirm that *only* `test_on_send_task_subscribe`
fails and every other test passes. That did not happen: **13 of 24 tests fail** (1 `FAILED` +
12 `ERROR`), not 1.

Root cause (exactly as anticipated by the task's own FILE 2 instructions): `task_manager.py`'s
`TaskManager` ABC now declares `on_cancel_task` and `on_get_extended_agent_card` as abstract methods
(per FILE 1's explicit target content). `CodemieTaskManager` does not yet implement them (per FILE 2's
explicit instruction: "Do NOT add `on_cancel_task` or `on_get_extended_agent_card` yet... Do not add
stub implementations yourself; leave this task exactly as scoped"). This makes `CodemieTaskManager`
abstract and uninstantiable — exactly what FILE 2's own text predicts: *"CodemieTaskManager will become
abstract-and-uninstantiable until Task 7 adds them — this is EXPECTED."*

Every test that uses the `task_manager` fixture (which instantiates `CodemieTaskManager`) therefore
errors at fixture setup with:
`TypeError: Can't instantiate abstract class CodemieTaskManager without an implementation for abstract
methods 'on_cancel_task', 'on_get_extended_agent_card'`

Failing/erroring tests (13): `test_initialization` (FAILED), `test_on_send_task_validation_error`,
`test_on_send_task_success`, `test_on_get_task_not_found`, `test_on_get_task_found`,
`test_on_send_task_subscribe`, `test_invoke_agent_success`, `test_invoke_agent_exception`,
`test_setup_agent`, `test_process_agent_response_input_required`,
`test_process_agent_response_completed`, `test_process_error_response`, `test_create_response_parts`
(all ERROR at fixture setup).

Passing (11): `test_upsert_task_new_task`, `test_upsert_task_existing_task`,
`test_validate_request_incompatible_modes`, `test_validate_request_compatible_modes`,
`test_update_store_task_not_found`, `test_update_store_success`, `test_update_store_with_status_message`,
`test_append_task_history_with_limit`, `test_append_task_history_no_limit`,
`test_get_user_query_valid`, `test_get_user_query_invalid` — these use `CodemieTaskManager` only as a
static/class-method target (no instantiation via the `task_manager` fixture), so they are unaffected.

I did not add stub implementations for `on_cancel_task`/`on_get_extended_agent_card` and did not
change scope beyond the mechanical renames, per the explicit instruction in FILE 2 to leave this task
exactly as scoped and let Task 7 resolve the abstract-class gap immediately after. This is a genuine
internal inconsistency between the task brief's VERIFICATION section (predicting 1 failure) and its
own FILE 2 rationale (predicting the abstract-uninstantiable state) — I followed FILE 2's explicit
instruction since it is more specific and directly governs the file I was editing.

No unexpected failures beyond this documented, instruction-driven consequence. All 13 failing tests
trace to the single abstract-class root cause, not to any error on my part in the renames themselves —
confirmed by the 11 passing tests exercising the same renamed types/classes without issue, and by the
fully green Tasks 1-5 suite.
