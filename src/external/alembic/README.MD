# Alembic Migrations Developer Guide

## What is Alembic?

Alembic is a database migration tool for SQLAlchemy that helps manage changes to your database schema over time. It tracks changes to your database models and creates migration scripts that can be applied or rolled back as needed.

### When to Create Migrations

You MUST create a new migration when you:
1. Create a new table model (new SQLModel class). If model is added to a new file, you must add an import to `src/external/alembic/env.py`
2. Modify an existing table model (add/remove/modify fields)
3. Change any ENUMs that are used as field types in table models
4. Add or modify table relationships
5. Change any constraints, indexes, or other database-level properties

## Prerequisites

Alembic's autogenerate functionality works by comparing your local database schema with your SQLModel code definitions. Therefore, you must have a local PostgreSQL database instance running.

## Understanding Migrations

### Autogenerate Feature

Alembic's autogenerate feature compares your SQLModel definitions with the current database schema and automatically generates appropriate migration scripts. In most cases, autogenerate will correctly handle:
- Creating new tables
- Adding/removing columns
- Changing column types
- Adding/removing indexes
- Modifying constraints

This means for typical schema changes, you only need to:
1. Update your SQLModel class definitions
2. Run `alembic revision --autogenerate -m "description"`
3. Review the generated migration
4. Apply it with `alembic upgrade head`

### Custom Migrations

While autogenerate handles most cases, you might need to write custom migrations for:
- Complex data transformations
- Splitting or merging tables
- Adding computed values to existing records
- Custom SQL operations
- Performance-critical operations on large tables

In these cases, you'll need to manually modify the generated migration script or create a new one from scratch.

## Automatic Migration Application

Migrations are automatically applied when the application starts up. This ensures your database schema is always in sync with the application code. However, during development you should still run migrations manually to verify they work as expected.

## Important Note

All Alembic commands must be executed from the `src/external/alembic` directory. This ensures Alembic can find its configuration files and migration scripts correctly.

## Workflow for Database Changes

1. Before making any changes to database entities in code:
   ```bash
   alembic upgrade head
   ```
   This ensures your local database is up to date with the latest migrations.

2. After making changes to SQLModel entities:
   ```bash
   alembic revision --autogenerate -m "description of changes"
   ```
   This will generate a new migration file in versions/ directory.

3. Review the generated migration file carefully:
   - Check that all intended changes are captured
   - Verify indexes and constraints
   - Ensure data migrations are handled appropriately
   - Remove any unintended changes

4. Test the migration:
   ```bash
   alembic upgrade head
   alembic downgrade -1
   alembic upgrade head
   ```
   This verifies both upgrade and downgrade paths work correctly.

## Handling Alembic Migration Conflicts

### When Multiple Heads Appear

Multiple heads typically occur in these scenarios:
1. Two developers created migrations simultaneously from the same base
2. Git merge brought migrations from different branches
3. Someone created a migration without pulling latest changes

### Resolution Strategies

#### 1. Manual Revision Change (Preferred for unpublished migrations)

When to use:
- Your migration hasn't been pushed to main branch
- You're the only one with the conflicting migration
- You're sure about the correct order of changes

How to do it:
```python
# Your migration file that needs updating
"""your migration

Revision ID: abc123
Revises: def456  # <- Change this to the latest head
Create Date: 2023-01-01
"""

# revision identifiers
revision = 'abc123'
down_revision = 'def456'  # <- Update this
```

#### 2. Merge Command (For published migrations)

When to use:
- Migrations are already in production/main branch
- Multiple developers have already pulled the migrations
- You're unsure about the correct order of changes

How to do it:
```bash
# 1. Check current heads
alembic heads

# 2. Create merge migration
alembic merge -m "merge_branch_x_and_y" <revision1> <revision2>

# 3. Apply merge
alembic upgrade head
```

### Prevention Best Practices

1. Always pull latest changes before creating migrations
2. Run `alembic upgrade head` before creating new migrations
3. Create and push migrations one at a time
4. Communicate with team when working on migrations

### Emergency Fixes

If you need to rewrite migration history:
1. Coordinate with all team members
2. Ensure everyone has committed their changes
3. Apply fixes
4. Have team members reset their databases and pull fresh changes

Remember: Changing migration history on published branches should be avoided unless absolutely necessary.

## Data Migrations

In addition to schema changes, you can perform data migrations using Alembic. Data migrations allow you to manipulate existing data in your database as part of the migration process.

### Creating Data Migrations

1. Generate an empty migration:
   ```bash
   alembic revision -m "migrate_user_data"
   ```

2. In the generated migration file, you can:
   - Execute raw SQL using `op.execute()`
   - Use SQLAlchemy expressions
   - Run any Python code to transform data

Example data migration:

```python
"""migrate_user_data

Revision ID: abc123def456
Revises: previous_revision_id
Create Date: 2024-01-01
"""

from alembic import op
import sqlalchemy as sa

def upgrade():
    # Execute raw SQL
    op.execute("UPDATE users SET status = 'active' WHERE status IS NULL")
    
    # Use SQLAlchemy expressions
    connection = op.get_bind()
    users = sa.table('users',
        sa.column('id', sa.Integer),
        sa.column('full_name', sa.String),
        sa.column('first_name', sa.String),
        sa.column('last_name', sa.String)
    )
    
    # Complex data transformation
    result = connection.execute(users.select())
    for user in result:
        if user.full_name and not (user.first_name or user.last_name):
            names = user.full_name.split(' ', 1)
            first_name = names[0]
            last_name = names[1] if len(names) > 1 else None
            
            connection.execute(
                users.update().
                where(users.c.id == user.id).
                values(
                    first_name=first_name,
                    last_name=last_name
                )
            )

def downgrade():
    # Implement reverse data migration if needed
    pass
```


## Important Notes

- When merging to main branch, pay attention to revision numbers and dependencies:
  - Each revision has a unique identifier and may depend on previous revisions
  - If multiple developers create migrations simultaneously, resolve conflicts carefully
  - Ensure the revision chain remains linear and consistent
  - Consider rebasing your migration branch if main has new migrations

- Best Practices:
  - One migration per logical database change
  - Include both upgrade() and downgrade() implementations
  - Test migrations on a copy of production data if possible
  - Document complex migrations with comments
  - Be cautious with data migrations that modify existing records
