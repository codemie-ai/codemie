# Plan: Remove ZephyrSquad integration entirely (backend)

Ticket: EPMCDME-10913 (reopened). Product owner (Yana Asadchaya) explicitly rejected the
prior "deprecate" approach (merged MR codemie!3904) and asked for total removal: "Let's
just totally remove ZephyrSquad from all app places and that's all." Confirmed: zero prod
usage, ZephyrSquad service itself no longer exists, no backward compatibility required.

Decision on the DB-level risk flagged by research (Section 6 of technical-analysis.md):
`credential_type` is a native Postgres ENUM. Since the PO confirmed zero prod rows and
explicitly said no backward compatibility is needed, we do a full removal: drop the
`ZEPHYR_SQUAD` label from the `credentialtypes` enum via a `sync_enum_values` migration,
matching the existing per-migration pattern. This is what "totally remove... that's all"
means at the DB layer, not an inert leftover enum value forever.

## Task 1 — Delete the ZephyrSquad tool implementation and catalog registration

Test-first: yes — `test_qa_toolkit.py::test_get_definition` and `test_tools_property`
currently assert `len(toolkit_ui.tools) == 5` and `"ZephyrSquad" in tool_names`; update
these first to assert `== 4` and absence, confirm they fail (RED) against unmodified
`toolkit.py`, then remove the registration to make them pass (GREEN).

- Delete `src/codemie_tools/qa/zephyr_squad/` (whole directory: `__init__.py`,
  `tools_vars.py`, `models.py`, `tools.py`, `api_wrapper.py`).
- `src/codemie_tools/qa/toolkit.py`: remove the `ZephyrSquadGenericTool`/`ZEPHYR_SQUAD_TOOL`
  import and its `Tool.from_metadata(...)` registration.
- `src/codemie_tools/base/models.py`: remove `deprecated: Optional[bool] = False` from
  `ToolMetadata` and `Tool`, and its propagation in `Tool.from_metadata` (confirmed zero
  other consumers).
- Delete `tests/codemie_tools/qa/zephyr_squad/` (whole directory).
- Delete `tests/codemie_tools/base/test_models_deprecated_flag.py`.
- `tests/codemie_tools/qa/test_qa_toolkit.py`: remove `ZEPHYR_SQUAD_TOOL` import (line 17),
  delete `test_zephyr_squad_marked_deprecated` and `test_zephyr_squad_metadata_deprecated_flag`,
  update the two count/membership assertions.

## Task 2 — Remove ZephyrSquad from the settings/credentials service layer

Test-first: yes — `test_settings_request_validator.py`'s deprecated-registry block and the
4 router "zephyr_squad_blocked" tests currently pass against the deprecation mechanism;
after deletion they must be removed (they'd otherwise fail to collect once
`CredentialTypes.ZEPHYR_SQUAD` / `DEPRECATED_CREDENTIAL_TYPES` no longer exist). Add one
generic-invalid-credential-type test if none remains post-deletion, confirming the
routers still 422 on a bogus `credential_type` — write/verify that test first.

- `src/codemie/service/settings/settings_request_validator.py`: remove
  `DEPRECATED_CREDENTIAL_TYPES` registry and `validate_credential_type_not_deprecated`;
  keep the `CredentialTypes` import if other code in the file still needs it (verify).
- `src/codemie/rest_api/routers/user_settings.py` and `project_settings.py`: remove the
  `validate_credential_type_not_deprecated` import and its 2 call sites each (create +
  update).
- `src/codemie/service/settings/settings.py`: remove `ZephyrSquadConfig` import; the
  `ZEPHYR_SQUAD_ACCOUNT_ID`/`ZEPHYR_SQUAD_ACCESS_KEY`/`ZEPHYR_SQUAD_SECRET_KEY` constants;
  their `LIST_OF_SENSITIVE_FIELDS` entries; `ZEPHYR_SQUAD_FIELDS`; the
  `ZephyrSquadConfig: CredentialTypes.ZEPHYR_SQUAD` entry in `__CREDENTIAL_CONFIG_TO_TYPE`;
  and the dead `get_zephyr_squad_creds()` method.
- `src/codemie/service/settings/settings_tester.py`: remove `ZephyrSquadConfig`/
  `ZephyrSquadGenericTool` imports, the `CredentialTypes.ZEPHYR_SQUAD` dispatch entry, and
  `_test_zephyr_squad()`.
- `src/codemie_tools/base/models.py`: remove `CredentialTypes.ZEPHYR_SQUAD` enum member.
- Delete matching test blocks: `test_settings_request_validator.py` lines ~25,27,344-386;
  `test_user_settings.py` `ZEPHYR_SQUAD_DEPRECATION_MESSAGE` + both blocked tests;
  `test_project_settings.py` same pattern.

## Task 3 — Drop the DB enum label

Test-first: no — this is a schema migration, verified by running it locally against the
dev DB and confirming `\dT+ credentialtypes` no longer lists `ZephyrSquad`, plus that
existing settings rows (none expected) still deserialize.

- Add a new Alembic migration using `op.sync_enum_values(...)` for the `credentialtypes`
  enum, following the pattern in `e03e516e00da_add_xray_credential_type.py` etc., but
  removing `ZephyrSquad` from the value list instead of adding one.
- Run the migration locally; confirm no data-rewrite error (expected, since zero rows use
  this value).

## Task 4 — Cosmetic cleanup

Test-first: no.

- `README.md:318`: drop "Zephyr Squad" from the QA toolkit description list.
- Optional: reword the `"ZephyrSquad"` sample string in
  `search_and_rerank/tool.py`/its test to any other camelCase example — purely cosmetic,
  skip if it risks unrelated churn.
- Verify `config/customer/customer-config.yaml:389-391` and `pyproject.toml:141`
  (`zephyr-python-api`) per the research risk notes before touching — leave alone if
  shared with Zephyr Scale or actually unused-but-unconfirmed (favor not breaking Zephyr
  Scale over a marginal cleanup).

## Task 5 — MR review follow-up (codemie!4148 note 2390663)

Test-first: yes — mutation-checked (dropping or reordering one enum value fails the new tests).

- Migration `d9e8f7a6b5c4`: run the `DELETE` through `op.get_bind()` and log its `rowcount`
  (WARNING when non-zero, INFO otherwise); the docstring states why there is no assertion and
  that downgrade does not restore rows.
- New `tests/codemie/migrations/test_credentialtypes_enum_migrations.py`: replays every
  migration that syncs `credentialtypes` against a mocked `op` and checks that the head enum
  equals the `CredentialTypes` model and that each `downgrade()` restores exactly the enum left
  by its nearest enum-changing ancestor. Three historical revisions with an order-only
  difference are checked by set equality. Plus tests for the row-count logging.
- `search_and_rerank/tool.py` + test: camelCase example `ZephyrSquad` -> `ZephyrScale`.
- Rebase onto `main` (2026-09-11): main added four migrations after `fa14587c0de1`, so
  `d9e8f7a6b5c4` is re-parented onto `d3c838ee6ab9` (none of them touch the enum).
- Live-database check found that the `DELETE` failed with "invalid input value for enum" on a
  re-run, once the label is gone; the column is now compared as text.
- FAQ example files (`evaluation/examples/CodeMie FAQ.*`) live in codemie-sdk and are fixed in
  codemie-sdk!646 together with the SDK enum tests and the credential-type fan-out note.

## Out of scope

- Zephyr Scale / Zephyr Cloud (`_ZEPHYR_CLOUD`) — different, active integration, untouched.
- Coordinating with the UI removal (parallel sdlc-light run in codemie-ui, same branch
  name `EPMCDME-10913_remove-zephyrsquad`) — cross-link MRs at the end.
