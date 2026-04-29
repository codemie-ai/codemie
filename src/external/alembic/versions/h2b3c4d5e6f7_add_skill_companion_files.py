"""add_skill_companion_files

Revision ID: h2b3c4d5e6f7
Revises: g1a2w3s4p5c6
Create Date: 2026-04-23 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "h2b3c4d5e6f7"
down_revision: Union[str, Sequence[str], None] = "g1a2w3s4p5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add companion_files JSONB column to skills table."""
    op.add_column(
        "skills",
        sa.Column(
            "companion_files",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    """Remove companion_files column from skills table."""
    op.drop_column("skills", "companion_files")
