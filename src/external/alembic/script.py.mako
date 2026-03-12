"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

${imports if imports else ""}                                                                                       
${"" if imports and "from sqlalchemy.dialects import postgresql" in imports else "from sqlalchemy.dialects import postgresql"} 

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    """Upgrade schema."""
    ${upgrades.replace("enum_schema='public'", "enum_schema='codemie'").replace("table_schema='public'", "table_schema='codemie'") if upgrades else "pass"}

def downgrade() -> None:
    """Downgrade schema."""
    ${downgrades.replace("enum_schema='public'", "enum_schema='codemie'").replace("table_schema='public'", "table_schema='codemie'")  if downgrades else "pass"}
