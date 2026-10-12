DONE
commit: 121d670019
changed: .ai-run/guides/agents/custom-tool-creation.md
test_command: none (docs)

Checklist:
- [x] "not a runtime plug-in" statement present
- [x] Requirement 1 (closed-enum members, same PR) present
- [x] Requirement 2 (sync_enum_values migration, delete-then-shrink downgrade, alembic heads check before writing and before merging) present
- [x] Requirement 3 (settings wiring: __CREDENTIAL_CONFIG_TO_TYPE + LIST_OF_SENSITIVE_FIELDS) present
- [x] Requirement 4 (hand-authored codemie-ui CREDENTIAL_UI_MAPPING entry) present
- [x] Requirement 5 (backend + UI redeploy) present
- [x] Email/IMAP (!4044) vs Exchange coexistence note present
- [x] MS_EXCHANGE (EPMCDME-15199) named as reference implementation
- [x] REQ-3.1.9 noted as external requirement ID

Concerns: none. Section is ~22 lines, matches existing heading/table/list style of the guide. Only the target file was staged and committed; doctor.json and the untracked docs/superpowers/tasks/ directory were left untouched.
