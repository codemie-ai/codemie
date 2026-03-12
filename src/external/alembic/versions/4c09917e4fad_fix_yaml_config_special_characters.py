"""fix_yaml_config_special_characters

Revision ID: 4c09917e4fad
Revises: d5e9f2a3b1c7
Create Date: 2025-11-05 14:32:13.584756

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '4c09917e4fad'
down_revision: Union[str, None] = 'd5e9f2a3b1c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Fix include_in_llm_history special characters in yaml_config
    op.execute("""
        UPDATE codemie.workflows
        SET yaml_config = REPLACE(yaml_config, '␁include_in_llm_history:␂␃', '      include_in_llm_history: false')
    """)

    # Fix store_in_context special characters in yaml_config
    op.execute("""
        UPDATE codemie.workflows
        SET yaml_config = REPLACE(yaml_config, '␁store_in_context:␂true', '      store_in_context: true')
    """)


def downgrade() -> None:
    """Downgrade schema."""
    pass
