DONE

## Commit
fa8044458 — EPMCDME-15327: Rename A2A JSON-RPC methods to v1.0 names and add GetExtendedAgentCard

## Changed files (git show --stat HEAD)
- src/codemie/rest_api/a2a/types.py | 39 ++++++++++++++++++++++++--------
- tests/codemie/rest_api/a2a/test_types.py | 26 +++++++++++++++++++--
2 files changed, 53 insertions(+), 12 deletions(-)

## Changes applied
1. `SendTaskRequest` -> `SendMessageRequest`, method literal `"SendMessage"`.
2. `SendTaskResponse` -> `SendMessageResponse` (body unchanged).
3. `SendTaskStreamingRequest` -> `SendStreamingMessageRequest`, method literal now ONLY `"SendStreamingMessage"` (dropped `"message/stream"` alt value).
4. `SendTaskStreamingResponse` -> `SendStreamingMessageResponse` (body unchanged).
5. `GetTaskRequest`: class name kept, method literal changed to `"GetTask"`.
6. `CancelTaskRequest`: class name kept, method literal changed to `"CancelTask"`.
7. `GetTaskResponse`, `CancelTaskResponse`, `SetTaskPushNotificationRequest/Response`, `GetTaskPushNotificationRequest/Response` untouched.
8. Added `GetExtendedAgentCardRequest` next to the other request classes; added `GetExtendedAgentCardResponse` immediately after the `AgentCard` class body (plain `AgentCard | None`, no forward-ref string, no `model_rebuild()`).
9. Rebuilt `A2ARequest` TypeAdapter union with exactly: `SendMessageRequest, SendStreamingMessageRequest, GetTaskRequest, CancelTaskRequest, GetExtendedAgentCardRequest`, discriminated on `method`.
10. Added backward-compatible aliases directly below the union: `SendTaskRequest = SendMessageRequest`, `SendTaskResponse = SendMessageResponse`, `SendTaskStreamingRequest = SendStreamingMessageRequest`, `SendTaskStreamingResponse = SendStreamingMessageResponse`.

## Tests (TDD)
- Appended `test_a2a_request_resolves_send_message` and `test_a2a_request_rejects_list_tasks` to `tests/codemie/rest_api/a2a/test_types.py`, merging `pytest`/`ValidationError`/new type imports into the existing import block.
- RED confirmed first: `ImportError: cannot import name 'SendMessageRequest'` (expected — class didn't exist yet).
- After implementation, GREEN confirmed.

## Test command and result
`poetry run pytest tests/codemie/rest_api/a2a/test_types.py -v` -> EXIT=0, 4 passed (Task 1's original 2 tests + this task's 2 new tests).

## Scope confirmation
Did NOT touch `codemie_task_manager.py`, `task_manager.py`, `utils.py`, or `routers/a2a.py` — confirmed via `git show --stat HEAD` listing only the two files above. Did not run `tests/codemie/rest_api/a2a/server/test_codemie_task_manager.py` or `tests/codemie/rest_api/a2a/test_utils.py` (out of scope for this task per instructions).

## Concerns
None. `.ai-run/sdlc-factory/doctor.json` and `.codemie/codemie-cli.config.json` show as modified in `git status` but were pre-existing working-tree changes unrelated to this task and were not staged or committed.
