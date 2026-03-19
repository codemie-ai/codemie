"""sharepoint datasource

Revision ID: c9b887e959ba
Revises: a1f2b3c4d5e6
Create Date: 2026-03-11 16:10:50.622036

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from alembic_postgresql_enum import TableReference
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c9b887e959ba'
down_revision: Union[str, None] = 'd64ac374f28c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add sharepoint JSONB column to index_info table and SHAREPOINT to credential types enum."""
    op.add_column(
        'index_info',
        sa.Column('sharepoint', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.sync_enum_values(
        enum_schema='codemie',
        enum_name='credentialtypes',
        new_values=[
            'JIRA',
            'CONFLUENCE',
            'GIT',
            'KUBERNETES',
            'AWS',
            'GCP',
            'KEYCLOAK',
            'AZURE',
            'ELASTIC',
            'OPEN_API',
            'PLUGIN',
            'FILE_SYSTEM',
            'SCHEDULER',
            'WEBHOOK',
            'EMAIL',
            'AZURE_DEVOPS',
            'SONAR',
            'SQL',
            'TELEGRAM',
            'ZEPHYR_SCALE',
            '_ZEPHYR_CLOUD',
            'ZEPHYR_SQUAD',
            'XRAY',
            'SERVICENOW',
            'REPORT_PORTAL',
            'ENVIRONMENT_VARS',
            'AUTH_TOKEN',
            'A2A',
            'LITE_LLM',
            'DIAL',
            'SHAREPOINT',
        ],
        affected_columns=[TableReference(table_schema='codemie', table_name='settings', column_name='credential_type')],
        enum_values_to_rename=[],
    )


def downgrade() -> None:
    """Remove sharepoint column from index_info table and SHAREPOINT from credential types enum."""
    op.execute("DELETE FROM codemie.settings WHERE credential_type = 'SHAREPOINT'")
    op.drop_column('index_info', 'sharepoint')
    op.sync_enum_values(
        enum_schema='codemie',
        enum_name='credentialtypes',
        new_values=[
            'JIRA',
            'CONFLUENCE',
            'GIT',
            'KUBERNETES',
            'AWS',
            'GCP',
            'KEYCLOAK',
            'AZURE',
            'ELASTIC',
            'OPEN_API',
            'PLUGIN',
            'FILE_SYSTEM',
            'SCHEDULER',
            'WEBHOOK',
            'EMAIL',
            'AZURE_DEVOPS',
            'SONAR',
            'SQL',
            'TELEGRAM',
            'ZEPHYR_SCALE',
            '_ZEPHYR_CLOUD',
            'ZEPHYR_SQUAD',
            'XRAY',
            'SERVICENOW',
            'REPORT_PORTAL',
            'ENVIRONMENT_VARS',
            'AUTH_TOKEN',
            'A2A',
            'LITE_LLM',
            'DIAL',
        ],
        affected_columns=[TableReference(table_schema='codemie', table_name='settings', column_name='credential_type')],
        enum_values_to_rename=[],
    )
