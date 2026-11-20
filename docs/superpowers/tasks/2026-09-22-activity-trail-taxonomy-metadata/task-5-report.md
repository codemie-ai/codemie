status: DONE
commit: ad1f84908
changed_paths:
- src/codemie/service/user/user_management_service.py
- src/codemie/service/user/registration_service.py
- src/codemie/service/user/authentication_service.py
- src/codemie/rest_api/routers/local_auth_router.py
- src/codemie/service/customer_config_service.py
- tests/codemie/service/user/test_user_management_service.py
- tests/codemie/service/user/test_registration_service.py
- tests/codemie/service/user/test_authentication_service.py
- tests/codemie/rest_api/routers/test_local_auth_router.py
- tests/codemie/service/test_customer_config_audit.py
test_command: poetry run pytest tests/codemie/service/user tests/codemie/rest_api/routers/test_local_auth_router.py tests/codemie/service/test_customer_config_audit.py tests/codemie/service/test_customer_config_refresh.py tests/codemie/service/test_customer_config_service.py tests/codemie/service/test_customer_config_validation.py -q
notes: Added entity_label=<in-scope user.name or user.email> at all 8 ActivityEventCreate sites in the three user-service files and the local_auth_router logout site; used declaration.key as entity_label for the shared customer_config_service._audit() helper. All 11 targeted TDD RED/GREEN cycles passed (RED confirmed for each site before the fix); the broader sweep of tests/codemie/service/user shows one pre-existing unrelated failure (TestLoadUserForAuth::test_load_user_for_auth_found, an is_admin assertion) that reproduces identically on the pre-task-5 commit (verified via git stash), so it is not a regression from this change.
