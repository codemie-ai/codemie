DONE

## Summary

Task 4 of the A2A protocol v1.0 upgrade slice (EPMCDME-15327): reshaped `AgentCard` in
`src/codemie/rest_api/a2a/types.py` to the v1.0 wire shape.

## Changes

- `AgentCapabilities` gained `extendedAgentCard: bool = False`.
- New `AgentInterface(BaseModel)` class (`url: str`, `transport: str`), placed after `AgentSkill`
  and before `AgentCard`.
- `AgentCard.url: str` removed; replaced in the same position by
  `supportedInterfaces: List[AgentInterface]`.
- No `signatures` field added (explicitly out of scope / non-goal).
- `AgentProvider`, `AgentAuthentication`, `AgentSkill`, `GetExtendedAgentCardResponse` left untouched.

## Commit

`6f19cabb7` — `EPMCDME-15327: Reshape AgentCard to v1.0 supportedInterfaces shape`

## Changed files (exact, from `git show --stat HEAD`)

- `src/codemie/rest_api/a2a/types.py`
- `tests/codemie/rest_api/a2a/test_types.py`

## Test-first evidence

- RED: ran the target test file before the implementation change — collection failed with
  `ImportError: cannot import name 'AgentInterface' from 'codemie.rest_api.a2a.types'`, the
  expected failure for the not-yet-added class.
- GREEN: after applying the changes, ran:

  ```
  poetry run pytest tests/codemie/rest_api/a2a/test_types.py -v > docs/superpowers/tasks/2026-09-29-a2a-server-protocol/test.local.log 2>&1; echo "EXIT=$?"; tail -30 docs/superpowers/tasks/2026-09-29-a2a-server-protocol/test.local.log
  ```

  `EXIT=0`, all 8 tests passed (2 from Task 1, 2 from Task 2, 3 from Task 3, 1 new from this task):
  `test_text_part_has_no_type_field`, `test_part_union_validates_without_discriminator`,
  `test_a2a_request_resolves_send_message`, `test_a2a_request_rejects_list_tasks`,
  `test_task_serializes_id_as_task_id`, `test_task_accepts_task_id_on_input`,
  `test_status_update_event_accepts_id_kwarg_and_serializes_task_id`,
  `test_agent_card_has_no_url_field`.

## Concerns

- As documented in the task scope, `src/codemie/rest_api/a2a/utils.py` constructs
  `AgentCard(url=...)` and will now fail to import/construct correctly — this is expected and is
  Task 5's responsibility, not this task's. Not fixed here.
- `tests/codemie/rest_api/a2a/test_utils.py` and the router/task-manager test files were not run,
  per explicit instruction — they are known-broken from earlier tasks' renames plus this task's
  field removal.
