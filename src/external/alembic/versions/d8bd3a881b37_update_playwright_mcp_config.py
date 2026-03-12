"""update_playwright_mcp_config

Revision ID: d8bd3a881b37
Revises: e8f3a9b5c2d1
Create Date: 2025-11-24 16:52:59.142277

This migration updates the Playwright MCP configuration in the mcp_configs table
to include additional arguments for browser configuration such as --isolated,
--headless, --no-sandbox, executable path, and viewport size.

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd8bd3a881b37'
down_revision: Union[str, None] = 'e8f3a9b5c2d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Update Playwright MCP configuration with browser settings."""
    op.execute("""
        UPDATE mcp_configs
        SET config = '{
          "env": {},
          "args": [
            "@playwright/mcp@latest",
            "--isolated",
            "--headless",
            "--no-sandbox",
            "-executable-path",
            "/usr/bin/chromium",
            "--viewport-size",
            "1920x1080"
          ],
          "command": "npx",
          "single_usage": false
        }'::jsonb
        WHERE name = 'Playwright MCP'
    """)


def downgrade() -> None:
    """Revert Playwright MCP configuration to previous state."""
    # Note: This downgrade is intentionally left as pass since we don't have
    # the previous configuration state and this is a data migration.
    # If you need to revert, you'll need to manually restore the previous config.
    pass
