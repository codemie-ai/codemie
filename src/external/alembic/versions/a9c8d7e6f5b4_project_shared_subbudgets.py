"""project_shared_subbudgets

Revision ID: a9c8d7e6f5b4
Revises: 5d2e30c11ead
Create Date: 2026-04-24 19:05:00.000000

Human-run verification after review:
  alembic upgrade head
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a9c8d7e6f5b4"
down_revision: Union[str, None] = "5d2e30c11ead"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("budgets", "budget_id", existing_type=sa.VARCHAR(length=128), type_=sa.String(length=255))
    op.alter_column(
        "user_budget_assignments",
        "budget_id",
        existing_type=sa.VARCHAR(length=128),
        type_=sa.String(length=255),
    )
    op.alter_column(
        "project_budget_assignments",
        "budget_id",
        existing_type=sa.VARCHAR(length=128),
        type_=sa.String(length=255),
    )
    op.alter_column(
        "project_member_budget_assignments",
        "project_budget_id",
        existing_type=sa.VARCHAR(length=128),
        type_=sa.String(length=255),
    )

    op.add_column(
        "budgets",
        sa.Column("budget_origin_type", sa.String(length=32), nullable=False, server_default="main"),
    )
    op.add_column("budgets", sa.Column("parent_budget_id", sa.String(length=255), nullable=True))
    op.add_column("budgets", sa.Column("owner_user_id", sa.String(length=36), nullable=True))
    op.add_column("budgets", sa.Column("project_name", sa.String(length=100), nullable=True))
    op.add_column("budgets", sa.Column("detached_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_budgets_parent_budget_id",
        "budgets",
        "budgets",
        ["parent_budget_id"],
        ["budget_id"],
    )
    op.create_foreign_key(
        "fk_budgets_owner_user_id",
        "budgets",
        "users",
        ["owner_user_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_budgets_project_name",
        "budgets",
        "applications",
        ["project_name"],
        ["id"],
    )
    op.create_index("ix_budgets_parent_budget_id", "budgets", ["parent_budget_id"])
    op.create_index("ix_budgets_origin_type", "budgets", ["budget_origin_type"])
    op.create_index("ix_budgets_project_origin", "budgets", ["project_name", "budget_category", "budget_origin_type"])

    op.add_column(
        "project_member_budget_assignments", sa.Column("shared_budget_id", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "project_member_budget_assignments",
        sa.Column("override_budget_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "project_member_budget_assignments",
        sa.Column("effective_budget_id", sa.String(length=255), nullable=True),
    )
    op.create_foreign_key(
        "fk_pmba_shared_budget_id",
        "project_member_budget_assignments",
        "budgets",
        ["shared_budget_id"],
        ["budget_id"],
    )
    op.create_foreign_key(
        "fk_pmba_override_budget_id",
        "project_member_budget_assignments",
        "budgets",
        ["override_budget_id"],
        ["budget_id"],
    )
    op.create_foreign_key(
        "fk_pmba_effective_budget_id",
        "project_member_budget_assignments",
        "budgets",
        ["effective_budget_id"],
        ["budget_id"],
    )
    op.create_index("ix_pmba_effective_budget_id", "project_member_budget_assignments", ["effective_budget_id"])


def downgrade() -> None:
    op.drop_index("ix_pmba_effective_budget_id", table_name="project_member_budget_assignments")
    op.drop_constraint("fk_pmba_effective_budget_id", "project_member_budget_assignments", type_="foreignkey")
    op.drop_constraint("fk_pmba_override_budget_id", "project_member_budget_assignments", type_="foreignkey")
    op.drop_constraint("fk_pmba_shared_budget_id", "project_member_budget_assignments", type_="foreignkey")
    op.drop_column("project_member_budget_assignments", "effective_budget_id")
    op.drop_column("project_member_budget_assignments", "override_budget_id")
    op.drop_column("project_member_budget_assignments", "shared_budget_id")

    op.drop_index("ix_budgets_project_origin", table_name="budgets")
    op.drop_index("ix_budgets_origin_type", table_name="budgets")
    op.drop_index("ix_budgets_parent_budget_id", table_name="budgets")
    op.drop_constraint("fk_budgets_project_name", "budgets", type_="foreignkey")
    op.drop_constraint("fk_budgets_owner_user_id", "budgets", type_="foreignkey")
    op.drop_constraint("fk_budgets_parent_budget_id", "budgets", type_="foreignkey")
    op.drop_column("budgets", "detached_at")
    op.drop_column("budgets", "project_name")
    op.drop_column("budgets", "owner_user_id")
    op.drop_column("budgets", "parent_budget_id")
    op.drop_column("budgets", "budget_origin_type")
    op.alter_column(
        "project_member_budget_assignments",
        "project_budget_id",
        existing_type=sa.String(length=255),
        type_=sa.VARCHAR(length=128),
    )
    op.alter_column(
        "project_budget_assignments",
        "budget_id",
        existing_type=sa.String(length=255),
        type_=sa.VARCHAR(length=128),
    )
    op.alter_column(
        "user_budget_assignments",
        "budget_id",
        existing_type=sa.String(length=255),
        type_=sa.VARCHAR(length=128),
    )
    op.alter_column("budgets", "budget_id", existing_type=sa.String(length=255), type_=sa.VARCHAR(length=128))
