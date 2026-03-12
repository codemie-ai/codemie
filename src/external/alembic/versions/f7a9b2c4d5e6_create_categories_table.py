"""create_categories_table

Revision ID: f7a9b2c4d5e6
Revises: e03e516e00da
Create Date: 2025-12-15 12:00:00.000000

"""

from typing import Sequence, Union
from datetime import datetime, UTC
from pathlib import Path

from alembic import op
import sqlalchemy as sa
import sqlmodel
import yaml

# revision identifiers, used by Alembic.
revision: str = 'f7a9b2c4d5e6'
down_revision: Union[str, None] = 'e03e516e00da'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _seed_categories_from_yaml() -> None:
    """Seed initial categories from YAML configuration using raw SQL."""
    # Load categories from YAML file
    yaml_path = (
        Path(__file__).parent.parent.parent.parent.parent / "config" / "categories" / "assistant-categories.yaml"
    )

    if not yaml_path.exists():
        raise FileNotFoundError(f"Categories YAML file not found at {yaml_path}")

    with open(yaml_path, 'r', encoding='utf-8') as file:
        category_config = yaml.safe_load(file)

    categories = category_config.get('categories', [])
    if not categories:
        return

    # Get current connection from Alembic
    bind = op.get_bind()

    # Insert categories using raw SQL
    now = datetime.now(UTC)
    for cat_data in categories:
        bind.execute(
            sa.text(
                "INSERT INTO categories (id, name, description, date, update_date) "
                "VALUES (:id, :name, :description, :date, :update_date) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": cat_data['id'],
                "name": cat_data['name'],
                "description": cat_data.get('description', ''),
                "date": now,
                "update_date": now,
            },
        )


def upgrade() -> None:
    """Upgrade schema."""
    # Create categories table
    op.create_table(
        'categories',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('date', sa.DateTime(), nullable=True),
        sa.Column('update_date', sa.DateTime(), nullable=True),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    # Create indexes
    op.create_index(op.f('ix_categories_name'), 'categories', ['name'], unique=True)

    # Seed initial categories from YAML using raw SQL
    _seed_categories_from_yaml()


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_categories_name'), table_name='categories')
    op.drop_table('categories')
