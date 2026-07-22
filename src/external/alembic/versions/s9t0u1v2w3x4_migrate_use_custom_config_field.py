"""migrate_use_custom_config_field

Revision ID: s9t0u1v2w3x4
Revises: 38255069bfab
Create Date: 2026-07-20 00:00:00.000000

"""

import json
import logging
from typing import Any, Sequence, Union

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "s9t0u1v2w3x4"
down_revision: Union[str, None] = "38255069bfab"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger(__name__)


def _transform_mcp_servers(mcp_servers: list[Any]) -> tuple[list[Any], bool, list[str]]:
    """Return (new_servers, changed, catalog_mcp_names) with use_custom_config set for inline configs.

    All MCPs with a non-empty inline config are currently using it at runtime, so the flag is
    set to preserve that behavior instead of falling back to the catalog reference.

    Pure function — no DB access, fully unit-testable.
    """
    new_servers: list[Any] = []
    changed = False
    catalog_mcp_names: list[str] = []

    for mcp in mcp_servers:
        # Leave anything unexpected untouched
        if not isinstance(mcp, dict):
            new_servers.append(mcp)
            continue

        # Skip if already migrated
        if mcp.get("use_custom_config"):
            new_servers.append(mcp)
            continue

        # An empty config object is normalized to None by the model, so it is not an inline config
        config = mcp.get("config")
        if not (isinstance(config, dict) and config):
            new_servers.append(mcp)
            continue

        new_servers.append({**mcp, "use_custom_config": True})
        changed = True

        if mcp.get("mcp_config_id"):
            catalog_mcp_names.append(str(mcp.get("name")))

    return new_servers, changed, catalog_mcp_names


def upgrade() -> None:
    """Migrate existing MCP servers to use use_custom_config field.

    Sets use_custom_config=true for all MCPs with inline config to preserve
    current runtime behavior (they are currently using inline config, not
    fetching from catalog).
    """
    conn = op.get_bind()
    rows = conn.execute(
        text(
            "SELECT id, mcp_servers FROM assistants"
            " WHERE mcp_servers IS NOT NULL"
            " AND jsonb_typeof(mcp_servers) = 'array'"
            " AND jsonb_array_length(mcp_servers) > 0"
        )
    ).fetchall()

    migrated_count = 0
    for row in rows:
        assistant_id, raw_servers = row[0], row[1]
        mcp_servers = raw_servers if isinstance(raw_servers, list) else json.loads(raw_servers)

        new_servers, changed, catalog_mcp_names = _transform_mcp_servers(mcp_servers)
        if not changed:
            continue

        conn.execute(
            text("UPDATE assistants SET mcp_servers = CAST(:mcp_servers AS jsonb) WHERE id = :id"),
            {"mcp_servers": json.dumps(new_servers), "id": assistant_id},
        )
        migrated_count += 1

        for mcp_name in catalog_mcp_names:
            logger.info(
                f"Assistant {assistant_id}, MCP '{mcp_name}': "
                f"was catalog MCP but using inline config, "
                f"migrated to use_custom_config=true"
            )

    logger.info(f"Migration complete: {migrated_count} assistants updated")


def downgrade() -> None:
    """Downgrade is not implemented as it would require complex logic to determine
    which MCPs should have use_custom_config set back to False."""
    pass
