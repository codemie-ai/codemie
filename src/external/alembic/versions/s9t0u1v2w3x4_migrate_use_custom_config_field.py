"""migrate_use_custom_config_field

Revision ID: s9t0u1v2w3x4
Revises: 38255069bfab
Create Date: 2026-07-20 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import logging

# revision identifiers, used by Alembic.
revision: str = "s9t0u1v2w3x4"
down_revision: Union[str, None] = "38255069bfab"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger(__name__)


def upgrade() -> None:
    """Migrate existing MCP servers to use use_custom_config field.

    Sets use_custom_config=true for all MCPs with inline config to preserve
    current runtime behavior (they are currently using inline config, not
    fetching from catalog).
    """
    from codemie.rest_api.models.assistant import Assistant

    migrated_count = 0
    page = 1

    while True:
        assistants = Assistant.get_all(page_number=page, items_per_page=500)
        if not assistants:
            break

        for assistant in assistants:
            if not assistant.mcp_servers:
                continue

            modified = False
            for mcp in assistant.mcp_servers:
                # Skip if already migrated
                if mcp.use_custom_config:
                    continue

                # All MCPs with inline config are currently using it
                # Preserve that behavior to avoid breaking existing assistants
                has_inline_config = mcp.config is not None
                if has_inline_config:
                    mcp.use_custom_config = True
                    modified = True

                    if mcp.mcp_config_id:
                        logger.info(
                            f"Assistant {assistant.id}, MCP '{mcp.name}': "
                            f"was catalog MCP but using inline config, "
                            f"migrated to use_custom_config=true"
                        )

            if modified:
                assistant.update()
                migrated_count += 1

        page += 1

    logger.info(f"Migration complete: {migrated_count} assistants updated")


def downgrade() -> None:
    """Downgrade is not implemented as it would require complex logic to determine
    which MCPs should have use_custom_config set back to False."""
    pass
