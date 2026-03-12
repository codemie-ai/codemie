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

from datetime import datetime
from typing import List, Optional

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from requests.exceptions import HTTPError

from codemie.configs import logger
from codemie.core.models import KnowledgeBase
from codemie.datasource.base_datasource_processor import (
    BaseDatasourceProcessor,
    DatasourceProcessorCallback,
)
from codemie.datasource.datasources_config import JIRA_CONFIG
from codemie.datasource.exceptions import EmptyResultException, InvalidQueryException
from codemie.datasource.loader.jira_loader import JiraLoader
from codemie.rest_api.models.guardrail import GuardrailAssignmentItem
from codemie.rest_api.models.index import (
    IndexInfo,
    JiraIndexInfo,
)
from codemie.rest_api.security.user import User
from codemie.service.llm_service.llm_service import llm_service
from codemie_tools.core.project_management.jira.models import JiraConfig


class JiraDatasourceProcessor(BaseDatasourceProcessor):
    INDEX_TYPE = "knowledge_base_jira"

    def __init__(
        self,
        *,
        datasource_name: str,
        user: User,
        project_name: str,
        credentials: JiraConfig,
        jql: str,
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
        self.jql = jql
        self.project_space_visible = project_space_visible
        self.setting_id = kwargs.get('setting_id')
        self.embedding_model = kwargs.get('embedding_model')

        super().__init__(
            datasource_name=datasource_name,
            user=user,
            index=index_info,
            callbacks=callbacks,
            request_uuid=request_uuid,
            guardrail_assignments=guardrail_assignments,
            cron_expression=kwargs.get('cron_expression'),
        )

    @property
    def _index_name(self) -> str:
        return KnowledgeBase(name=f"{self.project_name}-{self.datasource_name}", type=self.INDEX_TYPE).get_identifier()

    @property
    def _processing_batch_size(self) -> int:
        return JIRA_CONFIG.loader_batch_size

    def _cleanup_data(self):
        """Remove all data in the index. Used for full reindexing"""
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

    def _cleanup_data_for_incremental_reindex(self, docs_to_be_indexed: list[Document]):
        """Remove data by doc Jira ticket number."""
        updated_keys = [doc.metadata["key"] for doc in docs_to_be_indexed]
        try:
            self.client.delete_by_query(
                index=self._index_name,
                body={"query": {"terms": {"metadata.key.keyword": updated_keys}}},
                wait_for_completion=True,
                refresh=True,
            )
            logger.info("Successfully deleted data from index")
        except Exception as e:
            logger.error(f"Failed deleting data from index: {e}")

    def _init_loader(self):
        # For incremental reindex: use date preserved before update_index()
        update_date = None
        if self.is_incremental_reindex and self.index and self.index.last_reindex_date:
            update_date = self.index.last_reindex_date
        elif self.index:
            update_date = self.index.update_date

        loader = JiraLoader(
            jql=self.jql,
            **self._get_loader_args(self.credentials, self.is_incremental_reindex, update_date),
        )
        return loader

    def _init_index(self):
        if not self.index:
            self.index = IndexInfo.new(
                repo_name=self.datasource_name,
                full_name=self.datasource_name,
                project_name=self.project_name,
                description=self.description,
                project_space_visible=self.project_space_visible,
                index_type=self.INDEX_TYPE,
                user=self.user,
                jira=JiraIndexInfo(jql=self.jql),
                embeddings_model=self.embedding_model or llm_service.default_embedding_model,
                setting_id=self.setting_id,
            )

        self._assign_and_sync_guardrails()

    def _process_chunk(self, chunk: str, chunk_metadata, document: Document) -> Document:
        source = document.metadata["source"]
        key = document.metadata["key"]
        return Document(page_content=chunk, metadata={"source": source, "key": key})

    @classmethod
    def _get_splitter(cls, document: Optional[Document] = None) -> RecursiveCharacterTextSplitter:
        return RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            encoding_name="o200k_base",
            chunk_size=JIRA_CONFIG.chunk_size,
            disallowed_special={},
            chunk_overlap=JIRA_CONFIG.chunk_overlap,
        )

    @classmethod
    def validate_creds_and_loader(cls, jql: str, credentials: JiraConfig) -> dict[str, int]:
        loader = JiraLoader(jql=jql, **cls._get_loader_args(credentials))
        try:
            stats = loader.fetch_remote_stats()
            return stats
        except HTTPError as e:
            logger.error(f"Cannot parse JQL: {jql}. Failed with error {e}")
            raise InvalidQueryException("JQL", str(e))

    @classmethod
    def check_jira_query(cls, jql: str, credentials: JiraConfig):
        if not jql or not jql.strip():
            logger.error("JQL was not provided")
            raise InvalidQueryException("JQL", "There is no JQL expression")
        index_stats = cls.validate_creds_and_loader(jql=jql, credentials=credentials)
        docs_count = index_stats.get(JiraLoader.DOCUMENTS_COUNT_KEY, 0)

        if not docs_count:
            logger.error(f"Empty result returned for this query: {jql}.")
            raise EmptyResultException("JQL")

        return docs_count

    @classmethod
    def _get_loader_args(
        cls,
        credentials: JiraConfig,
        incremental_reindex: Optional[bool] = False,
        update_date: Optional[datetime] = None,
    ):
        """Returns credentials based on Jira type"""
        if credentials.cloud:
            args = {
                "cloud": True,
                "url": credentials.url,
                "username": credentials.username,
                "password": credentials.token,
            }
        else:
            args = {
                "cloud": False,
                "url": credentials.url,
                "token": credentials.token,
            }

        if incremental_reindex:
            args = {**args, "updated_gte": update_date}

        return args
