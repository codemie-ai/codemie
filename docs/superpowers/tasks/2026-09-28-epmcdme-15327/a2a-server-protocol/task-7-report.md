DONE

## Task 7 of 11 — CodemieTaskManager: real parts, cancel, extended card

**Commit SHA:** aed68d35c
**Branch:** EPMCDME-15327_upgrade-a2a-protocol

### Changed files (from `git show --stat HEAD`)
```
 .../rest_api/a2a/server/codemie_task_manager.py    | 34 +++++++++++++--
 .../a2a/server/test_codemie_task_manager.py        | 49 +++++++++++++++++++---
 2 files changed, 74 insertions(+), 9 deletions(-)
```
Full paths:
- `src/codemie/rest_api/a2a/server/codemie_task_manager.py`
- `tests/codemie/rest_api/a2a/server/test_codemie_task_manager.py`

### What changed
- `_create_response_parts` now returns `list[TextPart]` (real `TextPart` objects) instead of raw `{"type": "text", ...}` dicts.
- Added `on_cancel_task(request: CancelTaskRequest) -> CancelTaskResponse`: looks up the task via `Task.find_by_id`, returns `TaskNotFoundError` if missing, `TaskNotCancelableError` if the task is already `COMPLETED`/`FAILED`/`CANCELED`, otherwise transitions it to `CANCELED` via `self.update_store(task_id=..., status=..., artifacts=[])` (keyword args) and returns the updated task.
- Added `on_get_extended_agent_card(request: GetExtendedAgentCardRequest, http_request) -> GetExtendedAgentCardResponse`: builds the card via `assistant_to_agent_card(self.assistant, http_request)`.
- Extended the `codemie.rest_api.a2a.types` import with `TaskIdParams`, `TaskNotCancelableError`, `CancelTaskRequest`, `CancelTaskResponse`, `GetExtendedAgentCardRequest`, `GetExtendedAgentCardResponse`.
- Extended the existing `codemie.rest_api.a2a.utils` import line with `assistant_to_agent_card` (no duplicate import line added).
- Left `_handle_a2a_stream`, `_create_streaming_response`, and `on_send_streaming_message` untouched (Task 8's scope) — they still `raise new_not_implemented_error(request.id)`, and `_create_streaming_response`/`_handle_a2a_stream` now benefit from `_create_response_parts` returning real `TextPart` objects.

### Test file changes
- Updated import block: added `TaskIdParams`, `TaskNotCancelableError`, `CancelTaskRequest` alongside existing `TaskNotFoundError` etc.
- `test_create_response_parts`: now asserts `parts == [TextPart(text=content)]`.
- `test_process_agent_response_input_required`, `test_process_agent_response_completed`, `test_process_error_response`: mocked `_create_response_parts` return values changed from `[{"type": "text", "text": "..."}]` to `[TextPart(text="...")]`.
- Added 4 new tests: `test_create_response_parts_returns_text_part`, `test_on_cancel_task_transitions_working_to_canceled`, `test_on_cancel_task_rejects_terminal_task`, `test_on_cancel_task_unknown_id_returns_not_found`.

### TDD verification (RED then GREEN)
**RED** (before implementation) — `poetry run pytest tests/codemie/rest_api/a2a/server/test_codemie_task_manager.py -v` — `EXIT=1`
- All 4 new tests errored with `TypeError: Can't instantiate abstract class CodemieTaskManager without an implementation for abstract methods 'on_cancel_task', 'on_get_extended_agent_card'` (expected — correct failure reason).
- Result line: `1 failed, 11 passed, 4 warnings, 16 errors in 126.01s` — matches the inherited Task 6 starting state (abstract class, cannot instantiate `task_manager` fixture).

**GREEN** (after implementation) — command:
```
poetry run pytest tests/codemie/rest_api/a2a/server/test_codemie_task_manager.py -v
```
`EXIT=1` (pytest's non-zero exit reflects the one expected failing test below — this is the expected outcome per task instructions, not a regression)
- Result line: `1 failed, 27 passed, 4 warnings in 121.30s`
- **Exactly one test failed:** `TestCodemieTaskManager::test_on_send_task_subscribe` — `AttributeError: <class 'codemie.rest_api.a2a.server.codemie_task_manager.CodemieTaskManager'> does not have the attribute 'on_send_task_subscribe'`. This is the expected, pre-existing failure called out in the task brief (Task 8's job — the method was renamed away by Task 6 and `on_send_task_subscribe` no longer exists on the class).
- **No other test failed or errored.** All 27 other tests (including the 4 new tests, the 3 updated-mock tests, and every pre-existing test) passed.

### Tasks 1-5 regression check
```
poetry run pytest tests/codemie/rest_api/a2a/test_types.py tests/codemie/rest_api/a2a/test_utils.py -v
```
`EXIT=0` — `19 passed, 2 warnings in 27.40s`. No regressions from Tasks 1-5.

### Confirmation of exactly which test(s) failed
Only `tests/codemie/rest_api/a2a/server/test_codemie_task_manager.py::TestCodemieTaskManager::test_on_send_task_subscribe` failed, for the expected reason (references a method name removed by Task 6, to be addressed in Task 8). No other test in either test file failed or errored.

### Notes
- `git show --stat HEAD` confirms exactly the two intended files were committed (`src/codemie/rest_api/a2a/server/codemie_task_manager.py`, `tests/codemie/rest_api/a2a/server/test_codemie_task_manager.py`); no pre-commit hook modified any files, so no re-commit was needed.
- Pre-existing unrelated working-tree modifications (`.ai-run/sdlc-factory/doctor.json`, `.codemie/codemie-cli.config.json`) were present before this task started and were deliberately left untouched/unstaged.
