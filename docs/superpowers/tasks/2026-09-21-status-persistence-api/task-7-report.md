DONE

## What was implemented

Wired `llm_service.assert_model_selectable` into `create_assistant` and `update_assistant` in
`src/codemie/rest_api/routers/assistant.py`:

- Added imports: `ModelNotSelectableError` added to the existing
  `from codemie.core.exceptions import ExtendedHTTPException, MCPAuthenticationRequiredException` line,
  and a new `from codemie.service.llm_service.llm_service import llm_service` line placed alongside the
  other `codemie.service.*` imports.
- `create_assistant`: added `llm_service.assert_model_selectable(assistant.llm_model_type)` as the first
  statement inside the existing `try` block (before `assistant.save(refresh=True)`). Added a dedicated
  `except ModelNotSelectableError as e:` clause, placed before `except IntegrityError` and
  `except Exception`, which tracks the failure metric and raises
  `ExtendedHTTPException(code=400, message="Model not selectable", details=str(e))`.
- `update_assistant`: added, immediately after `repository.update(assistant, request, user)`:
  ```python
  if "llm_model_type" in request.model_fields_set and assistant.llm_model_type:
      llm_service.assert_model_selectable(assistant.llm_model_type)
  ```
  and a dedicated `except ModelNotSelectableError as e:` clause before the existing
  `except Exception as e:`, mirroring the same metric-tracking + `ExtendedHTTPException(code=400, ...)` shape.
- `ask_virtual_assistant` was not touched (out of scope, per the plan — its `Assistant.model_construct(...)`
  is never persisted).

## Exception-handling structure resolution

`_raise_assistant_operation_error` (used by the generic `except Exception` branches in both routes) always
raises `ExtendedHTTPException(code=400, message=f"Failed to {operation} assistant", details=f"An error
occurred while trying to {operation} the assistant: {error_message}", ...)`. That message/details shape
does not match the plan's required externally-observable behavior (`message="Model not selectable"`,
`details=str(e)`). I therefore added a specific `except ModelNotSelectableError` clause before the generic
handler in **both** `create_assistant` and `update_assistant` — option (a) from the task brief, consistent
with the existing `IntegrityError`-before-`Exception` pattern already present in `create_assistant`. This
keeps the 400/"Model not selectable" semantics exact and independent of `_raise_assistant_operation_error`'s
generic wrapping.

## Tests added

`tests/codemie/rest_api/routers/test_assistant.py`:
- `TestCreateAssistantModelSelectability` — unselectable model raises `ExtendedHTTPException` (code 400,
  `"bad-model"` in `.details`); selectable model still returns the normal `AssistantCreateResponse`.
- `TestUpdateAssistantModelSelectability` — unselectable model raises the same 400; selectable model still
  returns the normal `AssistantUpdateResponse`; and a request that never touches `llm_model_type` (simulated
  by discarding `"llm_model_type"` from `request.__pydantic_fields_set__` after construction, since
  `AssistantRequest` requires `llm_model_type` at validation time when `type` is Codemie, so it can't be
  omitted outright) skips the guard entirely (`assert_model_selectable` not called).

Confirmed RED first: ran the new tests before the implementation change — all 5 failed for the expected
reasons (missing `llm_service` patch target / guard not yet wired / assertion not satisfied), not on
unrelated errors. Then implemented the guard and confirmed GREEN.

## Test command and result

```
poetry run pytest tests/codemie/rest_api/routers/test_assistant.py -o addopts="--import-mode=importlib"
```
EXIT=0 — `34 passed, 4 warnings` (all pre-existing tests in the file still pass; no regressions).

## Commit

SHA `34929ca8d34f3c03ebfeda8316becb6beb74f5d6` on branch `EPMCDME-15077_model-management_BE`:
`EPMCDME-15080: Guard assistant create/update against unselectable models`

`git show --stat HEAD` confirms only:
- `src/codemie/rest_api/routers/assistant.py`
- `tests/codemie/rest_api/routers/test_assistant.py`

## Self-review notes

- Pre-existing dirty files (`config/customer/customer-config.yaml`, `.ai-run/sdlc-factory/doctor.json`,
  `.codemie-repos/`) were verified via `git status` and deliberately left unstaged/untracked — not part of
  this commit.
- Did not touch any file from Tasks 1-6, and did not touch `ask_virtual_assistant`.
- `update_assistant`'s guard checks `assistant.llm_model_type` (the persisted, post-`repository.update()`
  value) rather than `request.llm_model_type`, per the plan's instruction — this means the guard fires using
  whatever value ends up on the assistant record after the repository update is applied.
