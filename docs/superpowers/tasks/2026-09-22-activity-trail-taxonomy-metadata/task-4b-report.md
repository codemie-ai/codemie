status: DONE
changed_paths:
- src/codemie/service/budget/project_budget_service.py
- tests/codemie/service/budget/test_budget_service_activity.py
- tests/codemie/service/budget/test_project_budget_service.py
- tests/codemie/service/budget/test_project_budget_service_lifecycle.py
test_command: poetry run pytest tests/codemie/service/budget/ -q
notes: Added entity_label= to all 11 ActivityEventCreate(...) call sites in project_budget_service.py, using budget.name for budget-scoped events and group.name (ProjectBudgetGroup.name) for group-scoped events — all values were already in local scope, no new queries added. Extended assertions in test_budget_service_activity.py for each site (RED confirmed before the source change, GREEN after) and fixed pre-existing SimpleNamespace/MagicMock fixtures in test_project_budget_service.py and test_project_budget_service_lifecycle.py that lacked a `.name`/`name=` attribute and would otherwise AttributeError once entity_label=budget.name/group.name was added. Full tests/codemie/service/budget/ suite: 179 passed, 0 failed. Did not touch budget_service.py, active_project_budget_restore_service.py, inactive_project_budget_stop_service.py, inactive_cost_center_budget_stop_service.py, or test_inactive_project_budget_stop_service.py (already modified by other in-progress work). No git add/commit performed.
