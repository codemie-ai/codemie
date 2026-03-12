"""add_deleted_at_to_applications

Revision ID: a4d9b6c2e7f1
Revises: 5d6358638299
Create Date: 2026-02-10 14:30:00.000000

Add soft-delete support for projects by introducing applications.deleted_at.
Required for Story 14 project limit counting (exclude soft-deleted projects).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a4d9b6c2e7f1"
down_revision: Union[str, None] = "5d6358638299"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("applications", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.create_index("ix_applications_deleted_at", "applications", ["deleted_at"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_applications_deleted_at", table_name="applications")
    op.drop_column("applications", "deleted_at")
