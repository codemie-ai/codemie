status: DONE
commit: 8ab6789b2
changed_paths:
- src/codemie/service/project/project_service.py
- tests/codemie/service/project/test_project_service.py
test_command: poetry run pytest tests/codemie/service/project/ -q
notes: Added a second, conditional ActivityEventCreate (ProjectManagementEvent.MEMBER_SPEND_ENFORCEMENT_UPDATED) in ProjectService.update_project, emitted only when enforce_member_spend_limits is not None and differs from SettingsService.get_enforce_member_spend_limits(project.name) read before the existing set call. The unconditional PROJECT_UPDATED emission is untouched. Two new tests (TestProjectServiceMemberSpendEnforcementActivityEvent) cover the changed and unchanged cases; both were verified RED (1 failed) before implementation and GREEN after. Full tests/codemie/service/project/ suite: 226 passed.
