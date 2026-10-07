"""add_is_default_to_user_projects

Revision ID: bf3cb9db22b7
Revises: u2v3w4x5y6a7
Create Date: 2026-09-23 00:00:00.000000

Adds the per-membership default-project flag (EPMCDME-15110). Additive only,
no backfill: every existing row defaults to is_default=false. A partial
unique index enforces at most one default per user at the DB layer,
backstopping the application-level swap-old-for-new transaction.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "bf3cb9db22b7"
down_revision = "u2v3w4x5y6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_projects",
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "uix_user_projects_one_default",
        "user_projects",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_default = true"),
    )


def downgrade() -> None:
    op.drop_index("uix_user_projects_one_default", table_name="user_projects")
    op.drop_column("user_projects", "is_default")
