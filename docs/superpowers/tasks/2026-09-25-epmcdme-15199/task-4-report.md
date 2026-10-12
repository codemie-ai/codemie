DONE

commit: 567071991
changed: src/external/alembic/versions/5189d4d2afaf_add_ms_exchange_credential_type.py, tests/external/alembic/test_add_ms_exchange_credential_type.py
test_command: cd /Users/Oleksandr_Cherevach/work/codemie-dev/codemie && poetry run pytest tests/external/alembic/test_add_ms_exchange_credential_type.py > /Users/Oleksandr_Cherevach/work/codemie-dev/codemie/docs/superpowers/tasks/2026-09-25-epmcdme-15199/test.local.log 2>&1; echo "EXIT=$?"; tail -25 /Users/Oleksandr_Cherevach/work/codemie-dev/codemie/docs/superpowers/tasks/2026-09-25-epmcdme-15199/test.local.log
revision: 5189d4d2afaf down_revision: u2v3w4x5y6a7
heads: Static AST parse of revision/down_revision (Assign and AnnAssign, tuples for merges) across all 185 revision files in src/external/alembic/versions/*.py found exactly one head: 5189d4d2afaf (src/external/alembic/versions/5189d4d2afaf_add_ms_exchange_credential_type.py). No second head remains.
RED: test_migration_declares_expected_revision_chain failed with AssertionError: assert 'fa14587c0de1' == 'u2v3w4x5y6a7' (1 failed, 3 passed) before the fix, confirming the test exercised the down_revision bug.
GREEN: after setting down_revision = "u2v3w4x5y6a7" (and updating the docstring "Revises:" line), all 4 tests in tests/external/alembic/test_add_ms_exchange_credential_type.py passed (EXIT=0).
concerns: None. PRE (used for enum new_values) still correctly derives from fa14587c0de1's _ENUM_VALUES_FOLDED, which remains the latest credentialtypes enum state — only the alembic chain pointer (down_revision) was changed to attach to the single pre-existing head u2v3w4x5y6a7. ruff format and ruff check both passed clean on the two files. git show --stat HEAD confirms only these two files were committed; .ai-run/sdlc-factory/doctor.json and docs/superpowers/tasks/ were left untouched/unstaged.
