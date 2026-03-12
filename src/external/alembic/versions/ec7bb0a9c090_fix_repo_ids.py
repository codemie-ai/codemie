"""Fix repo ids

Revision ID: ec7bb0a9c090
Revises: d81ad465d53b
Create Date: 2025-06-26 11:21:54.445590

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'ec7bb0a9c090'
down_revision: Union[str, None] = 'd81ad465d53b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("""                                                                                                     
        UPDATE repositories                                                                                            
        SET id = new_id                                                                                                
        FROM (                                                                                                         
            SELECT id, app_id || '-' || name || '-' || (
                CASE index_type                                                                                            
                    WHEN 'SUMMARY' THEN 'summary'                                                                                
                    WHEN 'CODE' THEN 'code'
                    when 'CHUNK_SUMMARY' then 'chunk-summary'                                                                   
                END 
               ) new_id                                     
            FROM repositories                                                                                          
        ) AS subq                                                                                                      
        WHERE repositories.id = subq.id                                                                                
        AND repositories.id <> subq.new_id                                                                                                               
    """)


def downgrade() -> None:
    """Downgrade schema."""
    pass
