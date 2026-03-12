"""Update MCP source URLs

Revision ID: c9f5e8a2d4b7
Revises: b8e7f4d19c3a
Create Date: 2025-10-20 14:00:00.000000

"""

from typing import Sequence, Union
from alembic import op
from sqlalchemy import text
from datetime import datetime

# revision identifiers, used by Alembic.
revision: str = 'c9f5e8a2d4b7'
down_revision: Union[str, None] = 'b8e7f4d19c3a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# System user ID for system-provided configurations
SYSTEM_USER_ID = "system"


def upgrade() -> None:
    """
    Upgrade schema - set source_url to be the same as server_home_url for all system MCP configurations.

    This ensures that both "Link to MCP documentation" and "Link to source code" point to the same URL,
    which is typically the GitHub repository or documentation page for the MCP server.
    """
    connection = op.get_bind()

    # Update source_url to match server_home_url for system configurations
    update_stmt = text("""
        UPDATE mcp_configs
        SET
            source_url = server_home_url,
            update_date = :update_date
        WHERE
            is_system = true
            AND user_id = :user_id
            AND server_home_url IS NOT NULL
            AND source_url IS NULL
    """)

    now = datetime.utcnow()
    result = connection.execute(update_stmt, {"update_date": now, "user_id": SYSTEM_USER_ID})

    print(f"Updated {result.rowcount} system MCP configurations with source URLs")


def downgrade() -> None:
    """
    Downgrade schema - clear source_url for all system MCP configurations.

    This reverts the source_url back to NULL for system configurations.
    """
    connection = op.get_bind()

    # Clear source_url for system configurations
    update_stmt = text("""
        UPDATE mcp_configs
        SET
            source_url = NULL,
            update_date = :update_date
        WHERE
            is_system = true
            AND user_id = :user_id
    """)

    now = datetime.utcnow()
    result = connection.execute(update_stmt, {"update_date": now, "user_id": SYSTEM_USER_ID})

    print(f"Cleared source URLs for {result.rowcount} system MCP configurations")
