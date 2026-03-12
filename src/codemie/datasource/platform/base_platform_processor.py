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
from typing import Optional, List
from uuid import uuid4

from langchain_core.documents import Document

from codemie.datasource.base_datasource_processor import (
    BaseDatasourceProcessor,
    DatasourceProcessorCallback,
)
from codemie.rest_api.models.index import IndexInfo
from codemie.rest_api.security.user import User
from codemie.core.models import KnowledgeBase, SYSTEM_USER, CreatedByUser
from codemie.configs import logger
from codemie.clients.elasticsearch import ElasticSearchClient
from codemie.service.llm_service.llm_service import llm_service


class BasePlatformDatasourceProcessor(BaseDatasourceProcessor):
    """
    Base processor for platform system resources (assistants, workflows, datasources).
    Works similarly to GoogleDocDatasourceProcessor but with platform data.

    This processor:
    - Creates/manages IndexInfo records with project_space_visible=False (hidden from GET /index)
    - Indexes platform entities into Elasticsearch
    - Supports both full sync (all entities) and incremental updates (single entity)
    - Uses platform loaders to fetch and sanitize entities
    """

    # Must be defined in subclasses
    INDEX_TYPE: Optional[str] = None
    SYSTEM_PROJECT: str = "codemie"

    def __init__(
        self,
        *,
        datasource_name: str,
        project_name: Optional[str] = None,
        user: Optional[User] = None,
        index_info: Optional[IndexInfo] = None,
        callbacks: Optional[list[DatasourceProcessorCallback]] = None,
        request_uuid: Optional[str] = None,
        embedding_model: Optional[str] = None,
    ):
        if not self.INDEX_TYPE:
            raise ValueError("INDEX_TYPE must be defined in subclass")

        self.embedding_model = embedding_model
        self.client = ElasticSearchClient.get_client()
        self.project_name = project_name if project_name else self.SYSTEM_PROJECT

        # Use SYSTEM_USER if user is not provided (for system datasources)
        effective_user = user or SYSTEM_USER
        effective_callbacks = callbacks or []

        super().__init__(
            datasource_name=datasource_name,
            user=effective_user,
            index=index_info,
            callbacks=effective_callbacks,
            request_uuid=request_uuid,
        )

    @property
    def _index_name(self) -> str:
        """Generate Elasticsearch index name."""
        return KnowledgeBase(name=self.datasource_name, type=self.INDEX_TYPE).get_identifier()

    @abstractmethod
    def _init_loader(self):
        """Initialize loader. Must be implemented in subclasses."""
        pass

    def _init_index(self):
        """Create or get existing IndexInfo record."""
        if self.index:
            return

        existing = IndexInfo.filter_by_project_and_repo(project_name=self.project_name, repo_name=self.datasource_name)

        if existing:
            self.index = existing[0]
            logger.info(
                f"Found existing platform datasource: {self.index.id}",
                extra={
                    "datasource_name": self.datasource_name,
                    "index_type": self.INDEX_TYPE,
                    "index_id": self.index.id,
                },
            )
            return

        embedding_model_name = self.embedding_model or llm_service.default_embedding_model
        embedding_model_details = llm_service.get_model_details(embedding_model_name)

        # Convert User to CreatedByUser for IndexInfo
        created_by_user = CreatedByUser(
            id=self.user.id,
            username=self.user.username,
            name=self.user.name,
        )

        self.index = IndexInfo(
            repo_name=self.datasource_name,
            project_name=self.SYSTEM_PROJECT,
            description=f"Platform {self.datasource_name}\nIf document contain details url - use it in answer",
            index_type=self.INDEX_TYPE,
            embeddings_model=embedding_model_details.base_name,
            current_state=0,
            complete_state=0,
            completed=False,
            created_by=created_by_user,
        )
        self.index.save()
        logger.info(
            f"Created new platform datasource: {self.index.id}",
            extra={
                "datasource_name": self.datasource_name,
                "index_type": self.INDEX_TYPE,
                "index_id": self.index.id,
            },
        )

    def _process(self) -> int:
        """Main indexing process."""
        documents: List[Document] = list(self.loader.lazy_load())

        if not documents:
            logger.warning(
                f"No documents to index for {self.datasource_name}",
                extra={"datasource_name": self.datasource_name, "index_type": self.INDEX_TYPE},
            )
            return 0

        self._apply_guardrails_for_documents(documents)
        self._add_documents(documents)

        return len(documents)

    def _on_process_start(self):
        """Callback before processing starts."""
        if self.is_resume_indexing:
            raise NotImplementedError(f"Resume indexing is not supported for {self.INDEX_TYPE}")
        if self.is_incremental_reindex:
            raise NotImplementedError(f"Incremental reindex is not supported for {self.INDEX_TYPE}")

    @staticmethod
    def _get_store_by_index(index_name: str, embeddings_model: str):
        """Get Elasticsearch vector store for the given index and embeddings model."""
        from codemie.core.dependecies import get_elasticsearch

        return get_elasticsearch(index_name, embeddings_model)

    def _add_documents(self, documents: List[Document], is_update: bool = False) -> None:
        """Index documents into Elasticsearch with embeddings."""

        embeddings_model = llm_service.get_embedding_deployment_name(self.index.embeddings_model)
        store = self._get_store_by_index(self._index_name, embeddings_model)
        store._store._create_index_if_not_exists()

        ids = [doc.metadata.get('id', str(uuid4())) for doc in documents]
        store.add_documents(documents=documents, ids=ids)

        # Update progress only if this is a new document, not an update
        if not is_update:
            for doc in documents:
                entity_name = doc.metadata.get('name', 'unknown')
                self.index.move_progress(chunks_count=1, processed_file=entity_name)

        logger.info(
            f"Successfully indexed {len(documents)} documents with embeddings in {self._index_name}",
            extra={"success_count": len(documents), "index_name": self._index_name},
        )

    def index_single_entity(self, entity_id: str, is_update: bool = False) -> None:
        """
        Index a single entity (called on publish or update).

        Args:
            entity_id: ID of entity to index
            is_update: If True, this is an update of an existing entity (won't increment progress counters)
        """
        if not self.index:
            self._init_index()
        if not self.loader:
            self.loader = self._init_loader()

        document = self.loader.load_single_entity(entity_id)

        if not document:
            logger.warning(
                f"Entity {entity_id} not found or not eligible for indexing",
                extra={"entity_id": entity_id, "datasource": self.datasource_name},
            )
            return

        # Index with is_update flag to control progress tracking
        self._add_documents([document], is_update=is_update)

    def remove_single_entity(self, entity_id: str, entity_name: str) -> None:
        """
        Remove a single entity from index (called on unpublish).

        Args:
            entity_id: ID of entity to remove
            entity_name: name of entity to remove
        """
        if not self.index:
            self._init_index()

        logger.info(
            f"Removing entity from index: {entity_id}",
            extra={"entity_id": entity_id, "index_name": self._index_name},
        )

        try:
            self.client.delete(index=self._index_name, id=entity_id)

            # Update index metadata to reflect document removal
            self.index.decrease_progress(chunks_count=1, processed_file=entity_name)

            logger.info(
                f"Successfully removed entity {entity_id} from index",
                extra={"entity_id": entity_id, "index_name": self._index_name},
            )
        except Exception as e:
            logger.error(
                f"Failed to remove entity {entity_id}: {e}",
                extra={"entity_id": entity_id, "error": str(e)},
                exc_info=True,
            )
            raise
