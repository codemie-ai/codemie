"""merge_favorites_pinned_and_main_heads

Revision ID: f9e8d7c6b5a4
Revises: 8eb9522661cf, i3j4k5l6m7n8
Create Date: 2026-05-22 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "f9e8d7c6b5a4"
down_revision: Union[str, None] = ("8eb9522661cf", "i3j4k5l6m7n8")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
