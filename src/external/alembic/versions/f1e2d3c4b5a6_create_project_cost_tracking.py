"""create_project_cost_tracking

Revision ID: f1e2d3c4b5a6
Revises: a1f2b3c4d5e6
Create Date: 2026-03-17 00:00:00.000000

Create project_cost_tracking table for LiteLLM spend tracking collection.
Stores one row per API key per day with budget-reset-aware daily_spend delta
and idempotency enforced via UNIQUE (key_hash, spend_date).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "f1e2d3c4b5a6"
down_revision: Union[str, None] = "a1f2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create project_cost_tracking table."""
    op.create_table(
        "project_cost_tracking",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("project_name", sa.VARCHAR(), nullable=False),
        sa.Column("key_hash", sa.VARCHAR(), nullable=False),
        sa.Column("spend_date", sa.Date(), nullable=False),
        sa.Column("daily_spend", sa.Numeric(18, 9), nullable=False),
        sa.Column("cumulative_spend", sa.Numeric(18, 9), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # Indexes
    op.create_index("ix_project_cost_tracking_project_name", "project_cost_tracking", ["project_name"])
    op.create_index("ix_project_cost_tracking_key_hash", "project_cost_tracking", ["key_hash"])
    op.create_index("ix_project_cost_tracking_spend_date", "project_cost_tracking", ["spend_date"])

    # Idempotency constraint
    op.create_unique_constraint(
        "uix_project_cost_tracking_key_hash_spend_date",
        "project_cost_tracking",
        ["key_hash", "spend_date"],
    )


def downgrade() -> None:
    """Drop project_cost_tracking table."""
    op.drop_constraint(
        "uix_project_cost_tracking_key_hash_spend_date",
        "project_cost_tracking",
        type_="unique",
    )
    op.drop_index("ix_project_cost_tracking_spend_date", table_name="project_cost_tracking")
    op.drop_index("ix_project_cost_tracking_key_hash", table_name="project_cost_tracking")
    op.drop_index("ix_project_cost_tracking_project_name", table_name="project_cost_tracking")
    op.drop_table("project_cost_tracking")
