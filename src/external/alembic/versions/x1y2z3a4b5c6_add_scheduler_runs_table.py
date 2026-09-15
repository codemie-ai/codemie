"""add_scheduler_runs_table

Revision ID: x1y2z3a4b5c6
Revises: fa14587c0de1
Create Date: 2026-09-09 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'x1y2z3a4b5c6'
down_revision = 'd3c838ee6ab9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheduler_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("scheduler_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("trigger", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("execution_id", sa.String(), nullable=True),
        sa.Column("conversation_id", sa.String(), nullable=True),
        sa.Column("resource_execution_id", sa.String(), nullable=True),
        sa.Column("input_data", postgresql.JSONB(), nullable=True),
        sa.Column("result_data", postgresql.JSONB(), nullable=True),
        sa.Column("error_data", postgresql.JSONB(), nullable=True),
        sa.Column("logs", postgresql.JSONB(), nullable=True),
        sa.Column("metrics", postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(
            ["scheduler_id"],
            ["codemie.settings.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="codemie",
    )
    op.create_index(
        "ix_codemie_scheduler_runs_scheduler_id",
        "scheduler_runs",
        ["scheduler_id"],
        schema="codemie",
    )
    op.create_index(
        "ix_codemie_scheduler_runs_started_at",
        "scheduler_runs",
        ["started_at"],
        schema="codemie",
    )
    op.create_index(
        "ix_codemie_scheduler_runs_status",
        "scheduler_runs",
        ["status"],
        schema="codemie",
    )


def downgrade() -> None:
    op.drop_index("ix_codemie_scheduler_runs_status", table_name="scheduler_runs", schema="codemie")
    op.drop_index("ix_codemie_scheduler_runs_started_at", table_name="scheduler_runs", schema="codemie")
    op.drop_index("ix_codemie_scheduler_runs_scheduler_id", table_name="scheduler_runs", schema="codemie")
    op.drop_table("scheduler_runs", schema="codemie")
