"""add_effort_to_assistants

Revision ID: y1e2f3o4r5t6
Revises: d4f8a2c691be
Create Date: 2026-10-09 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "y1e2f3o4r5t6"
down_revision: Union[str, None] = "d4f8a2c691be"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("assistants", sa.Column("effort", sa.String(), nullable=True))
    op.add_column("assistant_configurations", sa.Column("effort", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("assistant_configurations", "effort")
    op.drop_column("assistants", "effort")
