"""change default schema

Revision ID: f90b60343f8c
Revises: 07418664fdcf
Create Date: 2025-04-28 09:46:47.533851

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
from codemie.configs import config as codemie_config
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f90b60343f8c'
down_revision: Union[str, None] = '07418664fdcf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create schema
    op.execute(f'CREATE SCHEMA IF NOT EXISTS {codemie_config.DEFAULT_DB_SCHEMA}')
    # Move existing tables
    op.execute(f'''
        DO $$
        DECLARE
            row record;
        BEGIN
            FOR row IN 
                SELECT tablename FROM pg_tables 
                WHERE schemaname = 'public'
            LOOP
                EXECUTE 'ALTER TABLE public.' || quote_ident(row.tablename) || 
                        ' SET SCHEMA {codemie_config.DEFAULT_DB_SCHEMA}';
            END LOOP;
        END;
        $$;
    ''')
    # Get all enum types from public schema and move them
    op.execute(f'''
        DO $$
        DECLARE
            enum_type record;
        BEGIN
            FOR enum_type IN (
                SELECT t.typname
                FROM pg_type t
                JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
                WHERE n.nspname = 'public'
                AND t.typtype = 'e'
            )
            LOOP
                EXECUTE 'ALTER TYPE public.' || quote_ident(enum_type.typname) || 
                        ' SET SCHEMA {codemie_config.DEFAULT_DB_SCHEMA}';
            END LOOP;
        END;
        $$;
    ''')


def downgrade() -> None:
    """Downgrade schema."""
    # Move tables back to public
    op.execute(f'''
        DO $$
        DECLARE
            row record;
        BEGIN
            FOR row IN 
                SELECT tablename FROM pg_tables 
                WHERE schemaname = '{codemie_config.DEFAULT_DB_SCHEMA}'
            LOOP
                EXECUTE 'ALTER TABLE {codemie_config.DEFAULT_DB_SCHEMA}.' || quote_ident(row.tablename) || 
                        ' SET SCHEMA public';
            END LOOP;
        END;
        $$;
    ''')
    # Move enum types back to public schema
    op.execute(f'''
        DO $$
        DECLARE
            enum_type record;
        BEGIN
            FOR enum_type IN (
                SELECT t.typname
                FROM pg_type t
                JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
                WHERE n.nspname = '{codemie_config.DEFAULT_DB_SCHEMA}'
                AND t.typtype = 'e'
            )
            LOOP
                EXECUTE 'ALTER TYPE {codemie_config.DEFAULT_DB_SCHEMA}.' || quote_ident(enum_type.typname) || 
                        ' SET SCHEMA public';
            END LOOP;
        END;
        $$;
    ''')
    # Drop schema if empty
    op.execute(f'DROP SCHEMA IF EXISTS {codemie_config.DEFAULT_DB_SCHEMA}')
