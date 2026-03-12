"""add_user_management_tables

Revision ID: 299a10eb05a0
Revises: aa36ad4f4409
Create Date: 2026-01-30 01:04:11.536919

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '299a10eb05a0'
down_revision: Union[str, None] = 'aa36ad4f4409'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ===========================================
    # Create users table
    # ===========================================
    op.create_table(
        'users',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('date', sa.DateTime(), nullable=True),
        sa.Column('update_date', sa.DateTime(), nullable=True),
        sa.Column('username', sa.String(255), unique=True, nullable=False),
        sa.Column('email', sa.String(255), unique=True, nullable=False),
        sa.Column('name', sa.String(255), nullable=True),
        sa.Column('password_hash', sa.String(255), nullable=True),
        sa.Column('picture', sa.String(1024), nullable=True),
        sa.Column('user_type', sa.String(50), nullable=False, server_default='regular'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('is_super_admin', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('auth_source', sa.String(50), nullable=False, server_default='local'),
        sa.Column('email_verified', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('last_login_at', sa.DateTime(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
    )

    # Users table indexes
    op.create_index('ix_users_id', 'users', ['id'])
    op.create_index('ix_users_username', 'users', ['username'])
    op.create_index('ix_users_email', 'users', ['email'])
    op.create_index('ix_users_is_active', 'users', ['is_active'])
    op.create_index('ix_users_is_super_admin', 'users', ['is_super_admin'])
    op.create_index('ix_users_deleted_at', 'users', ['deleted_at'])
    op.create_index('ix_users_active_deleted', 'users', ['is_active', 'deleted_at'])
    op.create_index('ix_users_email_deleted', 'users', ['email', 'deleted_at'])

    # ===========================================
    # Create user_projects table
    # ===========================================
    op.create_table(
        'user_projects',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('date', sa.DateTime(), nullable=True),
        sa.Column('update_date', sa.DateTime(), nullable=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('project_name', sa.String(255), nullable=False),
        sa.Column('is_project_admin', sa.Boolean(), nullable=False, server_default='false'),
    )

    # User projects indexes
    op.create_index('ix_user_projects_user_id', 'user_projects', ['user_id'])
    op.create_index('ix_user_projects_project_name', 'user_projects', ['project_name'])
    op.create_unique_constraint('ix_user_project_unique', 'user_projects', ['user_id', 'project_name'])

    # ===========================================
    # Create user_knowledge_bases table
    # ===========================================
    op.create_table(
        'user_knowledge_bases',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('date', sa.DateTime(), nullable=True),
        sa.Column('update_date', sa.DateTime(), nullable=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kb_name', sa.String(255), nullable=False),
    )

    # User knowledge bases indexes
    op.create_index('ix_user_knowledge_bases_user_id', 'user_knowledge_bases', ['user_id'])
    op.create_index('ix_user_knowledge_bases_kb_name', 'user_knowledge_bases', ['kb_name'])
    op.create_unique_constraint('ix_user_kb_unique', 'user_knowledge_bases', ['user_id', 'kb_name'])

    # ===========================================
    # Create email_verification_tokens table
    # ===========================================
    op.create_table(
        'email_verification_tokens',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('date', sa.DateTime(), nullable=True),
        sa.Column('update_date', sa.DateTime(), nullable=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('token_hash', sa.String(255), unique=True, nullable=False),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('token_type', sa.String(50), nullable=False, server_default='email_verification'),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('used_at', sa.DateTime(), nullable=True),
    )

    # Email verification tokens indexes
    op.create_index('ix_email_verification_tokens_user_id', 'email_verification_tokens', ['user_id'])
    op.create_index('ix_email_verification_tokens_token_hash', 'email_verification_tokens', ['token_hash'])
    op.create_index('ix_email_verification_tokens_expires_at', 'email_verification_tokens', ['expires_at'])


def downgrade() -> None:
    """Downgrade schema."""
    # Drop tables in reverse order (due to foreign keys)
    op.drop_table('email_verification_tokens')
    op.drop_table('user_knowledge_bases')
    op.drop_table('user_projects')
    op.drop_table('users')
