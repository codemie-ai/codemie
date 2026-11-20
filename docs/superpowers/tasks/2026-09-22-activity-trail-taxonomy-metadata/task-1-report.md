status: DONE
commit: 54f5ac8d5
changed_paths:
- src/codemie/service/activity/activity_models.py
- src/codemie/service/activity/activity_taxonomy.py
- tests/codemie/service/activity/test_activity_taxonomy.py
test_command: poetry run pytest tests/codemie/service/activity/test_activity_taxonomy.py -v
notes: Followed TDD strictly — RED confirmed as ModuleNotFoundError for codemie.service.activity.activity_taxonomy before any implementation code was written; GREEN confirmed with 43 passed. Noted: the four event classes (ProjectManagementEvent, CustomerConfigEvent, UserManagementEvent, BudgetManagementEvent) are plain classes with string constants, not Enum subclasses, so the test collects values via vars()/isinstance(str) instead of list(EnumClass). Also had to run `poetry install --sync` first — the active poetry venv was missing pytest-xdist/execnet, causing pytest.ini's `-n 2` addopt to fail with "unrecognized arguments: -n"; this was an environment sync issue unrelated to the task code. Full tests/codemie/service/activity/ suite (81 tests) also passes after the change.
