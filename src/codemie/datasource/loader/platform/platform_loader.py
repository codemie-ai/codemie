# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from abc import abstractmethod
from typing import Any, Iterator, List, Optional
from langchain_core.documents import Document
from codemie.datasource.loader.base_datasource_loader import BaseDatasourceLoader


class BasePlatformLoader(BaseDatasourceLoader):
    """
    Base loader for platform system objects (assistants, workflows, datasources).
    Works with data from PostgreSQL rather than external sources.

    This loader is designed to index platform entities into Elasticsearch for marketplace search.
    Each subclass should implement methods to:
    - Fetch entities from database
    - Sanitize entities (remove private fields like credentials, MCP servers)
    - Convert entities to searchable documents
    """

    def __init__(self):
        self.entities = []

    @abstractmethod
    def _fetch_entities(self) -> List[Any]:
        """
        Fetch entities from database (assistants/workflows/datasources).
        Must be implemented in subclasses.

        Returns:
            List of entity objects from database
        """
        pass

    @abstractmethod
    def _sanitize_entity(self, entity: Any) -> dict:
        """
        Remove private fields from entity before indexing.
        Must be implemented in subclasses.

        This method should remove:
        - Credentials and API keys
        - Private MCP server configurations
        - Internal relationship IDs
        - Any other sensitive or non-public information

        Args:
            entity: The entity to sanitize

        Returns:
            Dictionary with sanitized entity data
        """
        pass

    @abstractmethod
    def _entity_to_document(self, entity: Any) -> Document:
        """
        Convert entity to LangChain Document for indexing.
        Must be implemented in subclasses.

        The document should have:
        - page_content: Text for embeddings (name, description, etc.)
        - metadata: All sanitized fields for filtering and retrieval

        Args:
            entity: The entity to convert

        Returns:
            LangChain Document ready for indexing
        """
        pass

    @abstractmethod
    def load_single_entity(self, entity_id: str) -> Optional[Document]:
        """
        Load a single entity by ID and convert to document.
        Used for incremental indexing (publish/unpublish).

        Args:
            entity_id: ID of the entity to load

        Returns:
            LangChain Document or None if entity not found or not eligible
        """
        pass

    def lazy_load(self) -> Iterator[Document]:
        """Load all entities and convert them to documents."""
        self.entities = self._fetch_entities()

        for entity in self.entities:
            doc = self._entity_to_document(entity)
            yield doc

    def fetch_remote_stats(self) -> dict[str, Any]:
        """Return statistics about the number of entities."""
        if not self.entities:
            self.entities = self._fetch_entities()

        total_count = len(self.entities)

        return {
            self.DOCUMENTS_COUNT_KEY: total_count,
            self.TOTAL_DOCUMENTS_KEY: total_count,
            self.SKIPPED_DOCUMENTS_KEY: 0,
        }
