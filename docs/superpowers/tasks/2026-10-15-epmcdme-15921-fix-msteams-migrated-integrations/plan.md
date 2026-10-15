# EPMCDME-15921 MS Teams Settings Repair Implementation Plan

> **For agentic workers:** Implement task-by-task; steps use checkbox (`- [ ]`) syntax.

**Goal:** Repair MS_TEAMS settings rows corrupted by `c3d4e5f6a7b8` (jsonb-string `credential_values`, NULL alias) and correct that migration for environments that have not run it.

**Architecture:** New Alembic data-repair revision `e5f9a1c3b7d2` on head `y1z2a3b4c5d6`: raw SQL via `op.get_bind()`, pure module-level helpers, row-by-row writes, no-op downgrade. In-place fix of `c3d4e5f6a7b8`.

**Tech Stack:** Alembic, SQLAlchemy `sa.text`, PostgreSQL jsonb, pytest with mocked `op`.

**Spec:** `docs/superpowers/tasks/2026-10-15-epmcdme-15921-fix-msteams-migrated-integrations/spec.md` (analysis: `technical-analysis.md`, same dir).

Commit per task using the repository's existing convention. Tests run from repo root: `.venv/Scripts/python.exe -m pytest <path> -v`. Spec AC8 (UI edit/save) is owned by the flow's later verification stage.

## Global Constraints

- Raw SQL uses DB enum labels `'MS_TEAMS'`, `'PROJECT'`, `'USER'` and the `codemie.` schema prefix.
- `credential_values` is written by binding a Python **list** to `sa.bindparam("credential_values", type_=JSONB)`. Never `json.dumps` into that bind.
- Alias format `msteams-<date:%Y-%m-%d_%H-%M>`, collisions get `-2`, `-3`, … Uniqueness scope (any credential type): PROJECT = `project_name`; USER = `project_name` + `user_id`.
- Never call application services from migrations. Never touch `user_id`, `created_by`, `project_name`, `credential_type`, `setting_type`, `date`, `update_date`. Never delete rows. Never add a `{"key":"alias"}` entry.
- Migration files stay self-contained: each carries its own `_build_alias` (no cross-revision imports).

## Review Focus

1. Defect B recurring: a write binding a `str` instead of a `list` (Tasks 1, 3 assert bound type).
2. String `credential_values` with an existing alias: decode only, alias untouched (Task 3).
3. Array `credential_values` with NULL/empty alias: alias only, `credential_values` not written (Task 3).
4. Decoded array with no `assistant_ids` entry or a non-list value: ends with `{"key":"assistant_ids","value":[]}` (Task 2).
5. Alias colliding with another credential type's alias in the project, or NULL `date`: `-2` suffix / run-time date (Task 2).

---

### Task 1: Correct `c3d4e5f6a7b8` (array bind + alias on insert)

**Files:**
- Modify: `src/external/alembic/versions/c3d4e5f6a7b8_migrate_assistant_project_mapping_to_settings.py:83-132`
- Test: `tests/codemie/migrations/test_c3d4e5f6a7b8_migrate_assistant_project_mapping.py`

**Interfaces:** Produces `_build_alias(row_date: datetime | None, taken: set[str], now: datetime) -> str` in this module.

Test-first: yes — `upgrade()` with mocked `op` binds `credential_values` as a `list` (today a `str`) on insert and merge paths, and inserts a non-NULL `msteams-…` alias.

- [ ] **Step 1: Write failing tests.** Patch module `op` with `MagicMock` (pattern: `test_credentialtypes_enum_migrations.py:45-55`); script `bind.execute` so one project inserts and one merges. Assert every INSERT/UPDATE `credential_values` param is a `list`; INSERT `alias` matches `^msteams-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}(-\d+)?$`; when the project's alias query returns the base alias, the inserted one ends `-2`. Pure `_build_alias` tests: NULL date uses `now`; `taken` holding base and `-2` yields `-3`. Existing helper tests unchanged.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** Pass `merged` (l.119) and the insert list (l.130) directly; drop `import json`. Replace the INSERT's literal alias `NULL` (l.89) with `:alias`; before each insert select the project's non-NULL aliases and pass `_build_alias(None, taken, now)`. No id filtering here.
- [ ] **Step 4: Run, expect PASS.**

### Task 2: Repair revision — pure helpers

**Files:**
- Create: `src/external/alembic/versions/e5f9a1c3b7d2_repair_ms_teams_migrated_settings.py` (license header, `revision="e5f9a1c3b7d2"`, `down_revision="y1z2a3b4c5d6"`, module `logger`)
- Test: `tests/codemie/migrations/test_e5f9a1c3b7d2_repair_ms_teams_migrated_settings.py`

**Interfaces (Produces):**
```python
def _decode_credential_values(raw: object) -> list[dict] | None: ...
    # str -> json.loads; list -> as-is; invalid JSON or non-array -> None
def _filter_assistant_ids(
    credential_values: list[dict], project_name: str,
    assistants: dict[str, tuple[str | None, bool | None]],  # id -> (project, is_global)
) -> tuple[list[dict], list[tuple[object, str]]]: ...
    # (new values, [(dropped_id, reason)]), reason in {"deleted","foreign","invalid"}
def _build_alias(row_date: datetime | None, taken: set[str], now: datetime) -> str: ...
```

Test-first: yes — helper tests fail with ImportError before the module exists.

- [ ] **Step 1: Write failing tests.** `_decode_credential_values`: JSON-string array decodes; list passes through; `'"x"'`, `'{}'`, `'not json'` → `None`. `_filter_assistant_ids` (project `p1`, assistants `{"own":("p1",False),"glob":("p2",True),"for":("p2",False)}`): ids `["own","glob","for","gone",5,"own"]` → value `["own","glob"]`, dropped `[("for","foreign"),("gone","deleted"),(5,"invalid"),("own","invalid")]`; other entries preserved; all dropped → `[]` entry kept; missing entry or non-list value → `[]` entry present. `_build_alias`: `datetime(2026,9,7,8,0,45)` → `msteams-2026-09-07_08-00`; collision → `-2`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement the three helpers** (`_build_alias` copied from Task 1).
- [ ] **Step 4: Run, expect PASS.**

### Task 3: Repair revision — `upgrade()` / `downgrade()`

**Files:** Modify the Task 2 revision and test file.

**Interfaces:** Consumes Task 2 helpers.

Test-first: yes — mocked-`op` tests for target SQL, written values, logs and the no-op downgrade fail before `upgrade`/`downgrade` exist.

- [ ] **Step 1: Write failing tests** (mocked `op`, scripted `bind.execute`, `caplog`):
  - select SQL contains `credential_type = 'MS_TEAMS'`, `jsonb_typeof(credential_values) = 'string'` and `alias IS NULL OR alias = ''`.
  - string row + NULL alias → one `credential_values` UPDATE (bound `list`, filtered) and one alias UPDATE; each drop logged INFO with row id, project, reason.
  - string row with alias → no alias UPDATE. Array row with NULL alias → alias UPDATE only, no assistants lookup.
  - non-array decode → WARNING, no UPDATE, next row still processed.
  - two NULL-alias rows of one project with equal `date` → second gets `-2`; USER row's alias query filters on `user_id`.
  - one summary INFO line: scanned, decoded, aliased, ids dropped, skipped.
  - `downgrade()` makes no `execute` call; `ScriptDirectory(...).get_heads() == ["e5f9a1c3b7d2"]`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** `upgrade()`: select `id, project_name, setting_type, user_id, alias, date, credential_values` of targeted rows `ORDER BY date NULLS LAST, id`. String rows: decode, `SELECT id, project, is_global FROM codemie.assistants WHERE id = ANY(:ids)`, filter, `UPDATE codemie.settings SET credential_values = :credential_values WHERE id = :id`. NULL/empty-alias rows: taken = DB aliases in scope (`project_name = :p`, plus `user_id IS NOT DISTINCT FROM :u` for `'USER'`) ∪ aliases assigned earlier in this run for that scope; `UPDATE … SET alias = :alias WHERE id = :id`. `downgrade()`: log that the repair is intentionally not reverted.
- [ ] **Step 4: Run all `tests/codemie/migrations/`, expect PASS.**

### Task 4: Local DB verification of the repair

Test-first: no — manual migration evidence on local podman Postgres (at `y1z2a3b4c5d6`).

- [ ] Seed the keep-path row below via `podman exec -i postgres psql -U postgres -d postgres`, snapshot `SELECT id, project_name, alias, jsonb_typeof(credential_values), credential_values FROM codemie.settings WHERE credential_type='MS_TEAMS' ORDER BY project_name;`, run `../../../.venv/Scripts/python.exe -m alembic upgrade head` from `src/external/alembic` with the local backend DB env, snapshot, then `alembic downgrade -1` + `alembic upgrade head`, snapshot. Expect: every row `array` with a `msteams-…` alias; the 4 foreign rows end `assistant_ids: []` with INFO `foreign` logs; the `codemie` row keeps `a28cd98b-…`; `admin@codemie.ai` identical in all snapshots; third snapshot equals second. Report snapshots and log lines.

```sql
INSERT INTO codemie.settings (id, date, update_date, project_name, credential_type, setting_type,
  is_global, "default", user_id, alias, created_by, credential_values)
VALUES ('epmcdme-15921-keep', now(), now(), 'codemie', 'MS_TEAMS', 'PROJECT', false, false, NULL, NULL, NULL,
  to_jsonb('[{"key": "assistant_ids", "value": ["a28cd98b-9150-4536-b96e-d3dae10ff576"]}]'::text));
```
