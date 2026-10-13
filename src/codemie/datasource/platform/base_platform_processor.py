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
from langchain_text_splitters import RecursiveCharacterTextSplitter

from codemie.datasource.base_datasource_processor import (
    BaseDatasourceProcessor,
    DatasourceProcessorCallback,
)
from codemie.datasource.datasources_config import STORAGE_CONFIG
from codemie.datasource.loader.platform.platform_loader import BasePlatformLoader
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
    # Chunk body budget; with the identifying header prepended, chunks stay well under embedding limits
    CHUNK_TOKENS: int = 1500
    CHUNK_OVERLAP_TOKENS: int = 100

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

        # Guardrails run on the final chunks: exactly the text that is embedded, header included
        chunks = self._split_entity_documents(documents)
        self._apply_guardrails_for_documents(chunks)
        self._add_documents(chunks)

        return len(documents)

    @classmethod
    def _get_splitter(cls, document: Document = None) -> RecursiveCharacterTextSplitter:
        """Token-based splitter for platform entities (used by the base _split_documents)."""
        return RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            encoding_name="o200k_base",
            chunk_size=cls.CHUNK_TOKENS,
            chunk_overlap=cls.CHUNK_OVERLAP_TOKENS,
            disallowed_special={},
        )

    def _process_chunk(self, chunk: str, chunk_metadata, _document: Document) -> Document:
        """Prepend the entity's identifying header to every chunk; the header is not stored as metadata."""
        header = chunk_metadata.pop(BasePlatformLoader.CHUNK_HEADER_KEY, None)
        page_content = f"{header}\n\n{chunk}" if header else chunk
        return Document(page_content=page_content, metadata=chunk_metadata)

    def _split_entity_documents(self, documents: List[Document]) -> List[Document]:
        """Split whole entity documents into chunks with the base pipeline, numbering 1..N per entity."""
        chunks: List[Document] = []
        for document in documents:
            # Numbering is per entity: entities may share a name (the base numbering key)
            self._reset_chunk_numbering()
            for entity_chunks in self._split_documents([document]).values():
                chunks.extend(entity_chunks)
        return chunks

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

    def _add_documents(self, documents: List[Document], is_update: bool = False, fail_fast: bool = False) -> None:
        """Index entity chunk documents into Elasticsearch with embeddings.

        Chunks are grouped per entity (metadata 'id'). By default groups are embedded in batches; a failing
        batch is retried per entity so one bad entity is skipped instead of failing the whole index.
        Updates and single-entity indexing (fail_fast) propagate errors to the caller.
        After an entity is stored, its stale documents (legacy or leftover chunks) are removed.
        """

        embeddings_model = llm_service.get_embedding_deployment_name(self.index.embeddings_model)
        store = self._get_store_by_index(self._index_name, embeddings_model)
        store._store._create_index_if_not_exists()

        groups = self._group_by_entity(documents)

        if is_update or fail_fast:
            self._store_groups(store, groups)
            # Update progress only if this is a new entity, not an update
            if not is_update:
                self._move_progress_for_groups(groups)
            logger.info(
                f"Successfully indexed {len(documents)} chunks with embeddings in {self._index_name}",
                extra={"success_count": len(documents), "index_name": self._index_name},
            )
            return

        stored_groups: List[List[Document]] = []
        for batch in self._batch_groups(groups, STORAGE_CONFIG.embeddings_max_docs_count):
            stored_groups.extend(self._store_batch_with_fallback(store, batch))

        self._move_progress_for_groups(stored_groups)

        logger.info(
            f"Indexed {len(stored_groups)} of {len(groups)} entities with embeddings in {self._index_name}",
            extra={
                "success_count": len(stored_groups),
                "skipped_count": len(groups) - len(stored_groups),
                "chunks_count": sum(len(group) for group in stored_groups),
                "index_name": self._index_name,
            },
        )

    @staticmethod
    def _entity_id(doc: Document) -> str:
        return doc.metadata.get('id') or str(uuid4())

    @classmethod
    def _chunk_doc_id(cls, doc: Document) -> str:
        """Elasticsearch document id of a chunk: '<entity id>_<chunk num>'."""
        return f"{cls._entity_id(doc)}_{doc.metadata.get('chunk_num', 0)}"

    @classmethod
    def _group_by_entity(cls, documents: List[Document]) -> List[List[Document]]:
        """Group chunk documents by entity id, preserving order."""
        groups: dict[str, List[Document]] = {}
        for doc in documents:
            groups.setdefault(cls._entity_id(doc), []).append(doc)
        return list(groups.values())

    @staticmethod
    def _batch_groups(groups: List[List[Document]], max_docs: int) -> List[List[List[Document]]]:
        """Pack entity groups into batches of at most max_docs chunks; an oversized group forms its own batch."""
        batches: List[List[List[Document]]] = []
        current: List[List[Document]] = []
        current_size = 0
        for group in groups:
            if current and current_size + len(group) > max_docs:
                batches.append(current)
                current, current_size = [], 0
            current.append(group)
            current_size += len(group)
        if current:
            batches.append(current)
        return batches

    def _store_groups(self, store, groups: List[List[Document]]) -> None:
        """Store entity groups in one call, then remove their stale documents. Errors propagate."""
        documents = [doc for group in groups for doc in group]
        chunk_ids = [self._chunk_doc_id(doc) for doc in documents]
        store.add_documents(documents=documents, ids=chunk_ids)
        self._delete_stale_chunks([self._entity_id(group[0]) for group in groups], chunk_ids)

    def _store_batch_with_fallback(self, store, groups: List[List[Document]]) -> List[List[Document]]:
        """Store a batch of entity groups; on failure retry each entity alone and skip the ones that still fail."""
        try:
            self._store_groups(store, groups)
            return groups
        except Exception as batch_error:
            if len(groups) == 1:
                self._log_skipped_document(groups[0][0], self._entity_id(groups[0][0]), batch_error)
                return []

        stored: List[List[Document]] = []
        for group in groups:
            try:
                self._store_groups(store, [group])
                stored.append(group)
            except Exception as group_error:
                self._log_skipped_document(group[0], self._entity_id(group[0]), group_error)
        return stored

    def _delete_stale_chunks(self, entity_ids: List[str], keep_ids: List[str]) -> None:
        """Remove documents of the given entities that were not just written (legacy docs, leftover chunks)."""
        try:
            self.client.delete_by_query(
                index=self._index_name,
                body={
                    "query": {
                        "bool": {
                            "filter": [{"terms": {"metadata.id.keyword": entity_ids}}],
                            "must_not": [{"ids": {"values": keep_ids}}],
                        }
                    }
                },
                conflicts="proceed",
            )
        except Exception as e:
            logger.warning(
                f"Failed to remove stale chunks from {self._index_name}: {e}",
                extra={"entity_ids": entity_ids, "index_name": self._index_name, "error": str(e)},
            )

    def _move_progress_for_groups(self, groups: List[List[Document]]) -> None:
        for group in groups:
            self.index.move_progress(chunks_count=len(group), processed_file=group[0].metadata.get('name', 'unknown'))

    def _log_skipped_document(self, doc: Document, doc_id: str, error: Exception) -> None:
        logger.warning(
            f"Skipping {self.datasource_name} entity {doc_id} after embedding failure: {error}",
            extra={
                "entity_id": doc_id,
                "entity_name": doc.metadata.get('name', 'unknown'),
                "datasource_name": self.datasource_name,
                "error": str(error),
            },
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

        # Index with is_update flag to control progress tracking; single-entity failures must reach the caller
        self._add_documents(self._split_entity_documents([document]), is_update=is_update, fail_fast=True)

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
            # Remove every chunk of the entity (and a legacy single document) by its metadata id
            response = self.client.delete_by_query(
                index=self._index_name,
                body={"query": {"term": {"metadata.id.keyword": entity_id}}},
                conflicts="proceed",
                refresh=True,
            )
            # ObjectApiResponse (real client) and dict both support .get
            deleted = response.get("deleted", 0) or 0

            if not deleted:
                logger.warning(
                    f"No indexed documents found for entity {entity_id}",
                    extra={"entity_id": entity_id, "index_name": self._index_name},
                )
                return

            # Update index metadata to reflect document removal
            self.index.decrease_progress(chunks_count=deleted, processed_file=entity_name)

            logger.info(
                f"Successfully removed entity {entity_id} ({deleted} chunks) from index",
                extra={"entity_id": entity_id, "chunks_count": deleted, "index_name": self._index_name},
            )
        except Exception as e:
            logger.error(
                f"Failed to remove entity {entity_id}: {e}",
                extra={"entity_id": entity_id, "error": str(e)},
                exc_info=True,
            )
            raise
