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

from __future__ import annotations

from typing import List, Optional

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from codemie.core.models import KnowledgeBase
from codemie.datasource.base_datasource_processor import (
    BaseDatasourceProcessor,
    DatasourceProcessorCallback,
)
from codemie.datasource.datasources_config import XWIKI_CONFIG
from codemie.datasource.loader.xwiki_loader import (
    Metadata,
    XWikiLoader,
)
from codemie.rest_api.models.guardrail import GuardrailAssignmentItem
from codemie.rest_api.models.index import IndexInfo, XWikiIndexInfo
from codemie.rest_api.security.user import User
from codemie.service.llm_service.llm_service import llm_service
from codemie_tools.core.project_management.xwiki.models import XWikiConfig

# _process_chunk rebuilds metadata from scratch, so anything not listed here is dropped:
# the base class re-applies only chunk_num.
_CHUNK_METADATA_KEYS = tuple(key.value for key in Metadata)


class XWikiDatasourceProcessor(BaseDatasourceProcessor):
    INDEX_TYPE = "knowledge_base_xwiki"

    def __init__(
        self,
        *,
        datasource_name: str,
        user: Optional[User],
        project_name: str,
        credentials: XWikiConfig,
        space: str,
        wiki: str = "xwiki",
        description: str = "",
        project_space_visible: bool = False,
        index_info: Optional[IndexInfo] = None,
        callbacks: Optional[List[DatasourceProcessorCallback]] = None,
        request_uuid: Optional[str] = None,
        guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None,
        **kwargs,
    ):
        self.project_name = project_name
        self.description = description
        self.credentials = credentials
        self.space = space
        self.wiki = wiki
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
        return XWIKI_CONFIG.loader_batch_size

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
                xwiki=XWikiIndexInfo(space=self.space, wiki=self.wiki),
                embeddings_model=self.embedding_model or llm_service.default_embedding_model,
                setting_id=self.setting_id,
            )
        self._assign_and_sync_guardrails()

    def _init_loader(self) -> XWikiLoader:
        return XWikiLoader(
            config=self.credentials,
            space=self.space,
            wiki=self.wiki,
            page_size=XWIKI_CONFIG.loader_batch_size,
            max_pages=XWIKI_CONFIG.loader_max_pages,
            timeout_seconds=XWIKI_CONFIG.request_timeout_seconds,
            max_failed_pages_floor=XWIKI_CONFIG.max_failed_pages_floor,
            max_failed_pages_ratio=XWIKI_CONFIG.max_failed_pages_ratio,
        )

    def _process_chunk(self, chunk: str, chunk_metadata: dict, _document: Document) -> Document:
        metadata = {key: chunk_metadata.get(key) for key in _CHUNK_METADATA_KEYS}
        return Document(page_content=chunk, metadata=metadata)

    @classmethod
    def _get_splitter(cls, document: Optional[Document] = None) -> RecursiveCharacterTextSplitter:
        return RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            encoding_name="o200k_base",
            chunk_size=XWIKI_CONFIG.chunk_size,
            disallowed_special={},
            chunk_overlap=XWIKI_CONFIG.chunk_overlap,
        )

    def _fetch_remote_stats(self) -> dict:
        """Full remote stats from a single crawl.

        Callers that need both the count and the truncation flag must use this rather than
        calling _check_docs_health() and then building a second loader - a fresh loader's
        truncation flag is always False.
        """
        return self._init_loader().fetch_remote_stats()

    def _check_docs_health(self) -> int:
        """Deliberately unguarded: the donor swallows every exception and reports 0 documents,
        which makes a broken connection look like an empty wiki."""
        return self._fetch_remote_stats().get(XWikiLoader.DOCUMENTS_COUNT_KEY, 0)
