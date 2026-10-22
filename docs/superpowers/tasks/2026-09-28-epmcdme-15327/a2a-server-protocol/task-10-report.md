DONE_WITH_CONCERNS

## Commit
1fee06155 — "EPMCDME-15327: Wire A2A router dispatch table for SendMessage/GetTask/CancelTask/GetExtendedAgentCard"

## Changed files (exactly these two, confirmed via `git show --stat HEAD`)
- src/codemie/rest_api/routers/a2a.py
- tests/codemie/rest_api/routers/test_a2a_router.py

## What changed in the router
- Imports: `SendTaskRequest`/`SendTaskStreamingRequest` and the now-unused `GetTaskRequest`
  removed; added `MethodNotFoundError`. Ended up NOT importing `SendMessageRequest`,
  `CancelTaskRequest`, `GetExtendedAgentCardRequest` — the dispatch table keys on
  `json_rpc_request.method` (a string), so no isinstance/type-annotation use of those classes
  remained in the file. Importing them anyway would have been an unused import and failed
  `ruff check` (confirmed: `ruff check` passes clean on both changed files as committed).
- `execute_a2a_request` gained a `request: Request` parameter (before the `Depends` param, per
  FastAPI's default-arg ordering rule) so the `GetExtendedAgentCard` handler can build the
  agent card from the live HTTP request.
- Replaced the `isinstance` chain with a `dispatch` dict (lowercase — `ruff` N806 flags
  uppercase locals) mapping `"SendMessage"/"GetTask"/"CancelTask"/"GetExtendedAgentCard"` to
  lambdas calling the corresponding `CodemieTaskManager` method. Unmapped methods (i.e.
  `"SendStreamingMessage"`, the only other member of the `A2ARequest` discriminated union)
  fall through to `MethodNotFoundError` — the known, temporary gap Task 11 closes.

## Test commands run and results

1. `poetry run pytest tests/codemie/rest_api/routers/test_a2a_router.py -v -n 0`
   EXIT=0 — 11 passed (6 pre-existing Task-9 tests + 5 new).
2. `poetry run pytest tests/codemie/rest_api/a2a/test_types.py tests/codemie/rest_api/a2a/test_utils.py tests/codemie/rest_api/a2a/server/test_codemie_task_manager.py -v -n 0`
   EXIT=0 — 48 passed, unaffected by this change.
3. `poetry run ruff check src/codemie/rest_api/routers/a2a.py tests/codemie/rest_api/routers/test_a2a_router.py`
   "All checks passed!"

All commands above were run via the PowerShell tool with `-n 0` (see Concerns below for why).

## Concerns / environment findings (read before re-running these tests)

**1. `pytest-xdist` (`-n 2` from `pytest.ini` addopts) hangs/crashes in this Bash-tool shell.**
Running `poetry run pytest tests/codemie/rest_api/routers/test_a2a_router.py -v` (default
addopts, i.e. `-n 2`) via the Bash tool either hung indefinitely past a 120s timeout at
"collecting ..." or printed `OSError: [Errno 22] Invalid argument` from an xdist worker's
`exec(eval(sys.stdin.readline()))` bootstrap and then hung waiting on the broken worker. This
reproduced identically on the pre-existing (pre-my-change) test file via `git stash`, so it is
NOT caused by this task's changes — it is a Bash-tool/xdist/stdin interaction on this Windows
host. Switching to the **PowerShell tool** with `-n 0` (disables parallel workers) ran cleanly
in ~90-110s. Recommend future work on this file use PowerShell + `-n 0`, or investigate the
xdist/stdin issue separately.

**2. `Task` (`codemie.rest_api.a2a.types.Task`) is Postgres-backed with no test DB in this
environment.** `pytest.ini` sets `PG_URL=postgresql://pg:pg123@localhost:111/postgres`, and
nothing is listening there in this sandbox — a call through `Task.find_by_id` (or
`.save()`/`.update()`) hangs attempting a TCP connect rather than failing fast. Per the task's
own guidance, `Task.find_by_id` is patched via `patch.object(Task, "find_by_id", ...)` in the
three tests that touch it (`test_send_message_response_uses_task_id_alias`,
`test_get_task_unknown_id_returns_200_with_task_not_found`, `test_cancel_task_success`), matching
the exact pattern already used throughout `test_codemie_task_manager.py`. For the SendMessage
test, `Task.update` is also patched (class-level) since `on_send_task`'s full path calls
`update_store` — and thus `task.update(refresh=True)` — twice more after `upsert_task`.

**3. `@patch("codemie.rest_api.routers.a2a.authenticate")` does not work for
`execute_a2a_request` — this is a real gap in the task's provided test skeletons, not
something I introduced.** `execute_a2a_request` authenticates via
`user: User = Depends(authenticate)`; FastAPI captures the `authenticate` function object by
reference into the route's `Dependant` tree at router-registration/import time. Patching the
module attribute afterwards has no effect on requests through that route. (It does work for
`get_assistant_agent_card`, which calls `await authenticate(...)` directly in its body — that's
why the 6 pre-existing Task-9 tests, all against the GET endpoint, passed with this pattern.)
Confirmed by first running the task's literal test skeletons unmodified: all 5 new POST-endpoint
tests failed with 401 "Missing user-id header for local authentication" because the mock never
took effect. Fixed by switching to `app.dependency_overrides[a2a.authenticate] = lambda: mock_user`
inside a `try/finally` that resets `app.dependency_overrides = {}` — the existing convention in
this repo for exactly this situation (see `tests/codemie/rest_api/routers/test_ai_kata.py`,
e.g. lines 166/180).

**4. Two of the five provided test bodies needed adjustment to match actual JSON-RPC parsing
behavior; both are logic corrections, not workarounds:**
   - `test_get_extended_agent_card_returns_card`: `"params": {}` fails
     `GetExtendedAgentCardRequest` validation because `params: TaskIdParams | None = None` — an
     empty dict is not `None`, and `TaskIdParams.id` is required, so `{}` raises a
     `ValidationError` before the dispatch table is ever reached (surfaces as `InvalidRequestError`
     -32600 with no `"result"` key, matching the observed `KeyError: 'result'` on first run).
     Fixed by sending `"params": {"id": "req-1"}`.
   - `test_list_tasks_returns_method_not_found` (renamed
     `test_unmapped_method_returns_method_not_found`): `"method": "ListTasks"` is not a member
     of the `A2ARequest` discriminated union (`method` is the discriminator field across
     `SendMessageRequest`/`SendStreamingMessageRequest`/`GetTaskRequest`/`CancelTaskRequest`/
     `GetExtendedAgentCardRequest`), so `A2ARequest.validate_python(body)` itself raises
     `ValidationError` and the router's existing `except Exception` handler converts it to
     `InvalidRequestError` (-32600) — the dispatch table's `MethodNotFoundError` branch is
     never reached for a method string outside that union. This is pre-existing behavior in
     `types.py` (out of this task's scope) and is arguably correct JSON-RPC-wise for a
     genuinely unrecognized method. The dispatch table's `MethodNotFoundError` fallback is only
     reachable for `"SendStreamingMessage"` — the one method that IS a valid union member but is
     deliberately absent from this task's table (per the task's own explicit design note, closed
     by Task 11). Rewrote the test to send `"method": "SendStreamingMessage"` instead, which
     genuinely exercises the dispatch table's fallback branch and documents the gap in the test's
     own docstring.

None of the above required touching `codemie_task_manager.py`, `task_manager.py`, `utils.py`, or
`types.py` — all fixes are confined to the two committed files, within this task's stated scope.

Reported as `DONE_WITH_CONCERNS` (not `DONE`) because two of the five test bodies specified in
the task prompt could not be used as literally written (auth-mocking pattern and one test's
`method`/`params` values), and the local test environment needed a non-default runner
invocation (`-n 0`, PowerShell) to get a result at all rather than a hang. The router
implementation itself matches the task's spec exactly.
