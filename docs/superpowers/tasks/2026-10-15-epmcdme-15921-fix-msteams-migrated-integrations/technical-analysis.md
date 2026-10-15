# Technical Research

**Task**: settings msteams alembic migration
**Generated**: 2026-10-15
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-15921 (Bug, Major) — MS Teams integration: "Missing required parameters" error when editing an existing integration.

Ticket description: When a user tries to edit an already created MS Teams integration in CodeMie, the "Missing required parameters" error is displayed and the integration cannot be edited. Steps: integrations section → open existing MS Teams integration → Edit → change any field → Save. Expected: updated and saved. Actual: error, changes not saved.
Ticket acceptance criteria:
1. Editing an existing MS Teams integration saves changes without errors.
2. The "Missing required parameters" error is no longer displayed for MS Teams integrations.
3. Existing integration data is preserved after editing.

VERIFIED root cause (do not re-derive; build on it). Fix lives in THIS repo (codemie backend); UI stays alias-based by user decision.
- Alembic migration src/external/alembic/versions/c3d4e5f6a7b8_migrate_assistant_project_mapping_to_settings.py (EPMCDME-14111, MR !4094, merged 2026-09-01, ran on prod 2026-09-07) moved assistant_project_mapping into codemie.settings MS_TEAMS PROJECT rows and dropped the source table.
- Defect A: insert hardcodes alias/user_id/created_by = NULL (lines 83-89). Spec (docs/superpowers/tasks/2026-08-27-teams-integ-2-migration/spec.md:32) said alias=None; a later review (2026-08-31 option-4b task, CR-008) flagged it as major and it was dismissed as out of scope. UI edit lookup and API (SettingRequest.alias: str, Settings.check_alias_unique "Alias is required"), and project transfer (_validate_no_blank_aliases) all require an alias.
- Defect B: credential_values double-encoded: json.dumps(...) passed into sa.bindparam(type_=JSONB) (lines 90/94 + 119/130), on both the insert path and the merge/update path. Verified by running the statement through PostgresClient.get_engine() (psycopg2): stored jsonb_typeof = 'string', value "[{\"key\": \"assistant_ids\", \"value\": [...]}]"; PydanticListType.process_result_value (rest_api/models/base.py:338-350) iterates the string and returns []. Spec intended an array. Assistant ids are recoverable only from that string (source table dropped).
- Consumers of MS Teams assistant_ids: Teams bot (codemie-ms-teams-bot src/service/assistants/manager.py:195-218) via integrations list API → gets [] for migrated projects; Settings.prune_ms_teams_assistant_id (models/settings.py:353+); validate_ms_teams_request (service/settings/settings_request_validator.py:390-440) requires non-empty assistant_ids list of strings + project-owned assistants on update.
- Alias uniqueness (Settings.check_alias_unique, models/settings.py:306-325; get_by_fields exact == match, base.py:463-475): PROJECT → project_name + alias across ALL rows with that project_name (any credential_type, any setting_type); USER → project_name + user_id + alias. App-level only; no DB unique constraint on settings (verify against migrations, incl. f1a2b3c4d5e6_add_ms_teams_singleton_unique_index.py — a review claimed its partial-index predicate 'MS_TEAMS' may not match the enum value; check what the DB enum label actually is and whether this index matters for the repair).
- Prod sample (from user): migrated rows look like {"alias": null, "credential_values": [], "created_by": null, "user_id": null, "date": "2026-09-07T08:00:45..", "credential_type": "MSTeams", "setting_type": "project"}; ~51 MS Teams rows total on prod, newer UI-created ones are healthy (alias like "msteams-2026-10-14_14-27", credential_values contains {"key":"alias",...} and {"key":"assistant_ids",[...]}).
- Local DB (podman container `postgres`, db postgres, schema codemie) has 5 seeded broken rows reproducing this (projects codemie, demo, pr-1, user1@example.com, user2@example.com) plus one healthy row.

Intended fix (to be refined in spec): (1) NEW Alembic data-repair migration: for MS_TEAMS rows whose credential_values is a jsonb string, decode it back to an array; for MS_TEAMS rows with NULL/empty alias, backfill a deterministic alias unique per check_alias_unique's rule; idempotent and safe on healthy rows; sensible downgrade. (2) Correct c3d4e5f6a7b8 so environments that have not yet run it produce correct rows (pass list not json.dumps, or untyped bind + CAST; set alias). Consider whether user_id/created_by should stay NULL. Consider deleted/moved assistants inside recovered ids (prune semantics), and the multi-head Alembic state (determine correct down_revision / current head(s)).

Research focus: Alembic layout and head(s) (how to pick down_revision; repo's 13-head note), conventions of recent data migrations (raw SQL via op.get_bind, logging, tests under tests/codemie/migrations/), how migrations are tested here (is there any DB-backed migration test harness?), CredentialTypes enum label actually stored ('MS_TEAMS'), settings model/columns, alias uniqueness rule, any other code that reads MS Teams credential_values or alias, and risks.

---

## 2. Codebase Findings

### Existing Implementations
- `src/external/alembic/versions/c3d4e5f6a7b8_migrate_assistant_project_mapping_to_settings.py` — the defective migration. Pure helpers `_group_assistant_ids` / `_merge_assistant_ids` (lines 43-71) are unit-tested; `upgrade()` binds `json.dumps(...)` into `sa.bindparam("credential_values", type_=JSONB)` on insert (l.83-90, 130) and update (l.92-94, 119). Merge-path lookup (l.97-103) filters `project_name` + `credential_type = 'MS_TEAMS'` with no `setting_type` filter. `downgrade()` recreates an empty `assistant_project_mapping` and touches no settings rows (deliberate, l.156-163).
- Related MS Teams revisions: `8b2c1a4d5e6f_add_ms_teams_credential_type.py` (adds enum label `MS_TEAMS` via `op.sync_enum_values`; its downgrade DELETEs all MS_TEAMS settings rows), `f1a2b3c4d5e6_add_ms_teams_singleton_unique_index.py` (partial unique index `uix_settings_project_ms_teams_singleton` on `project_name WHERE credential_type = 'MS_TEAMS'`), `9c1d2e3f4a5b_add_ms_teams_user_singleton_unique_index.py` (`uix_settings_user_ms_teams_singleton` on `user_id WHERE credential_type='MS_TEAMS' AND setting_type='USER'`), `a8032409ff23_scope_ms_teams_project_singleton_to_project_type.py` (re-creates project index with `AND setting_type = 'PROJECT'`, raw SQL with `IF [NOT] EXISTS`).
- `src/codemie/rest_api/models/settings.py` — `SettingRequest.alias: str` (l.233, required); `SettingsBase.alias: Optional[str]` (l.257); `credential_values` column `PydanticListType(CredentialValues)`; `check_alias_unique` (l.306-325); `check_ms_teams_exist` / `check_ms_teams_exist_for_user` (l.327-351); `prune_ms_teams_assistant_id` (l.353-386); `ASSISTANT_IDS_KEY = "assistant_ids"`.
- `src/codemie/rest_api/models/base.py` — `PydanticListType.process_result_value` (l.336-352) skips string items, so a jsonb-string column reads as `[]`; `get_by_fields` / `get_all_by_fields` (l.463-493) do exact `==` matching.
- `src/codemie_tools/base/models.py:113` — `CredentialTypes.MS_TEAMS = "MSTeams"` (Python value); `SettingType.PROJECT = "project"`, `USER = "user"`.
- `src/codemie/service/settings/settings_request_validator.py:316-460` — `validate_ms_teams_request`: feature flag `teamsBotIntegration`; PROJECT scope requires `project_name` equal to existing row's; exactly one `assistant_ids` entry, non-empty list of unique strings; each id must `AssistantService.belongs_to_project(id, project_name)` or be a marketplace assistant; then singleton check.
- `src/codemie/service/settings/settings.py:388, 471` — `create_setting` / `update_settings` call `check_alias_unique` first (update passes `setting_id`).
- `src/codemie/service/settings/settings_transfer_service.py:200-235` — `_validate_aliases_present` (422 "Integration without an alias") and `_validate_no_collisions` (reuses `check_alias_unique` against the target project); `_is_subsystem_managed` (l.64) treats `ENFORCE_MEMBER_SPEND_LIMITS_ALIAS` and `SUBSYSTEM_MANAGED_PREFIXES` aliases specially. (The ticket's `_validate_no_blank_aliases` name does not exist; the method is `_validate_aliases_present`.)
- Prune callers: `src/codemie/rest_api/models/assistant.py:1155` (on delete), `src/codemie/service/assistant/assistant_repository.py:243-265` (on project move).
- Routers: `src/codemie/rest_api/routers/project_settings.py:113-114, 175-176` and `user_settings.py:176-177, 219-220` dispatch to `validate_ms_teams_request`; generic exception → 422 "Cannot update specified setting".

### Architecture and Layers Affected
- Data / migration layer: `src/external/alembic/versions/` (new revision + edit of `c3d4e5f6a7b8`). Migrations run automatically at app startup via `command.upgrade(alembic_cfg, "head")` in `src/codemie/clients/postgres.py:216-218`; `env.py` takes `LOCK TABLE alembic_version IN ACCESS EXCLUSIVE MODE` and sets `search_path` to the codemie schema.
- Model/service layers (read only for this task): Settings model, settings service, request validator, transfer service.

### Integration Points
- UI (`repos/codemie-ui/src/pages/integrations/components/EditSettingPageBase.tsx:106-108`) shows "Missing required parameters" when `project_name`, `credential_type` or `alias` query param is empty, then looks up via `findProjectSetting(project, type, alias)`. UI default alias = `generateDefaultAlias(type)` = `<typeSlug>-<date>` (`codemie-ui/src/utils/settings.ts:235-241`), producing e.g. `msteams-2026-10-14_14-27`.
- Teams bot `repos/codemie-ms-teams-bot/src/service/assistants/manager.py:195-218` lists integrations filtered by `type=MSTeams` (+ project) and flattens every `assistant_ids` credential value.

### Patterns and Conventions
- Alembic: single linear chain, **one head today: `y1z2a3b4c5d6`** (`y1z2a3b4c5d6_add_agent_workspace_folder_owners.py`, down_revision `a8f23cf09c37`), matching local `codemie.alembic_version`. No "13 heads" state exists now; tests enforce a single head (`tests/codemie/migrations/test_add_agent_workspace_folder_owners.py:126`, `test_credentialtypes_enum_migrations.py:89`, `tests/external/alembic/test_add_ms_exchange_credential_type.py:85`).
- Chain order around MS Teams: `8b2c1a4d5e6f → c3d4e5f6a7b8 → f1a2b3c4d5e6 → … → fa14587c0de1 → 9c1d2e3f4a5b → a8032409ff23 → … → 5189d4d2afaf (MS_EXCHANGE enum sync) → a8f23cf09c37 → y1z2a3b4c5d6`.
- Data-migration style: `op.get_bind().execute(sa.text(...))`, module `logger = logging.getLogger(__name__)`, info/warning logs with counts (e.g. `d9e8f7a6b5c4`: `"Deleted 3 ZEPHYR_SQUAD settings rows (0 expected)"`), raw SQL with `codemie.` schema prefix and enum literals as names (`'MS_TEAMS'`, `'PROJECT'`). Pure transform helpers extracted at module level for unit testing (`c3d4e5f6a7b8`, `k5l6m7n8o9p0`).
- Migration files are excluded from license-header checks (`scripts/license_headers/check_license_headers.py:180`), though recent ones carry the header.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `src/external/alembic/README.MD` — workflow (`alembic revision`, upgrade/downgrade/upgrade test), multiple-heads resolution (re-point unpublished `down_revision`; `alembic merge` for published), data-migration example via `op.get_bind()`, "Changing migration history on published branches should be avoided unless absolutely necessary."
- `.ai-run/guides/data/database-patterns.md:14-23` — migrations go in `src/external/alembic/versions/`; "Inspect Alembic state before changing migration chains".
- Prior task docs: `docs/superpowers/tasks/2026-08-27-teams-integ-2-migration/` (spec, analysis), `2026-08-31-implement-option-4b-for-routing-teams-bot-group-ch/code-review-final.json` (contains CR-008).

### Architectural Decisions
- `c3d4e5f6a7b8` downgrade is intentionally non-destructive (comment l.156-163).
- `a8032409ff23` docstring: index scoped to `PROJECT` because USER-scope MS Teams rows also carry `project_name`; uses IF EXISTS because some deployments reached it without running `f1a2b3c4d5e6`.
- `prune_ms_teams_assistant_id` docstring: assistant_ids has no FK/cascade; must be pruned on delete/move.

### Derived Conventions
- Raw-SQL enum comparisons use DB labels (member names); Python-side queries use `.value` and rely on SQLAlchemy Enum lookup.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/migrations/test_c3d4e5f6a7b8_migrate_assistant_project_mapping.py` — only `_group_assistant_ids` / `_merge_assistant_ids`; `upgrade()` SQL/bind behavior untested (which is how Defect B shipped).
- `tests/codemie/migrations/test_credentialtypes_enum_migrations.py` — loads revisions via `ScriptDirectory`, patches module `op` with `MagicMock`, asserts SQL text of `op.get_bind().execute` calls, call order and `caplog` log records; single-head assertion.
- `tests/codemie/migrations/test_add_agent_workspace_folder_owners.py` — `_OpRecorder` fake `op`.
- `tests/codemie/rest_api/models/test_settings_model.py` — `check_ms_teams_exist*` with patched `get_by_fields`; `tests/codemie/rest_api/routers/test_project_settings.py`, `test_user_settings*.py`, `tests/codemie/service/assistant/test_assistant_repository.py` cover MS Teams API/prune paths.

### Testing Framework and Patterns
- pytest; `unittest.mock`; migration modules imported as `external.alembic.versions.<file>`.
- **No DB-backed migration harness** exists: every migration test mocks `op`. Real-DB verification is manual against the local podman Postgres.

### Coverage Gaps
- No test exercises JSONB binding of `credential_values` in any migration.
- No test for alias presence on migrated MS Teams rows, or for reading a string-typed `credential_values`.

---

## 5. Configuration and Environment

### Environment Variables
- `DEFAULT_DB_SCHEMA` (codemie), `ALEMBIC_INI_PATH`, `ALEMBIC_MIGRATIONS_DIR` via `codemie.configs.config`.

### Configuration Files
- `src/external/alembic/alembic.ini`, `env.py`.

### Feature Flags and Deployment Concerns
- `customer_config` feature `teamsBotIntegration` gates `validate_ms_teams_request` (403 when off); migrations are not gated.
- Migrations auto-apply on startup under an exclusive lock on `alembic_version`.
- **Local DB state (verified 2026-10-15)**: `codemie.alembic_version = y1z2a3b4c5d6`; enum labels `MS_TEAMS`, `PROJECT`, `USER` exist (so the `'MS_TEAMS'` index predicate matches the stored label, not the Python value `MSTeams`). **Neither `uix_settings_project_ms_teams_singleton` nor `uix_settings_user_ms_teams_singleton` exists locally**, despite being at head; the cause is unknown. Settings indexes present: pkey, gin trgm on alias/project_name, btree on date/user_id/credential_type. Columns: `alias varchar NULL`, `user_id NULL`, `created_by jsonb NULL`, `credential_values jsonb NULL`, `setting_hash NULL`.
- Local MS_TEAMS rows: 4 broken (demo, pr-1, user1@example.com, user2@example.com — **no `codemie` row exists**, unlike the ticket's list), all `jsonb_typeof='string'` with NULL alias/user_id/created_by, plus 1 healthy row (admin@codemie.ai, alias `msteams-2026-10-15_10-08`, array with `alias` + `assistant_ids` entries). The SQL `(credential_values #>> '{}')::jsonb` decodes the strings to arrays (verified). All 5 recovered assistant ids exist but belong to project `codemie`, not to the rows' projects.

---

## 6. Risk Indicators

- **Stale or foreign recovered ids break edit (AC1).** `prune_ms_teams_assistant_id` read `[]` from broken rows, so deletes and moves since 2026-09-07 were never pruned from the encoded strings. On save, `validate_ms_teams_request` rejects any id that is not project-owned and not on the marketplace (400 "Invalid assistant_ids"). Locally, every recovered id fails this rule. Speculative: the repair needs a policy for such ids (drop, keep, or rely on the UI letting the user deselect them).
- **Teams bot behavior change.** After the repair, migrated projects return non-empty assistant_ids to the bot again, including possibly deleted assistants (the bot drops fetch failures via `_gather_skipping_errors`).
- **Alias collision rule.** `check_alias_unique` (PROJECT) matches project_name + alias across all credential types and scopes, using exact `==`. Speculative: a backfilled alias must be unique per project, deterministic, and must avoid `SUBSYSTEM_MANAGED_PREFIXES`/`ENFORCE_MEMBER_SPEND_LIMITS_ALIAS` so transfer does not treat it as subsystem-managed.
- **Editing a published migration.** `c3d4e5f6a7b8` has already run on prod. Changing it only affects environments that have not run it, and the README discourages history edits. Its merge path also writes double-encoded data into pre-existing rows.
- **Merge-path ambiguity.** `c3d4e5f6a7b8` lookup lacks a `setting_type` filter. It runs before USER-scope rows existed, but this matters if the corrected file runs on a DB at an unusual state.
- **Singleton index uncertainty.** The index is absent locally, and prod presence is not verified. The repair does not change project_name/credential_type/setting_type, so the index should not block it. Duplicate PROJECT MS_TEAMS rows per project could exist where the index is missing.
- **Mixed-shape rows.** A merge-path row could hold a string that decodes to an array that also has non-assistant_ids entries. Speculative: decoding must handle any array, and the repair must leave already-array rows untouched (idempotency).
- **Downgrade semantics.** Re-encoding to strings would re-break data. Speculative: a no-op or log-only downgrade fits the precedent of `c3d4e5f6a7b8`.
- **Testing.** There is no DB migration harness, so SQL correctness (jsonb casting) can only be unit-asserted on SQL text plus a manual run on local podman. Defect B escaped this way.
- `user_id`/`created_by` stay NULL for PROJECT rows. Whether ownership checks (`ProjectSetting`) need them was not verified.

---

## 7. Summary for Complexity Assessment

The change is in the data/migration layer only: one new Alembic data-repair revision chained onto the single current head `y1z2a3b4c5d6`, plus a corrective edit to the already-published `c3d4e5f6a7b8` (stop double-encoding JSONB and set an alias). It reads no new application code. Behavior depends on existing rules in `Settings.check_alias_unique` (project-wide alias uniqueness), `PydanticListType` (string → `[]`), `validate_ms_teams_request` (ids must be project-owned or on the marketplace) and the Teams bot's flattening of `assistant_ids`. The DB stores the enum label `MS_TEAMS` (the Python value is `MSTeams`), so raw SQL must use `'MS_TEAMS'`/`'PROJECT'`. Decoding is a single SQL expression, `(credential_values #>> '{}')::jsonb`, verified locally.

The technique is not new: the repo has several raw-SQL data migrations with logging and mocked-`op` unit tests. The novel parts are generating deterministic, collision-free aliases and deciding what to do with recovered assistant ids that are stale or not owned by the project. Because broken rows read as `[]`, prune never touched them, and the local data shows recovered ids that would fail edit validation.

Tests only cover pure helpers and mocked-`op` SQL text. No DB-backed migration harness exists, which is how Defect B shipped. Key risks: AC1 can still fail after the repair because of foreign or stale ids; a behavior change for the Teams bot; the semantics of editing a published migration; and unverified singleton-index presence on prod (the index is missing locally).

---

## 8. External References

- `docs/superpowers/tasks/2026-08-27-teams-integ-2-migration/spec.md` (resolved, lines 25-40): "insert one `settings` row: `id=str(uuid4())`, `date=update_date=utcnow()`, `project_name=<group>`, `credential_type='ms_teams'`, `setting_type='PROJECT'`, `is_global=False`, `default=False`, `user_id=None`, `alias=None`, `created_by=None`, `credential_values` serialized as … `[{"key": "assistant_ids", "value": ["<assistant_id>", ...]}]`". The spec also said to skip (not merge) when a row exists; the shipped code merges instead.
- `repos/codemie-ms-teams-bot/src/service/assistants/manager.py:195-218` (resolved): `_list_integration_assistant_ids` calls `integrations.list(setting_type, filters={"type": "MSTeams", "project": project}, per_page=1000)` and returns `[aid for integration … for credential in integration.credential_values if credential.key == "assistant_ids" for aid in credential.value]`.
- `repos/codemie-ui/src/pages/integrations/components/EditSettingPageBase.tsx:106-108` (resolved): `if (!query.project_name || !query.credential_type || !query.alias) { toaster.error('Missing required parameters') … }`.
- `2026-08-31-implement-option-4b-for-routing-teams-bot-group-ch/code-review-final.json` contains CR-008 (located, not re-read).
