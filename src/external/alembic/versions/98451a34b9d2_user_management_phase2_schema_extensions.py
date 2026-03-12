"""phase2_schema_extensions

Revision ID: 98451a34b9d2
Revises: 299a10eb05a0
Create Date: 2026-02-07 14:32:25.000000

Phase 2 schema extensions for user management and project classification.
Adds project_type and created_by to applications table, project_limit to users table,
and case-insensitive unique index on project names.

Story: EPMCDME-10160 Story 1 - Database Schema Extensions
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '98451a34b9d2'
down_revision: Union[str, None] = '299a10eb05a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ===========================================
    # 1. Add project_type column to applications
    # ===========================================
    # Add column without server_default first (will backfill explicitly)
    op.add_column('applications', sa.Column('project_type', sa.String(50), nullable=True))

    # Backfill existing rows explicitly
    op.execute("""
        UPDATE applications
        SET project_type = 'shared'
        WHERE project_type IS NULL
    """)

    # Now make it NOT NULL with default for future rows
    op.alter_column('applications', 'project_type', nullable=False, server_default='shared')

    # Add CHECK constraint for project_type enum
    op.create_check_constraint('ck_applications_project_type', 'applications', "project_type IN ('personal', 'shared')")

    # ===========================================
    # 2. Add created_by column to applications
    # ===========================================
    # Widened to VARCHAR(255) to accommodate non-UUID IDP sub claims (e.g., email-based, domain-specific IDs)
    op.add_column('applications', sa.Column('created_by', sa.String(255), nullable=True))

    # Explicit backfill (all existing projects are shared, created_by remains NULL)
    # No UPDATE needed as NULL is the correct state for existing records

    # Note: Foreign key constraint deferred to separate operational task per story requirements

    # ===========================================
    # 3. Add project_limit column to users
    # ===========================================
    # Add with server_default to ensure future rows get default value
    op.add_column('users', sa.Column('project_limit', sa.Integer(), nullable=True, server_default='3'))

    # Add CHECK constraint for project_limit (>= 0 OR NULL)
    op.create_check_constraint('ck_users_project_limit', 'users', "project_limit IS NULL OR project_limit >= 0")

    # Backfill existing users explicitly
    op.execute("""
        UPDATE users
        SET project_limit = CASE
            WHEN is_super_admin = true THEN NULL
            ELSE 3
        END
        WHERE project_limit IS NULL
    """)

    # ===========================================
    # 4. Case-insensitive unique index on applications.name
    # ===========================================
    # Ensure trigram index exists for ILIKE search performance
    # Note: This index is expected to exist from earlier migrations, but can be missing
    # in some environments. Use IF NOT EXISTS to avoid duplicate-index errors.
    op.execute("CREATE INDEX IF NOT EXISTS ix_applications_name ON applications USING gin (name gin_trgm_ops)")

    # Add new functional unique index for case-insensitive uniqueness
    # Commented as this index must be created only when we enable user management
    # and clean up the applications table to ensure no existing duplicates.
    # op.execute(
    #     "CREATE UNIQUE INDEX IF NOT EXISTS ix_applications_name_lower " "ON applications USING btree (LOWER(name))"
    # )

    # ===========================================
    # 5. Data migration complete
    # ===========================================
    # All backfills done explicitly above


def downgrade() -> None:
    """Downgrade schema."""
    # Drop in reverse order

    # Remove case-insensitive unique index (keep original trigram index)
    op.execute('DROP INDEX IF EXISTS ix_applications_name_lower')

    # Remove trigram index (it may have been created by this migration for some envs)
    op.execute('DROP INDEX IF EXISTS ix_applications_name')

    # Remove project_limit column and constraint
    op.drop_constraint('ck_users_project_limit', 'users', type_='check')
    op.drop_column('users', 'project_limit')

    # Remove created_by column (no constraint to drop)
    op.drop_column('applications', 'created_by')

    # Remove project_type column and constraint
    op.drop_constraint('ck_applications_project_type', 'applications', type_='check')
    op.drop_column('applications', 'project_type')
