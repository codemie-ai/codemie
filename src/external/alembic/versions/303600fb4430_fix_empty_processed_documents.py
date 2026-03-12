"""Fix empty processed documents

Revision ID: 303600fb4430
Revises: c4bb7fb757e9
Create Date: 2025-07-01 13:56:18.754595

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
from codemie.clients.elasticsearch import ElasticSearchClient
from codemie.configs.logger import logger
import json

# revision identifiers, used by Alembic.
revision: str = '303600fb4430'
down_revision: Union[str, None] = 'c4bb7fb757e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    connection = op.get_bind()

    def get_index_identifier(row) -> str:
        """Reimplementation of IndexInfo.get_index_identifier() for migration purposes"""
        INVALID_CHARS = ['"', ' ', '\\', '/', ',', '|', '>', '?', '*', '<', ':']

        if row.index_type.startswith("knowledge_base"):
            # Knowledge base format - need to sanitize repo_name
            name = row.repo_name.lower()
            for char in INVALID_CHARS:
                if char in name:
                    name = name.replace(char, "_")
            return name
        elif row.index_type.startswith("llm_routing_google"):
            # Google doc format - need to sanitize combined name
            name = f"{row.project_name}-{row.repo_name}".lower()
            for char in INVALID_CHARS:
                if char in name:
                    name = name.replace(char, "_")
            return name
        else:
            # Default format for code indices
            return f"{row.project_name}-{row.repo_name}-{row.index_type}"

    # Use raw SQL to get only needed fields
    statement = sa.text("""                                                                                                  
        SELECT id, project_name, repo_name, index_type, processed_files                                                      
        FROM index_info                                                                                                      
        WHERE processed_files = '[]'                                                                                         
        AND current_state > 0                                                                                                
    """)

    indices = connection.execute(statement).fetchall()
    es_client = ElasticSearchClient.get_client()

    for idx in indices:
        try:
            index_name = get_index_identifier(idx)
            res = es_client.search(index=index_name, query={"match_all": {}}, source=["metadata.source"], size=10000)

            sources = set()
            for hit in res["hits"]["hits"]:
                if "metadata" in hit["_source"] and "source" in hit["_source"]["metadata"]:
                    sources.add(hit["_source"]["metadata"]["source"])
                    if len(sources) >= 1000:
                        break

            # Update using raw SQL
            update_stmt = sa.text("""                                                                                        
                UPDATE index_info                                                                                            
                SET processed_files = :processed_files                                                                       
                WHERE id = :id                                                                                               
            """)
            connection.execute(update_stmt, {"id": idx.id, "processed_files": json.dumps(list(sources)[:1000])})

            logger.info(f"Updated index {idx.id} with {len(sources)} processed_files (max 1000)")

        except Exception as e:
            logger.info(f"Error updating processed_files in index {idx.id}: {str(e)}")
            continue


def downgrade() -> None:
    """Downgrade schema."""
    pass
