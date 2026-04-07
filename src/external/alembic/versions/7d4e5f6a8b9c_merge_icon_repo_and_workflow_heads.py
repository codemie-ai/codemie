"""merge icon repo and workflow heads

Revision ID: 7d4e5f6a8b9c
Revises: f2a3b4c5d6e7, f2c3d4e5f6a7
Create Date: 2026-04-06 12:30:00.000000

"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "7d4e5f6a8b9c"
down_revision: Union[str, Sequence[str], None] = ("f2a3b4c5d6e7", "f2c3d4e5f6a7")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Merge parallel heads into a single linear tip."""
    pass


def downgrade() -> None:
    """Restore the split heads."""
    pass
