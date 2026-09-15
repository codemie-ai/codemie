"""epmcdme_13959_add_soft_limit_notification_enabled

Adds soft_limit_notification_enabled column to budgets.
When false (default), soft-limit email notifications are suppressed
regardless of notification_owner_email being set.

Human-run verification after review:
  alembic upgrade head
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, None] = "x1y2z3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "budgets",
        sa.Column(
            "soft_limit_notification_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("budgets", "soft_limit_notification_enabled")
