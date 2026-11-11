# EPMCDME-15577 Orphan user_projects Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A one-time, irreversible Alembic data migration deletes `user_projects` rows whose `project_name` matches no `applications` row (id or name, any casing). A regression test also pins that project deletion leaves no membership rows.

**Architecture:** One new migration after head `e5f9a1c3b7d2`, modeled on `src/external/alembic/versions/e5f9a1c3b7d2_repair_ms_teams_migrated_settings.py` (license header, typed revision header, module `logger`, `op.get_bind()` + `sa.text`, schema-qualified `codemie.<table>`, summary log line, log-only downgrade). SQL computes each row's match flags, a pure Python helper classifies the row, and one guarded `DELETE` removes the orphans. No application code changes.

**Tech Stack:** Python, Alembic, SQLAlchemy `sa.text`, PostgreSQL, pytest.

**Spec:** `docs/superpowers/tasks/2026-10-16-epmcdme-15577-orphan-user-projects/spec.md`

Commit per task using the repository's existing convention (`EPMCDME-15577: <summary>`). Run tests with `poetry run pytest <path> -v` from the repo root.

## Global Constraints

- Orphan = `project_name` matches no `applications.id` and no `applications.name`, compared case-insensitively. Only orphans are deleted.
- Kept: exact match, case-only match, and a match on a soft-deleted application (`deleted_at` set). The SQL must **not** filter on `deleted_at`.
- Personal projects (name = email) get no special treatment. Do not exclude them.
- Log per-class counts (scanned, exact kept, case-only kept, orphans deleted) and one line per deleted row with `user_id`, `project_name`, `is_default`.
- Idempotent: a second run deletes nothing. The downgrade changes no data and only logs.
- No backup table, no ops script, no FK, no changes under `src/codemie/`, no UI changes.

## Review Focus

1. An application whose `name` is NULL: `x IN (subquery)` returns NULL, not false, when the set holds a NULL, so every row would be misclassified. The name subquery must carry `WHERE name IS NOT NULL`. (Task 1 test: SQL text check.)
2. An application created between the SELECT and the DELETE (concurrent pod start): its members must survive, so the DELETE re-checks the no-match condition. (Task 1 test.)
3. An orphan with `is_default = true` is deleted and logged with `is_default=True`. (Task 1 test.)
4. Project names containing `_` or `%`: matching uses equality on `LOWER(...)`, never `LIKE`/`ILIKE`. (Task 1 test: SQL text check.)
5. An empty table, or no orphans (the second run): no DELETE is issued and the summary line still logs zeros. (Task 1 test.)

---

### Task 1: Orphan user_projects cleanup migration

Test-first: yes — `test_upgrade_deletes_only_orphans_and_logs_each` fails with `ModuleNotFoundError` for `external.alembic.versions.f7c3a9d1e4b6_delete_orphan_user_projects`.

**Files:**
- Create: `src/external/alembic/versions/f7c3a9d1e4b6_delete_orphan_user_projects.py`
- Test: `tests/codemie/migrations/test_f7c3a9d1e4b6_delete_orphan_user_projects.py`

**Interfaces:**
- Produces: `revision = "f7c3a9d1e4b6"`, `down_revision = "e5f9a1c3b7d2"`; `_classify(exact_match: bool, any_case_match: bool) -> str` returning `"exact"`, `"case_only"` or `"orphan"`; `_SELECT_ROWS`, `_DELETE_ORPHANS` (`sa.text`); `upgrade()`, `downgrade()`.

- [ ] **Step 1: Write the failing tests.** Copy the harness shape of `tests/codemie/migrations/test_e5f9a1c3b7d2_repair_ms_teams_migrated_settings.py:33-36,172-225` (`_module()`, a `_ScriptedBind` recording `(statement, sql, params)`, `_run(fn_name, bind)` patching `op`, and `_messages(caplog, level)`). The bind returns the scripted rows for `SELECT`. For `DELETE` it returns a result with `rowcount = len(params["ids"])`. Rows are `(id, user_id, project_name, is_default, exact_match, any_case_match)`. Tests:
  - `test_classify` (parametrized): `(True, True) -> "exact"`, `(False, True) -> "case_only"`, `(False, False) -> "orphan"`.
  - `test_upgrade_deletes_only_orphans_and_logs_each`: rows `exact` (`"alpha"`, T, T), `case_only` (`"MyTeam"`, F, T), `orphan1` (`"gone"`, `is_default=True`, F, F) and `orphan2` (`"old@x.com"`, F, F). Expect exactly one DELETE with `params["ids"] == ["orphan1", "orphan2"]`, and two INFO lines containing `'gone'`/`True` and `'old@x.com'`/`False` plus their user ids.
  - `test_upgrade_logs_one_summary_line`: the same rows give exactly one INFO message equal to `"Orphan user_projects cleanup: scanned=4 exact_kept=1 case_only_kept=1 orphans_deleted=2"`.
  - `test_upgrade_without_orphans_issues_no_delete` (the second-run case, AC4): rows that are only exact or case-only (and, separately, `[]`) produce no DELETE call, and the summary shows `orphans_deleted=0`.
  - `test_select_matches_id_and_name_case_insensitively`: the normalized `_SELECT_ROWS` text contains `LOWER(`, `codemie.applications` and `name IS NOT NULL`, and contains neither `deleted_at` nor `LIKE`.
  - `test_delete_rechecks_no_match`: the normalized `_DELETE_ORPHANS` text contains `id = ANY(:ids)`, `NOT IN` and `name IS NOT NULL`, and does not contain `deleted_at`.
  - `test_downgrade_changes_no_data`: as in the reference test (no bind calls, `op.execute` not called), plus one INFO message containing `"intentionally not reverted"`.
  - `test_cleanup_revision_is_in_the_single_alembic_head_chain`: as in reference L366-372, with `"f7c3a9d1e4b6"` and `down_revision == "e5f9a1c3b7d2"`.

- [ ] **Step 2: Run** `poetry run pytest tests/codemie/migrations/test_f7c3a9d1e4b6_delete_orphan_user_projects.py -v`. Expected: FAIL, module not found.

- [ ] **Step 3: Implement the migration.** Header and imports follow the reference (lines 1-42), with a docstring summarising the cleanup and `Create Date: 2026-10-16 00:00:00.000000`. Drop the JSONB, json and datetime imports. The SQL, exactly:

```python
_SELECT_ROWS = sa.text(
    "SELECT up.id, up.user_id, up.project_name, up.is_default, "
    "up.project_name IN (SELECT id FROM codemie.applications "
    "UNION SELECT name FROM codemie.applications WHERE name IS NOT NULL) AS exact_match, "
    "LOWER(up.project_name) IN (SELECT LOWER(id) FROM codemie.applications "
    "UNION SELECT LOWER(name) FROM codemie.applications WHERE name IS NOT NULL) AS any_case_match "
    "FROM codemie.user_projects up ORDER BY up.project_name, up.user_id"
)
_DELETE_ORPHANS = sa.text(
    "DELETE FROM codemie.user_projects up WHERE up.id = ANY(:ids) "
    "AND LOWER(up.project_name) NOT IN (SELECT LOWER(id) FROM codemie.applications "
    "UNION SELECT LOWER(name) FROM codemie.applications WHERE name IS NOT NULL)"
)
```

  `upgrade()`: fetch the rows, classify each one and count `exact` and `case_only`. For each orphan, log INFO `"Deleting orphan user_projects row: user_id=%r project_name=%r is_default=%r"` and collect its id. If there are any ids, execute `_DELETE_ORPHANS` with `{"ids": ids}` and take `rowcount` as `orphans_deleted` (otherwise 0). Then log the summary line exactly as in the Step 1 test. `downgrade()`: a comment plus `logger.info("Orphan user_projects cleanup is intentionally not reverted; no data changed.")`.

- [ ] **Step 4: Run** the Step 2 command plus `tests/codemie/migrations/test_e5f9a1c3b7d2_repair_ms_teams_migrated_settings.py`. Expected: all PASS (there is still a single head).

- [ ] **Step 5: Commit.**

### Task 2: Regression test that project deletion leaves no membership row

Test-first: no — the behavior shipped in EPMCDME-14501. This test pins it (AC6) and passes immediately.

**Files:**
- Modify: `tests/codemie/service/project/test_project_service_delete_update.py:525-545`

- [ ] **Step 1: Strengthen `test_delete_cleans_up_creator_membership`, run it and commit.** Add a docstring naming EPMCDME-15577 AC6: "deleting a project removes every `user_projects` row for it: the creator's row is removed, and any other row blocks deletion with 409". Add `mock_app_repo.delete_by_name.assert_called_once_with(mock_session, "my-project")`. Also assert that the set of `(user_id, project_name)` passed to `mock_upr.remove_project` equals the set of `(record.user_id, "my-project")` from `get_by_project_name`. Run `poetry run pytest tests/codemie/service/project/test_project_service_delete_update.py -v`. Expected: PASS. Commit.
