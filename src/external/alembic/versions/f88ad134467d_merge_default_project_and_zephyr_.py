"""merge_default_project_and_zephyr_squad_removal_heads

Revision ID: f88ad134467d
Revises: bf3cb9db22b7, a7f3c9e1b4d6
Create Date: 2026-09-30 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "f88ad134467d"
down_revision: Union[str, None] = ("bf3cb9db22b7", "a7f3c9e1b4d6")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
