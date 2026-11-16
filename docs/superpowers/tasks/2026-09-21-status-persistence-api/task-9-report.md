DONE_WITH_CONCERNS

# Task 9: Wire the guard into create_workflow / update_workflow

## What was implemented

- Added `_assert_all_assistants_selectable(workflow_config: WorkflowConfig) -> None` module-level
  helper to `src/codemie/rest_api/routers/workflow.py`, exactly as specified in the plan: iterates
  `workflow_config.assistants`, calls `llm_service.assert_model_selectable(wf_assistant.model)` for
  every assistant with a `model` set, and converts a caught `ModelNotSelectableError` into an
  `ExtendedHTTPException(code=400, message="Model not selectable", details=str(e))`.
- Added two imports: `ModelNotSelectableError` (added to the existing
  `from codemie.core.exceptions import (...)` block) and
  `from codemie.service.llm_service.llm_service import llm_service`.
- Wired `_assert_all_assistants_selectable(workflow_config)` into `create_workflow` right after
  `workflow_config = WorkflowConfig(**request.model_dump())` and before the autonomous-mode check —
  i.e. **outside** the `try:` block, per the plan's explicit instruction, so the "Model not
  selectable" message/details survive un-rewrapped.
- Wired `_assert_all_assistants_selectable(updated_config)` into `update_workflow` **inside** the
  first `try:` block, right after `updated_config.parse_execution_config()` — per the plan's
  explicit instruction.

## Files changed

- `src/codemie/rest_api/routers/workflow.py`
- `tests/codemie/rest_api/routers/test_workflow.py`

## Tests added (TDD, RED confirmed before implementation)

1. `test_create_workflow_rejects_unselectable_model` — POST with one assistant whose model is
   patched to raise `ModelNotSelectableError`; asserts 400, `error.message == "Model not
   selectable"`, and `"bad-model" in error.details`.
2. `test_create_workflow_with_selectable_model_succeeds` — POST with an assistant referencing an
   arbitrary, non-configured model (`"gpt-4o"`); `assert_model_selectable` is left unpatched
   (real no-op for unknown base_name); asserts 200.
3. `test_update_workflow_rejects_unselectable_model` — PUT whose yaml_config assistant model is
   patched to raise `ModelNotSelectableError`; asserts 400 and
   `error.message == "Workflow Configuration error"` (see self-review note below — it does NOT
   assert the model name appears in `details`, because it doesn't).
4. `test_update_workflow_with_selectable_model_succeeds` — PUT using the existing
   `update_workflow_request`/`test_yaml_config` fixture (assistant model
   `gpt-4o-2024-11-20`, unconfigured -> no-op); asserts 200 and the usual success message.

RED was confirmed for tests 1 and 3 before implementation: both failed with
`AttributeError: module 'codemie.rest_api.routers.workflow' has no attribute 'llm_service'` when
`patch("codemie.rest_api.routers.workflow.llm_service.assert_model_selectable")` tried to resolve
the (not-yet-imported) attribute — the expected "feature missing" failure mode. Tests 2 and 4
passed even before the change (guard didn't exist yet, so nothing could reject them) — this is
expected and consistent with the plan's "still succeeds" requirement; they now also protect against
a future regression turning the guard into a false positive for unconfigured/selectable models.

After implementing the helper and wiring, all 4 new tests pass (GREEN).

## Self-review concern (flagged per task instructions, not silently "fixed")

For `update_workflow`, the plan explicitly places `_assert_all_assistants_selectable(updated_config)`
**inside** the same `try:` block that wraps `parse_execution_config()`, whose `except Exception as e:`
re-raises a **new** `ExtendedHTTPException(message=WORKFLOW_CONFIGURATION_ERROR, details=str(e).strip(), ...)`.

I verified empirically that `ExtendedHTTPException.__init__` never calls `super().__init__(...)` with
any args, so `Exception.args == ()` and `str(e) == ''` for any `ExtendedHTTPException` instance:

```
$ PYTHONPATH=src poetry run python -c "
from codemie.core.exceptions import ExtendedHTTPException
e = ExtendedHTTPException(code=400, message='Model not selectable', details='bad-model is deprecated')
print(repr(str(e)))  # ''
print(e.args)        # ()
"
```

Consequence: when `_assert_all_assistants_selectable` raises inside that try block for
`update_workflow`, the surrounding generic `except Exception as e:` produces a 400 response with
`message == "Workflow Configuration error"` and `details == ""` (empty) — it does **not** name the
model, unlike `create_workflow`'s response. The `error_format == JSON` branch doesn't help either,
since `e.args` is an empty tuple, not a dict.

Per the task's explicit instruction ("if you find the resulting details truly empty/useless, ...
note it as a self-review concern in your report rather than silently changing the plan's specified
structure"), I implemented the wiring exactly as specified and adjusted the failing-test's
assertions to match the actually-observable (empty-details) behavior rather than asserting the
model name appears — asserting that would be asserting something false about the implemented
behavior. This is a real UX gap for `update_workflow` callers vs. `create_workflow` callers, worth a
follow-up decision (e.g., moving the check to its own try/except with a dedicated catch for
`ExtendedHTTPException` re-raise, mirroring `create_workflow`'s placement) — but that would deviate
from the plan's explicit call-site instruction, so it was left as specified.

## Validation commands actually run

```
cd /Users/Aliaksei_Hurynovich/developer/_codemie/codemie
poetry run pytest tests/codemie/rest_api/routers/test_workflow.py -o addopts="--import-mode=importlib" > /Users/Aliaksei_Hurynovich/Developer/_codemie/codemie/docs/superpowers/tasks/2026-09-21-status-persistence-api/.test.log 2>&1; echo "EXIT=$?"; tail -50 .../.test.log
```

Result: `EXIT=0`, `48 passed, 3 skipped, 4 warnings in 11.52s` (the 3 skipped tests are the
`WORKFLOW_GENERATION_ENABLED`-gated refine tests, pre-existing skip condition, unrelated to this
change). No pre-existing test broke.

## Commit

SHA: `8e92f3bee6fc42eb37eadb63bf0a71dde8f9a77f`

`git show --stat HEAD`:
```
 src/codemie/rest_api/routers/workflow.py        |  17 +++
 tests/codemie/rest_api/routers/test_workflow.py | 135 +++++++++++++++++++++++-
 2 files changed, 151 insertions(+), 1 deletion(-)
```
Confirmed only these two files are in the commit.

## Ownership

Only touched `src/codemie/rest_api/routers/workflow.py` and
`tests/codemie/rest_api/routers/test_workflow.py`, as scoped.
