DONE_WITH_CONCERNS

## Commit
6b034b211

## Changed files (git show --stat HEAD)
- src/codemie/rest_api/a2a/types.py (5 insertions, 4 deletions... actually: 2 files changed, 26 insertions(+), 4 deletions(-))
- src/codemie/rest_api/a2a/types.py
- tests/codemie/rest_api/a2a/test_types.py

Exact stat output:
```
 src/codemie/rest_api/a2a/types.py        |  5 +----
 tests/codemie/rest_api/a2a/test_types.py | 25 +++++++++++++++++++++++++
 2 files changed, 26 insertions(+), 4 deletions(-)
```

## Test commands run

1. RED check (before Step 2 edit):
   `poetry run pytest tests/codemie/rest_api/a2a/test_types.py -v` → EXIT=1
   Both new tests failed for the expected reasons:
   - `test_text_part_has_no_type_field`: `TextPart(text="hi").model_dump()` still contained `"type": "text"`.
   - `test_part_union_validates_without_discriminator`: `pydantic_core._pydantic_core.ValidationError: ... Unable to extract tag using discriminator 'type' [type=union_tag_not_found, input_value={'text': 'hi'}, ...]`.

2. GREEN check (after Step 2 edit), the required verification command:
   `poetry run pytest tests/codemie/rest_api/a2a/test_types.py -v > docs/superpowers/tasks/2026-09-29-a2a-server-protocol/test.local.log 2>&1; echo "EXIT=$?"; tail -25 docs/superpowers/tasks/2026-09-29-a2a-server-protocol/test.local.log`
   → EXIT=0, both tests PASSED:
   - `test_text_part_has_no_type_field` PASSED
   - `test_part_union_validates_without_discriminator` PASSED

3. Adjacent-file check (not my scope to fix, informational only):
   `poetry run pytest tests/codemie/rest_api/a2a/test_utils.py tests/codemie/rest_api/a2a/server/test_codemie_task_manager.py -v` → EXIT=1 overall (1 failed, 32 passed)
   - All 22 tests in `tests/codemie/rest_api/a2a/server/test_codemie_task_manager.py` PASSED.
   - All tests in `tests/codemie/rest_api/a2a/test_utils.py` PASSED except one:
     `test_convert_messages_to_chat_messages` FAILED with `AttributeError: 'TextPart' object has no attribute 'type'`, raised from `src/codemie/rest_api/a2a/utils.py:111` (`if part.type == "text":`).

## Concerns

`src/codemie/rest_api/a2a/utils.py` reads `part.type` in `convert_messages_to_chat_messages` (line 111) and elsewhere it likely branches similarly for `FilePart`/`DataPart`. Removing the `type` field from the three Part classes broke this consumer immediately, exactly as expected given the plan's bottom-up sequencing (types.py first, consumers later). This is explicitly out of scope for Task 1 per the task instructions ("utils.py ... NOT part of this task... left untouched") and is owned by a later task in this same plan. Flagging it here so the task that migrates `utils.py` knows to replace `part.type == "text"` style branching (likely with `isinstance(part, TextPart)` etc.) as part of its own TDD cycle.

No other concerns. `FileContent`, `Message`, `TaskStatus`, `Artifact`, and all other classes in `types.py` were left untouched. The `Annotated`/`Field` imports remain in the file since they are still used elsewhere (e.g. `A2ARequest` TypeAdapter, `JSONRPCMessage.id`, etc).
