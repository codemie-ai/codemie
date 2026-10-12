DONE
commit: 65eac697
changed: src/codemie/service/settings/settings.py, tests/codemie/service/settings/test_exchange_settings.py
test_command: cd /Users/Oleksandr_Cherevach/work/codemie-dev/codemie && poetry run pytest tests/codemie/service/settings/test_exchange_settings.py > /Users/Oleksandr_Cherevach/work/codemie-dev/codemie/docs/superpowers/tasks/2026-09-25-epmcdme-15199/test.local.log 2>&1; echo "EXIT=$?"; tail -25 .../test.local.log
RED evidence: EXIT=1, 4 failed — get_config raised `ValueError: ConfigClass is not supported... ExchangeToolConfig` for both auth paths, and NTLM's service_account_password was neither encrypted (`test-secret` unchanged) nor masked (assert '**********' == 'test-secret' failed).
GREEN evidence: EXIT=0, 8 passed (all 4 test functions x 2 auth-path parametrizations).
Regression check: full `tests/codemie/service/settings/` run — 291 passed, 1 failed (`test_folded_oauth_type.py::test_oauth_settings_service_dispatches_by_provider`), pre-existing and unrelated: `ModuleNotFoundError: No module named 'codemie_enterprise'` at import time, not touched by this task's files.
Notes:
- `oauth_client_secret` was already present in LIST_OF_SENSITIVE_FIELDS (added earlier for email-notification OAuth), so only `service_account_password` was newly appended; no duplicate entry added, `client_secret` key left untouched.
- Added one import (`ExchangeToolConfig` from `codemie_tools.ms_exchange.models`) and one map entry `ExchangeToolConfig: CredentialTypes.MS_EXCHANGE` in `__CREDENTIAL_CONFIG_TO_TYPE`.
- No `SettingsTester` handler added, per plan.
- Test file follows patterns from `tests/codemie/service/settings/test_sharepoint_auth_switch.py` (SimpleNamespace-free real `Settings`/`CredentialValues` model usage) and `test_folded_oauth_type.py` (monkeypatching internals to exercise `get_config` without a DB). Since the repo's default test encryption is `PlainEncryptionService` (identity), tests monkeypatch `SettingsService.encryption_service` to the existing `Base64EncryptionService` (reused, not newly written) so encryption/decryption round-trip assertions are meaningful; `get_config`'s DB layer is stubbed by patching `build_settings_handlers` so the real `retrieve_setting`/`_decrypt_fields` path executes.
- `poetry run ruff format` and `poetry run ruff check` run clean on both changed files.
- `git show --stat HEAD` confirms exactly the two declared files.
- Concerns: none. The one failing test elsewhere in the directory is an unrelated pre-existing environment gap (missing `codemie_enterprise` package), not caused by this task.
