# EPMCDME-15921: Repair MS Teams settings rows corrupted by migration c3d4e5f6a7b8

## Problem

`c3d4e5f6a7b8_migrate_assistant_project_mapping_to_settings.py` (EPMCDME-14111) wrote MS_TEAMS PROJECT
rows into `codemie.settings` with two defects (see `technical-analysis.md` §1):

- **A**: `alias`, `user_id` and `created_by` are NULL (l.83-89). The UI edit page needs an alias, so it shows
  "Missing required parameters". The API (`SettingRequest.alias`, `check_alias_unique`) and project
  transfer (`_validate_aliases_present`) also reject a missing alias.
- **B**: `credential_values` is double-encoded. `json.dumps` goes into a `JSONB` bind (l.90/94, 119/130), so
  the column holds a jsonb **string**. `PydanticListType` reads that as `[]`. As a result the Teams bot sees
  no assistants, and `prune_ms_teams_assistant_id` never pruned these rows.

The fix is in the backend data layer only. The UI keeps looking integrations up by alias.

## Design

### 1. New data-repair revision

Chained on the current single head `y1z2a3b4c5d6`. Rows are read with `op.get_bind()`, transformed by pure
module-level helpers (unit-testable, as in `c3d4e5f6a7b8`), and written back one row at a time (about 51 rows
on prod). Raw SQL uses the DB enum labels `'MS_TEAMS'`, `'PROJECT'` and `'USER'`.

**Target rows.** A row is targeted only if it is MS_TEAMS and shows a defect: `jsonb_typeof(credential_values) = 'string'`,
or `alias` is NULL or empty. Healthy rows are never written, so a second run changes nothing.

**Decode (string rows only).** Decode with `(credential_values #>> '{}')::jsonb`, or the Python equivalent. If
the result is not an array, or is an array containing any non-object element, log a warning, skip the row
(left as is, alias not backfilled), and let the migration continue. No known producer writes such rows; the
stricter skip came from code-review CR-001 (round 1) and was accepted by the user. Entries other than
`assistant_ids` are kept as they are.

**Assistant-id filter (string rows only).** This makes the row look as if prune had worked all along. Within
the `assistant_ids` value, drop an id when:

- it has no row in `codemie.assistants` (the assistant was deleted), or
- the assistant's `project` differs from the row's `project_name` **and** `is_global` is not true.

This is exactly the rule in `_validate_ms_teams_assistant_ownership`
(`settings_request_validator.py:372-387`, backed by `AssistantService.belongs_to_project` /
`is_marketplace_assistant`, `assistant_service.py:112-144`). It must be implemented as raw SQL against
`codemie.assistants(id, project, is_global)`, **not** by calling application services. Non-string ids and
duplicates are dropped too, so the list passes the validator's shape checks. Each dropped id is logged at
info level with the row id, the project and the reason (`deleted`, `foreign`, `invalid`). If nothing valid
remains, the row is kept with `{"key": "assistant_ids", "value": []}`, which is the same end state prune
produces.

**Alias backfill (rows with a NULL or empty alias).** The alias is `msteams-<date>`, where `<date>` is the
row's `date` column formatted as `YYYY-MM-DD_HH-MM` (the format of the UI's `generateDefaultAlias`). If
`date` is NULL, the migration's run time is used. On a collision, append `-2`, `-3` and so on. Uniqueness
follows `Settings.check_alias_unique` (`models/settings.py:306-325`):

- PROJECT: exact match on `project_name` + `alias` across all settings rows of that project, any type or scope.
- USER: `project_name` + `user_id` + `alias`.

Aliases already assigned earlier in the same run count as taken. No `{"key":"alias"}` entry is added to
`credential_values`, because the backend does not read one.

**Unchanged fields.** `user_id` and `created_by` stay NULL. `project_name`, `credential_type` and
`setting_type` are not modified.

**Write.** `credential_values` must be stored as a jsonb **array**. This is the exact trap Defect B fell
into, so the bind or cast must be chosen deliberately and tested.

**Logging.** One summary line: rows scanned, decoded, aliased, ids dropped, rows skipped.

**Downgrade.** Logs and does nothing. Re-encoding would break the data again. This follows the
`c3d4e5f6a7b8` precedent (l.156-163).

### 2. Correct `c3d4e5f6a7b8` in place

This only affects environments that have not run it yet. On both the insert and merge paths:

- Bind the list itself so the stored value is a jsonb array.
- Set an alias on inserted rows, using the same format and uniqueness rule as above.
- Apply the same assistant-id ownership filter as the repair revision before insert and merge: keep an id
  only if the assistant exists and either belongs to the row's project or is `is_global`, checked with raw
  SQL against `codemie.assistants`. Log each dropped id with project and reason. (Code-review CR-002, user
  decision Option A: both upgrade paths must leave identical, savable data.) Deleted ids cannot occur here
  because the mapping table cascaded on assistant delete, but foreign ids can.

After the corrected file runs, the repair finds nothing to do.

### 3. Verification

- **Unit tests** in `tests/codemie/migrations/`, in the existing mocked-`op` style (SQL text, call order,
  `caplog`) plus pure-helper tests. They cover decode, the filter reasons, the empty-list outcome, alias
  generation including collisions and USER scope, skipping healthy rows, non-array decode, the no-op
  downgrade, and the single head. Extend the `c3d4e5f6a7b8` test so the value it binds is a list, not a str,
  and an alias is set.
- **Manual run** against the local podman Postgres (4 broken rows plus 1 healthy row). Record the before and
  after `jsonb_typeof`, alias and `assistant_ids`. Then run downgrade and upgrade again and confirm nothing
  changes. The local ids are all owned by project `codemie`, an artifact of how the data was seeded, so to
  exercise the keep path, seed one broken row whose id is owned by its project.

## Acceptance criteria

1. After upgrade, every MS_TEAMS row has `jsonb_typeof(credential_values) = 'array'` and a non-empty alias.
2. Recovered ids that exist and are project-owned or `is_global` are kept. The others are dropped, and each drop is logged with row, project and reason.
3. Rows left with no valid ids keep an `assistant_ids` entry with `[]`. No row is deleted.
4. A backfilled alias is `msteams-YYYY-MM-DD_HH-MM[-N]` and is unique under the `check_alias_unique` scope rule.
5. Healthy rows are byte-identical after upgrade, and a second upgrade pass changes nothing.
6. Downgrade changes no data.
7. Corrected `c3d4e5f6a7b8` stores an array, sets an alias, and keeps only ids that exist and are project-owned or `is_global` (dropping and logging the rest). Its existing helper tests still pass.
8. The repaired rows open in the UI edit page and save through `PUT` project settings once assistants are selected (AC1-3 of the ticket).
9. Alembic still has a single head.

## Non-goals

- No UI change, and no change to how the UI looks integrations up.
- No change to the settings model, the validator, the transfer service or the Teams bot.
- `user_id` and `created_by` are not filled.
- Duplicate PROJECT MS_TEAMS rows per project are not merged or deduplicated, and the missing singleton indexes are not created.
- No DB-backed migration test harness.
- No re-encoding or restoring of data on downgrade.
