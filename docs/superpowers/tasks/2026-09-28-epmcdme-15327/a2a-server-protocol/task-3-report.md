DONE_WITH_CONCERNS

## Commit
7743d3960 — "EPMCDME-15327: Alias Task and event id fields as taskId on the wire"

## Changed files (exactly)
- src/codemie/rest_api/a2a/types.py
- src/codemie/rest_api/routers/a2a.py
- tests/codemie/rest_api/a2a/test_types.py

## Test command and result
```
poetry run pytest tests/codemie/rest_api/a2a/test_types.py -v
EXIT=0
7 passed (Task 1's 2 + Task 2's 2 + this task's 3)
```
RED confirmed first: the 3 new tests failed before the implementation change (log at
`docs/superpowers/tasks/2026-09-29-a2a-server-protocol/test-red.local.log`), for the expected
reasons (`taskId` key missing from dump, `task.id` stayed `None` after validating `taskId` input).

## Changes made

1. `types.py`: added `AliasChoices` to the pydantic import line (`ConfigDict` was already imported,
   not duplicated). Added module-level `_TASK_ID_ALIAS = dict(serialization_alias="taskId",
   validation_alias=AliasChoices("taskId", "id"))` immediately before the `Task` class.
2. `Task`: added `model_config = ConfigDict(populate_by_name=True)`, overrode the inherited `id`
   field as `id: Optional[str] = SQLField(default=None, primary_key=True, **_TASK_ID_ALIAS)`, and
   added `contextId: str | None = None`.
3. `TaskStatusUpdateEvent` / `TaskArtifactUpdateEvent`: added `model_config =
   ConfigDict(populate_by_name=True)` and changed `id: str` to `id: str = Field(**_TASK_ID_ALIAS)`
   (plain pydantic `Field`, already imported).
4. `routers/a2a.py` line ~132 (inside `execute_a2a_request`): changed
   `response.model_dump(exclude_none=True)` to `response.model_dump(exclude_none=True,
   by_alias=True)`. No other line in that file touched; the `_handle_exception` call at ~188 was
   left as-is per instructions.

## SQLField alias pass-through — worked as-is, no fallback needed

`SQLField(default=None, primary_key=True, **_TASK_ID_ALIAS)` — i.e. passing
`serialization_alias`/`validation_alias` straight through SQLModel's `Field` — worked cleanly on
the first attempt with this project's pinned SQLModel version. No `TypeError` on class definition,
no fallback to a plain `alias=` kwarg was needed. `test_task_accepts_task_id_on_input` and
`test_task_serializes_id_as_task_id` both passed once the (separate, see below) `serialize_dt`
issue was fixed, confirming both directions of the alias work through the SQLModel-wrapped field.

## Concern (why DONE_WITH_CONCERNS, not DONE): pre-existing bug found and fixed in the same file

The required test `test_task_serializes_id_as_task_id` constructs a bare `Task(id="t1",
status=TaskStatus(...))` and calls `.model_dump(by_alias=True)`. This failed with
`PydanticSerializationError: Error calling function 'serialize_dt': AttributeError: 'NoneType'
object has no attribute 'isoformat'` — unrelated to the aliasing work. `Task.serialize_dt`
(`@field_serializer("update_date", "date")`) unconditionally calls `dt.isoformat()`, but
`update_date`/`date` are `Optional[datetime] = None` on `CommonBaseModel`/`BaseModelWithSQLSupport`
and are only populated by `.save()`. A `Task` instance built directly (never persisted) has both as
`None`, which this decorator has apparently never survived a `model_dump()` call before — no prior
test exercised it. This was already true before Task 1/2 and is unrelated to the wire-aliasing
change, but it blocked the plan's own required test, so I fixed it in the same file (in scope) by
adding `when_used="unless-none"` to the decorator:
`@field_serializer("update_date", "date", when_used="unless-none")`. This is a one-line, minimal,
behavior-preserving fix (when the fields ARE set, serialization is unchanged; when unset, the field
now serializes to `None`/is omitted under `exclude_none` instead of crashing). Flagging this
explicitly since it's a real behavior change beyond what the task described, made because the
required test could not otherwise pass — worth a reviewer's eye.

No other files were touched. `codemie_task_manager.py`, `task_manager.py`, `utils.py` were left
alone as instructed (still broken by Task 2's renames, not in scope here).
