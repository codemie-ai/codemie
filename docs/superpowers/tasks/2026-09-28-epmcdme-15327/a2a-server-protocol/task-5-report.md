DONE

## Task 5 of 11 — A2A conversion layer (`utils.py`) updated for v1.0 wire shape

Commit SHA: `efa59b96c`

### Changed files (exactly these two)
- `src/codemie/rest_api/a2a/utils.py`
- `tests/codemie/rest_api/a2a/test_utils.py`

### Changes made

1. **Imports** (`utils.py`): replaced `SendTaskRequest, SendTaskStreamingRequest` with canonical
   `SendMessageRequest, SendStreamingMessageRequest`; added `FilePart, DataPart, AgentInterface`.
2. **`convert_to_task_request`**: return-type annotation and `request_cls` selection now use
   `SendMessageRequest`/`SendStreamingMessageRequest`.
3. **`convert_messages_to_chat_messages`**: replaced `part.type == "text"/"file"/"data"` chain with
   `isinstance(part, TextPart)/FilePart/DataPart` checks (branch bodies unchanged).
4. **`assistant_to_agent_card`**: replaced `url=f"..."` kwarg with
   `supportedInterfaces=[AgentInterface(url=f"...", transport="JSONRPC")]`; changed
   `AgentCapabilities(streaming=False, ...)` to `AgentCapabilities(streaming=True, pushNotifications=False, stateTransitionHistory=True, extendedAgentCard=True)`.
5. Test file: added `test_convert_messages_handles_file_part_without_type_field` and
   `test_convert_messages_handles_data_part_without_type_field`; updated
   `test_convert_to_task_request_non_streaming`/`_streaming` (isinstance + method literal
   `"SendMessage"`/`"SendStreamingMessage"`); updated `test_assistant_to_agent_card` for the new
   `supportedInterfaces`/`AgentInterface`/`extendedAgentCard` shape and `streaming=True`.

Nothing else in `utils.py` was touched (`convert_to_base_model_response`, `to_kebab_case`,
`tool_to_agent_skill`, `tools_from_toolkits_to_agent_skills`, `_get_agent_authentication`,
`get_auth_header`, and the `_*_auth` helpers are unchanged).

### TDD evidence

RED (pre-edit `utils.py`, ran `poetry run pytest tests/codemie/rest_api/a2a/test_utils.py -v`):
EXIT=1, 4 failed / 7 passed — failures were `AttributeError: 'TextPart' object has no attribute
'type'` (new FilePart/DataPart tests and the pre-existing `test_convert_messages_to_chat_messages`,
all hitting the same removed-`type`-field production code) and the old `url=`/`streaming=False`
assertions in `test_assistant_to_agent_card`. All for the expected reasons.

GREEN (post-edit): `poetry run pytest tests/codemie/rest_api/a2a/test_utils.py -v` — **EXIT=0**,
11 passed.

Regression check: `poetry run pytest tests/codemie/rest_api/a2a/test_types.py -v` — **EXIT=0**,
8 passed (untouched, confirms Tasks 1-4 still intact).

### Verification
- `git show --stat HEAD` lists exactly `src/codemie/rest_api/a2a/utils.py` and
  `tests/codemie/rest_api/a2a/test_utils.py`.
- Commit made cleanly, no pre-commit hook modified files, no amend needed.

### Concerns
None. `codemie_task_manager.py`, `task_manager.py`, and `routers/a2a.py` remain untouched and still
broken by earlier tasks' renames, per scope — owned by later tasks in this plan.
