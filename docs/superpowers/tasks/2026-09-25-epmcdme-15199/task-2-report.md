DONE
commit: 566feef9
changed: src/codemie_tools/ms_exchange/models.py, tests/codemie_tools/ms_exchange/test_models.py
test_command: cd /Users/Oleksandr_Cherevach/work/codemie-dev/codemie && poetry run pytest tests/codemie_tools/ms_exchange/ > /Users/Oleksandr_Cherevach/work/codemie-dev/codemie/docs/superpowers/tasks/2026-09-25-epmcdme-15199/test.local.log 2>&1; echo "EXIT=$?"; tail -25 <log>
RED evidence: initial run of tests/codemie_tools/ms_exchange/test_models.py exited 1 with `ModuleNotFoundError: No module named 'codemie_tools.ms_exchange.models'` (collection error), confirming the test suite was written before the module existed.
GREEN evidence: after implementing models.py, tests/codemie_tools/ms_exchange/test_models.py exited 0 with `17 passed`; full tests/codemie_tools/ms_exchange/ directory exited 0 with `23 passed, 5 warnings` (warnings are pre-existing/unrelated: langgraph deprecation, google-auth grpcio PQC).
Renames: none.
Reused patterns: RequiredField, CodeMieToolConfig, CredentialTypes from src/codemie_tools/base/models.py (same as notification/email/models.py); mirrored the model_validator(mode="after") dual-path style and license header from src/codemie_tools/notification/email/models.py and tests/codemie_tools/notification/email/test_models.py.
Guides/standards applied: superpowers:test-driven-development skill followed (RED then GREEN); ran `poetry run ruff format` and `poetry run ruff check` on both files before commit (ruff format reformatted both files for line-length wrapping; ruff check passed clean).
Concerns: none. Only oauth_client_secret and service_account_password are marked sensitive in the schema, verified via test_only_two_secrets_marked_sensitive. No get_tool_default or ntlm fallback used; auth_type defaults to None as required.
