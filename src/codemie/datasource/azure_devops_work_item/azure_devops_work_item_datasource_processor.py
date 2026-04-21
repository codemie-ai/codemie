# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

from __future__ import annotations

from typing import List, Optional

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from codemie.configs import logger
from codemie.core.models import KnowledgeBase
from codemie.datasource.base_datasource_processor import (
    BaseDatasourceProcessor,
    DatasourceProcessorCallback,
)
from codemie.datasource.datasources_config import AZURE_DEVOPS_WORK_ITEM_CONFIG
from codemie.datasource.loader.azure_devops_work_item_loader import AzureDevOpsWorkItemLoader
from codemie.rest_api.models.guardrail import GuardrailAssignmentItem
from codemie.rest_api.models.index import AzureDevOpsWorkItemIndexInfo, IndexInfo
from codemie.rest_api.models.settings import AzureDevOpsCredentials
from codemie.rest_api.security.user import User
from codemie.service.llm_service.llm_service import llm_service


class AzureDevOpsWorkItemDatasourceProcessor(BaseDatasourceProcessor):
    INDEX_TYPE = "knowledge_base_azure_devops_work_item"

    def __init__(
        self,
        *,
        datasource_name: str,
        user: User | None,
        project_name: str,
        credentials: AzureDevOpsCredentials,
        wiql_query: str,
        description: str = "",
        project_space_visible: bool = False,
        index_info: Optional[IndexInfo] = None,
        callbacks: Optional[list[DatasourceProcessorCallback]] = None,
        request_uuid: Optional[str] = None,
        guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None,
        **kwargs,
    ):
        self.project_name = project_name
        self.description = description
        self.credentials = credentials
        self.wiql_query = wiql_query
        self.project_space_visible = project_space_visible
        self.setting_id = kwargs.get("setting_id")
        self.embedding_model = kwargs.get("embedding_model")

        super().__init__(
            datasource_name=datasource_name,
            user=user,
            index=index_info,
            callbacks=callbacks,
            request_uuid=request_uuid,
            guardrail_assignments=guardrail_assignments,
            cron_expression=kwargs.get("cron_expression"),
        )

    @property
    def _index_name(self) -> str:
        return KnowledgeBase(name=f"{self.project_name}-{self.datasource_name}", type=self.INDEX_TYPE).get_identifier()

    @property
    def _processing_batch_size(self) -> int:
        return AZURE_DEVOPS_WORK_ITEM_CONFIG.loader_batch_size

    def _cleanup_data(self):
        """Remove all data in the index. Used for full reindexing."""
        try:
            self.client.delete_by_query(
                index=self._index_name,
                body={"query": {"match_all": {}}},
                wait_for_completion=True,
                refresh=True,
            )
            logger.info(f"Successfully deleted index with data: {self._index_name}")
        except Exception as e:
            logger.error(f"Failed deleting index with data: {e}")

    def _init_index(self):
        """Initialize or retrieve the index."""
        if not self.index:
            self.index = IndexInfo.new(
                repo_name=self.datasource_name,
                full_name=self.datasource_name,
                project_name=self.project_name,
                description=self.description,
                project_space_visible=self.project_space_visible,
                index_type=self.INDEX_TYPE,
                user=self.user,
                azure_devops_work_item=AzureDevOpsWorkItemIndexInfo(wiql_query=self.wiql_query),
                embeddings_model=self.embedding_model or llm_service.default_embedding_model,
                setting_id=self.setting_id,
            )

        self._assign_and_sync_guardrails()

    def _init_loader(self):
        """Initialize Azure DevOps Work Item loader with an optional vision-capable chat model."""
        chat_model = None
        try:
            from codemie.core.dependecies import get_llm_by_credentials

            multimodal_llms = llm_service.get_multimodal_llms()
            if multimodal_llms:
                chat_model = get_llm_by_credentials(llm_model=multimodal_llms[0], streaming=False)
        except Exception as e:
            logger.warning(f"Could not initialise vision model for work item attachment indexing: {e}")

        return AzureDevOpsWorkItemLoader(
            base_url=self.credentials.base_url,
            wiql_query=self.wiql_query,
            access_token=self.credentials.access_token,
            organization=self.credentials.organization,
            project=self.credentials.project,
            batch_size=AZURE_DEVOPS_WORK_ITEM_CONFIG.loader_batch_size,
            chat_model=chat_model,
            index_comments=AZURE_DEVOPS_WORK_ITEM_CONFIG.index_comments,
            index_attachments=AZURE_DEVOPS_WORK_ITEM_CONFIG.index_attachments,
        )

    def _process_chunk(self, chunk: str, chunk_metadata, document: Document) -> Document:
        """Process a chunk and return a Document with metadata."""
        source = document.metadata.get("source", "")
        work_item_id = document.metadata.get("work_item_id", "")
        work_item_type = document.metadata.get("work_item_type", "")
        state = document.metadata.get("state", "")
        title = document.metadata.get("title", "")

        metadata: dict = {
            "source": source,
            "work_item_id": work_item_id,
            "work_item_type": work_item_type,
            "state": state,
            "title": title,
        }

        # Preserve content_type and attachment/comment metadata when present
        content_type = document.metadata.get("content_type")
        if content_type:
            metadata["content_type"] = content_type
        for key in ("attachment_name", "attachment_mime_type", "attachment_summary", "summary"):
            if key in document.metadata:
                metadata[key] = document.metadata[key]

        return Document(page_content=chunk, metadata=metadata)

    @classmethod
    def _get_splitter(cls, document: Optional[Document] = None) -> RecursiveCharacterTextSplitter:
        """Return text splitter for work item documents."""
        return RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            encoding_name="o200k_base",
            chunk_size=AZURE_DEVOPS_WORK_ITEM_CONFIG.chunk_size,
            disallowed_special={},
            chunk_overlap=AZURE_DEVOPS_WORK_ITEM_CONFIG.chunk_overlap,
        )

    def _check_docs_health(self) -> int:
        """Health check to verify connection and count available work items."""
        try:
            loader = self._init_loader()
            stats = loader.fetch_remote_stats()
            return stats.get(AzureDevOpsWorkItemLoader.DOCUMENTS_COUNT_KEY, 0)
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            raise
