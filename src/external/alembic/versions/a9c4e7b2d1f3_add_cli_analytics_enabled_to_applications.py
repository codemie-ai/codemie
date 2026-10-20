"""add_cli_analytics_enabled_to_applications

Revision ID: a9c4e7b2d1f3
Revises: e5f9a1c3b7d2
Create Date: 2026-10-15 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a9c4e7b2d1f3"
down_revision: Union[str, None] = "e5f9a1c3b7d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column(
            "cli_analytics_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("applications", "cli_analytics_enabled")
