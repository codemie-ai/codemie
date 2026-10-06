status: DONE

commit: 48ba852b4
changed_files: src/codemie/rest_api/main.py
test_command: poetry run python -c "import ast; ast.parse(open('src/codemie/rest_api/main.py', encoding='utf-8').read())" ; poetry run pytest tests/codemie/rest_api/test_main_keycloak_migration.py tests/codemie/rest_api/test_main_spend_tracking_setup.py -q ; poetry run pytest tests/codemie/configs/test_config.py -k retrieval_available -v

Wrapped `app.include_router(index.router)` (line 894) in `if retrieval_available(config):` and added `retrieval_available` to the existing `from codemie.configs.config import ENV_LOCAL` import, matching the style of the existing `is_litellm_enabled()`/`ENABLE_USER_MANAGEMENT` conditional router blocks in the same file. Verified with an AST syntax check, the two existing test files that import `codemie.rest_api.main` at module load time (9 tests, all passed, confirming no import-time regression), and the pre-existing `retrieval_available` unit tests (2 tests, both green). Left `main.py:887` (`StateImportService().import_indexes()`) and all other `app.include_router` lines untouched, and staged/committed only `src/codemie/rest_api/main.py`.
