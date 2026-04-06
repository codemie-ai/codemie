"""migrate_icon_urls_to_new_repo

Migrates icon/image/logo URLs from the legacy asset repository to the new one
across assistants, workflows, ai_katas, and mcp_configs tables.

Revision ID: f2a3b4c5d6e7
Revises: b4c5d6e7f8a9
Create Date: 2026-03-31 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, None] = "b4c5d6e7f8a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD = "https://raw.githubusercontent.com/epam/edp-install/master/docs/assets/ai/"
_NEW = "https://epam-gen-ai-run.github.io/ai-run-install/docs/assets/ai/"


def upgrade() -> None:
    op.execute(
        f"UPDATE assistants  SET icon_url  = REPLACE(icon_url,  '{_OLD}', '{_NEW}') WHERE icon_url  LIKE '%epam/edp-install%'"
    )
    op.execute(
        f"UPDATE workflows   SET icon_url  = REPLACE(icon_url,  '{_OLD}', '{_NEW}') WHERE icon_url  LIKE '%epam/edp-install%'"
    )
    op.execute(
        f"UPDATE ai_katas    SET image_url = REPLACE(image_url, '{_OLD}', '{_NEW}') WHERE image_url LIKE '%epam/edp-install%'"
    )
    op.execute(
        f"UPDATE mcp_configs SET logo_url  = REPLACE(logo_url,  '{_OLD}', '{_NEW}') WHERE logo_url  LIKE '%epam/edp-install%'"
    )


def downgrade() -> None:
    pass  # Intentionally no-op: migration to new asset host is permanent
