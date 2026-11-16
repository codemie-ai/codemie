DONE

## Summary

Implemented Task 3 of the 11-task plan: the Alembic migration that creates the
`llm_model_status` table backing the `LLMModelStatus` SQLModel added in Task 2.

## Files changed

- Created `src/external/alembic/versions/854f0f549445_create_llm_model_status_table.py`

No other files were touched. `config/customer/customer-config.yaml`,
`.ai-run/sdlc-factory/doctor.json`, and `.codemie-repos/` (all pre-existing
dirty/untracked state) were left untouched and unstaged.

## Chosen revision id

`854f0f549445` (generated via `python3 -c "import uuid; print(uuid.uuid4().hex[:12])"`,
then verified with `ls src/external/alembic/versions/ | grep -i 854f0f549445`
that no existing file used it).

`down_revision = "c4d5e6f7a8b9"`, per the controller-provided current head
(`c4d5e6f7a8b9_add_soft_limit_notification_enabled.py`). I did not re-run
`alembic heads` (confirmed broken in this env due to the pre-existing
`tree_sitter_languages` import issue in `env.py`, unrelated to this change).

## Branch/head sanity check

```
grep -rl "c4d5e6f7a8b9" src/external/alembic/versions/ | grep -v "854f0f549445_create_llm_model_status_table.py"
```
Output: only `c4d5e6f7a8b9_add_soft_limit_notification_enabled.py` (the file that
defines that revision itself). No other pre-existing file branches off
`c4d5e6f7a8b9`, so this migration is the sole new head — no branch conflict.

## Schema mirrored (from Task 2's `LLMModelStatus` model)

- `id` — `postgresql.UUID(as_uuid=True)`, primary key
- `base_name` — `sa.String(length=255)`, `nullable=False`, unique + indexed
- `status` — `sa.String(length=20)`, `nullable=False`, `server_default="enabled"`
- `updated_by_id` — `sa.String(length=255)`, nullable
- `updated_by_name` — `sa.String(length=255)`, nullable
- `created_at` — `sa.TIMESTAMP(timezone=True)`, `nullable=False`, `server_default=sa.func.now()`
- `updated_at` — `sa.TIMESTAMP(timezone=True)`, nullable
- `sa.PrimaryKeyConstraint("id")`, `sa.UniqueConstraint("base_name")`
- Separate `op.create_index("ix_llm_model_status_base_name", "llm_model_status", ["base_name"], unique=True)` for the `index=True` on the SQLModel field
- `downgrade()` drops the index first, then the table

## Idioms chosen (for easy reconsideration)

- **UUID column type**: `sqlalchemy.dialects.postgresql.UUID(as_uuid=True)`. Chosen
  because it's the established convention in this repo's migrations — confirmed by
  grepping `src/external/alembic/versions/*.py` for `postgresql.UUID`, which
  surfaced `e1f2a3b4c5d6_add_cost_centers.py` and
  `f1e2d3c4b5a6_create_project_cost_tracking.py`, both using
  `postgresql.UUID(as_uuid=True)` for UUID PK/FK columns (via
  `from sqlalchemy.dialects import postgresql`). No files in this migration set
  use `sqlmodel.sql.sqltypes.GUID()`, so I did not consider that alternative
  further.
- **Unique + indexed column idiom**: a plain `op.create_index(name, table, [cols], unique=True)`
  call after `create_table`, following the same two reference files (which use
  `op.create_index("ix_cost_centers_deleted_at", ...)`,
  `op.create_index("ux_cost_centers_name_lower", ..., unique=True)`, and three
  `op.create_index(...)` calls in the cost-tracking migration) rather than an
  inline `sa.Index(...)` inside `create_table`. Index name follows the
  SQLModel-generated-style convention `ix_<table>_<column>` used elsewhere
  (e.g. `ix_applications_cost_center_id`, `ix_project_cost_tracking_project_name`).

## Validation performed (no test — schema migration, per plan's "Test-first: no")

```
poetry run python -c "import ast; ast.parse(open('src/external/alembic/versions/854f0f549445_create_llm_model_status_table.py').read())"
```
Result: no exception raised (printed `SYNTAX_OK` marker) — file parses as valid Python.

`poetry run alembic -c src/external/alembic/alembic.ini heads` was intentionally
NOT run, per the controller's note that it fails in this local env with
`ModuleNotFoundError: No module named 'tree_sitter_languages'` — a pre-existing,
unrelated environment issue.

## Commit

`9f9cb9dec` — "EPMCDME-15080: Add llm_model_status Alembic migration"

`git show --stat HEAD` confirms exactly one file changed:
`src/external/alembic/versions/854f0f549445_create_llm_model_status_table.py`
(45 insertions).

## Self-review notes

- Column order, types, nullability, and default values match Task 2's
  `LLMModelStatus` SQLModel exactly.
- `status` server_default is the string `'enabled'`, matching the SQLModel field
  default `"enabled"`.
- `updated_at` has no `onupdate` equivalent at the migration/DDL level (Alembic
  migrations don't encode ORM-level `onupdate` callables — that behavior lives
  in the SQLModel/SQLAlchemy layer from Task 2, not in the schema DDL), so the
  column is simply nullable with no server-side default/trigger. This matches
  how other migrations in this repo handle SQLAlchemy-level `onupdate=func.now()`
  columns (e.g. `update_date` in `add_cost_centers.py` uses a server_default but
  not a server-side trigger for updates either) — i.e., "update on change" is
  handled by the application/ORM, not the database schema.
- Did not touch any other existing migration file, per ownership scope.
- No second head or branch conflict found off `c4d5e6f7a8b9`.
