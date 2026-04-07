"""merge workflow and leaderboard heads

Revision ID: f2c3d4e5f6a7
Revises: b4c5d6e7f8a9, c7e9b4a1d2f3
Create Date: 2026-04-06 12:00:00.000000

"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "f2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = ("b4c5d6e7f8a9", "c7e9b4a1d2f3")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Merge parallel heads into a single linear tip."""
    pass


def downgrade() -> None:
    """Restore the split heads."""
    pass
