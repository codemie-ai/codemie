status: DONE

commit: a01264e8d
changed_files: src/codemie/datasource/google_doc/google_doc_datasource_processor.py, src/codemie/rest_api/main.py, tests/codemie/datasource/google_doc/test_google_doc_datasource_processor.py
test_command: poetry run pytest tests/codemie/datasource/google_doc/test_google_doc_datasource_processor.py -v; poetry run python -c "import ast; ast.parse(open('src/codemie/rest_api/main.py', encoding='utf-8').read())"; poetry run pytest tests/codemie/rest_api/test_main_keycloak_migration.py tests/codemie/rest_api/test_main_spend_tracking_setup.py -v

Part A: added the failing test `test_client_is_not_a_class_attribute` (asserts `"client" not in GoogleDocDatasourceProcessor.__dict__`), confirmed it failed for the correct reason (class dict contained a real `Elasticsearch(['http://localhost:9200'])` instance), then removed the class-body line `client = ElasticSearchClient.get_client()` from `GoogleDocDatasourceProcessor`. Kept the `ElasticSearchClient` import since three existing tests patch it via `codemie.datasource.google_doc.google_doc_datasource_processor.ElasticSearchClient`; `BaseDatasourceProcessor.__init__` still sets `self.client` lazily per-instance via `super().__init__()`, so no other behavior changed. All 13 tests in the file pass with zero regressions.

Part B: wrapped the module-scope `StateImportService().import_indexes()` call in `main.py` with `if retrieval_available(config):`, using the already-imported `retrieval_available` and `config` names (no new imports added). Verified with an AST parse of the file and by re-running `test_main_keycloak_migration.py` and `test_main_spend_tracking_setup.py` (9 tests, all pass) since those import `codemie.rest_api.main` at module load.
