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
import hashlib
import re
import uuid
from typing import Any, Dict, List, Optional

from elasticsearch.helpers import bulk
from langchain_core.documents import Document

from codemie.rest_api.models.guardrail import GuardrailAssignmentItem
from codemie.rest_api.security.user import User
from codemie.datasource.base_datasource_processor import (
    BaseDatasourceProcessor,
    DatasourceProcessorCallback,
)
from codemie.rest_api.models.index import (
    IndexInfo,
)
from codemie.service.llm_service.llm_service import llm_service
from codemie.core.models import KnowledgeBase
from codemie.configs import logger
from codemie.datasource.loader.google_doc_loader import GoogleDocLoader
from codemie.clients.elasticsearch import ElasticSearchClient
from codemie.service.constants import FullDatasourceTypes


class GoogleDocDatasourceProcessor(BaseDatasourceProcessor):
    client = ElasticSearchClient.get_client()
    INDEX_TYPE = FullDatasourceTypes.GOOGLE.value

    def __init__(
        self,
        *,
        datasource_name: str,
        project_name: str,
        google_doc: str,
        description: str = "",
        project_space_visible: bool = False,
        user: Optional[User] = None,
        index_info: Optional[IndexInfo] = None,
        callbacks: Optional[list[DatasourceProcessorCallback]] = None,
        request_uuid: Optional[str] = None,
        embedding_model: Optional[str] = None,
        guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None,
        cron_expression: Optional[str] = None,
    ):
        self.project_name = project_name
        self.description = description
        self.google_doc = google_doc
        self.product_id = self._parse_google_doc_id(google_doc)
        self.project_space_visible = project_space_visible
        self.embedding_model = embedding_model

        super().__init__(
            datasource_name=datasource_name,
            user=user,
            index=index_info,
            callbacks=callbacks,
            request_uuid=request_uuid,
            guardrail_assignments=guardrail_assignments,
            cron_expression=cron_expression,
        )

    @property
    def _index_name(self) -> str:
        return KnowledgeBase(name=f"{self.project_name}-{self.datasource_name}", type=self.INDEX_TYPE).get_identifier()

    def _init_loader(self):
        return GoogleDocLoader(product_id=self.product_id)

    def _init_index(self):
        if not self.index:
            # this also handles index creation if it not exists
            self.index = IndexInfo.new(
                repo_name=self.datasource_name,
                full_name=self.datasource_name,
                project_name=self.project_name,
                description=self.description,
                project_space_visible=self.project_space_visible,
                index_type=self.INDEX_TYPE,
                user=self.user,
                google_doc_link=self.google_doc,
                embeddings_model=self.embedding_model or llm_service.default_embedding_model,
                setting_id=None,
            )

        self._assign_and_sync_guardrails()

    def _process(self) -> int:
        (
            documents,
            titles,
            document_id,
        ) = self.loader.load_with_extra()

        # to add ability to reindex old indicies
        if not documents:
            raise ValueError("Trying to index empty datasource, use different URL or document")

        self._apply_guardrails_for_documents(documents)
        self._add_documents(documents)
        self._update_kb_info(document_id)
        self._save_table_of_contents(titles)
        return len(documents)

    def _on_process_start(self):
        if self.is_resume_indexing:
            raise NotImplementedError(f"Resume indexing is not supported for {self.INDEX_TYPE}")
        if self.is_incremental_reindex:
            raise NotImplementedError(f"Incremental reindex is not supported for {self.INDEX_TYPE}")

    def _add_documents(self, documents: List[Document]) -> List[str]:
        """
        Structure documents and add them to elasticsearch index.

        Args:
            documents (List[Document]: Documents to add to the vectorstore.

        Returns:
            List[str]: List of IDs of the added texts.
        """
        texts = [f"{doc.metadata['title']}\n{doc.page_content}" for doc in documents]
        metadata = [doc.metadata for doc in documents]

        logger.info(f"Adding {len(documents)} documents to index {self._index_name}...")
        self._add_texts(texts, metadata)
        logger.info(f"Added {len(documents)} documents to index {self._index_name}.")

    @classmethod
    def _parse_google_doc_id(cls, url):
        document_id_regex = r".*/d/([a-zA-Z0-9-_]+)/edit.*"
        match = re.search(document_id_regex, url)
        if match:
            return match.group(1)
        else:
            logger.error(f"Invalid Google Doc URL field {url}")
            return ""

    def _add_texts(self, texts: List[str], metadatas: List[Dict[str, Any]]):
        requests = []
        ids = [str(uuid.uuid4()) for _ in texts]

        for i, text in enumerate(texts):
            metadata = metadatas[i] if metadatas else {}
            requests.append(
                {
                    "_op_type": "index",
                    "_index": self._index_name,
                    "content": text,
                    "metadata": metadata,
                    "_id": ids[i],
                }
            )
            self.index.move_progress(chunks_count=1, processed_file=metadata["title"])

        _, failed = bulk(self.client, requests, stats_only=True, refresh=True)
        if failed:
            logger.error(f"Failed to add {len(failed)} documents to index {self._index_name}")

    def _save_table_of_contents(self, titles: List[str]) -> List[str]:
        """
        Saves table of contents (chapters) to be reused in routing
        """
        self._update_metadata({"table_of_contents": titles})
        logger.info("Saved table of contents to index metadata")

    def _update_metadata(self, new_metadata: Dict[str, Any]) -> None:
        current_metadata = self.get_metadata()
        updated_metadata = {**current_metadata, **new_metadata}
        self.client.indices.put_mapping(index=self._index_name, body={"_meta": updated_metadata})

    def get_documents_by_checksum(self, refs) -> dict[Any, dict[str, Any]]:
        """
        Returns list of documents by refs.
        """
        data = self._read_chapters()
        docs = {}
        for ref in refs:
            for doc in data:
                if doc["reference"].startswith(ref):
                    # we use checksum to merge documents with the same content
                    doc["content_checksum"] = hashlib.sha512(doc["content"].encode("utf-8")).hexdigest()
                    try:
                        docs[doc["content_checksum"]]["title"] += "; " + doc["title"]
                    except KeyError:
                        docs[doc["content_checksum"]] = doc
        return docs

    def get_table_of_contents(self):
        """
        Returns table of contents (chapters)
        """
        metadata = self.get_metadata()

        if "table_of_contents" not in metadata:
            return []

        return metadata["table_of_contents"]

    def get_metadata(self):
        try:
            mapping = self.client.indices.get_mapping(index=self._index_name)
            return mapping[self._index_name]["mappings"]["_meta"]
        except KeyError:
            return {}

    def _update_kb_info(self, document_id):
        """
        Updates KB version in metadata
        """
        timestamp = datetime.now()

        self._update_metadata(
            {
                "kb_index_timestamp": timestamp,
            }
        )

        logger.info(f"Updated KB info: {document_id} / {timestamp}")

    def _read_chapters(self) -> List[Dict[str, Any]]:
        chapters: List[Dict[str, Any]] = []
        response = self.client.search(
            index=self._index_name,
            size=1000,
            query={"match_all": {}},
        )

        for hit in response["hits"]["hits"]:
            chapters.append(hit["_source"]["metadata"])
        return chapters

    @classmethod
    def validate_google_doc_and_loader(cls, product_id: str) -> dict[str, int]:
        loader = GoogleDocLoader(product_id=product_id)
        try:
            stats = loader.fetch_remote_stats()
            return stats
        except Exception as e:
            msg = f"Cannot parse google doc by product_id {product_id}."
            logger.error(msg + f" Failed with error {e}")
            raise ValueError(msg)

    @classmethod
    def check_google_doc(cls, product_id: str):
        index_stats = cls.validate_google_doc_and_loader(product_id=product_id)
        docs_count = index_stats.get(GoogleDocLoader.DOCUMENTS_COUNT_KEY, 0)

        if not docs_count:
            msg = f"Empty result returned for given google doc with product_id: {product_id}."
            logger.warning(msg)
            raise ValueError(msg)

        return docs_count
