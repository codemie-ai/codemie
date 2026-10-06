status: DONE

commit: ace33aa29
changed_files: src/codemie/configs/customer_config.py, tests/codemie/configs/test_customer_config.py
test_command: poetry run pytest tests/codemie/configs/test_customer_config.py -v

Added three retrieval-gated runtime components (`features:knowledgeBases`, `features:datasources`,
`features:codeIndexing`) to `CONFIG_IDS` and `_get_runtime_config()` in `src/codemie/configs/customer_config.py`,
each computed via `retrieval_available(config)` (imported from `codemie.configs.config`, already on branch).
Also fixed the `is_feature_enabled()` docstring, which wrongly claimed unknown features default to `True`
(the code actually delegates to `is_component_enabled()`, which defaults to `False`).

Test-first: added two new tests (`test_retrieval_features_enabled_when_backend_available`,
`test_retrieval_features_disabled_when_backend_none`) asserting all three components via
`is_component_enabled()` and `get_enabled_components()`. Adjusted 5 pre-existing `len()` assertions on
`get_enabled_components()` output (`test_get_enabled_components`, `test_runtime_features_enterprise_installed`,
`test_runtime_features_enterprise_not_installed`, `test_runtime_features_override_yaml` all +3; each of these
now explicitly sets `mock_config.RETRIEVAL_BACKEND = "elasticsearch"` since an unset MagicMock attribute would
have implicitly counted the new components as enabled anyway) plus `test_disabled_runtime_features_excluded_like_yaml`,
which was left at its original count of 3 by explicitly setting `mock_config.RETRIEVAL_BACKEND = "none"` (fits its
"disabled things excluded" theme) with added assertions confirming the three new components are excluded. The
four single-component-count checks (`idpProvider` x2, `mcpAuthOrigin` x2) were verified unaffected — they filter
`get_enabled_components()` output by a specific unrelated id.

Verification: `poetry run pytest tests/codemie/configs/test_customer_config.py -v` -> EXIT=0, 31 passed (was 29
before this task, +2 new tests). `poetry run pytest tests/codemie/configs/test_config.py -v` -> EXIT=0, 14 passed,
confirming Task 1's tests (including `test_retrieval_available_true_for_elasticsearch` /
`test_retrieval_available_false_for_none`) are unaffected.
