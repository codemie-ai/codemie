# Database Patterns

## SQLModel And Sessions

Use existing SQLModel/session helpers and keep transaction boundaries visible.

| Avoid | Prefer |
|---|---|
| Raw SQL string interpolation | SQLModel/SQLAlchemy expressions and parameters |
| Long-lived sessions in global state | Scoped sessions through existing clients/helpers |

Evidence: SQLModel and sessions are used in base API models at `src/codemie/rest_api/models/base.py:27`; PostgreSQL dependencies are declared at `pyproject.toml:56`.

## Migrations

Use Alembic for schema changes and keep migration files under the existing external Alembic tree.

| Avoid | Prefer |
|---|---|
| Runtime schema creation for persistent tables | Alembic migration in `src/external/alembic/versions/` |
| Hand-editing migration history without checking heads | Inspect Alembic state before changing migration chains |

Evidence: README directs migrations through `src/external/alembic` at `README.md:90`.
