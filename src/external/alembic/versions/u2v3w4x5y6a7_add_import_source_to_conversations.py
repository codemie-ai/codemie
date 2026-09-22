"""add_import_source_to_conversations

Revision ID: u2v3w4x5y6a7
Revises: d8e4f1a2b6c3
Create Date: 2026-08-13 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "u2v3w4x5y6a7"
down_revision: Union[str, None] = "d8e4f1a2b6c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("import_source", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "import_source")
