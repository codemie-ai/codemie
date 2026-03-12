"""set_null_is_global_to_false_in_setting_table

Revision ID: aeb028ff26ac
Revises: 5467f64f2f38
Create Date: 2025-06-17 17:06:19.678494

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'aeb028ff26ac'
down_revision: Union[str, None] = '5467f64f2f38'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Update the setting table: set is_global to false where it's null
    op.execute("UPDATE settings SET is_global = FALSE WHERE is_global IS NULL")


def downgrade() -> None:
    """Downgrade schema."""
    pass
