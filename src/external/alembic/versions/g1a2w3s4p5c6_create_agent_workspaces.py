"""create agent workspaces

Revision ID: g1a2w3s4p5c6
Revises: e5f6a7b8c9d0
Create Date: 2026-04-23 16:20:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

# revision identifiers, used by Alembic.
revision: str = "g1a2w3s4p5c6"
down_revision: Union[str, Sequence[str], None] = "234f8f339638"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "agent_workspaces",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("date", sa.DateTime(), nullable=True),
        sa.Column("update_date", sa.DateTime(), nullable=True),
        sa.Column("conversation_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_agent_workspaces_conversation_id"),
        "agent_workspaces",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_workspaces_user_id"),
        "agent_workspaces",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "agent_workspace_files",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("date", sa.DateTime(), nullable=True),
        sa.Column("update_date", sa.DateTime(), nullable=True),
        sa.Column("workspace_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("path", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("blob_owner", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("blob_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("mime_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("checksum", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_agent_workspace_files_path"),
        "agent_workspace_files",
        ["path"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_workspace_files_workspace_id"),
        "agent_workspace_files",
        ["workspace_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_agent_workspace_files_workspace_id"),
        table_name="agent_workspace_files",
    )
    op.drop_index(op.f("ix_agent_workspace_files_path"), table_name="agent_workspace_files")
    op.drop_table("agent_workspace_files")

    op.drop_index(op.f("ix_agent_workspaces_user_id"), table_name="agent_workspaces")
    op.drop_index(op.f("ix_agent_workspaces_conversation_id"), table_name="agent_workspaces")
    op.drop_table("agent_workspaces")
