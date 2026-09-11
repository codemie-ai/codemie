# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an “AS IS” BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Base class for whole-document LLM-routing datasources (``llm_routing_*``).

Every LLM-routing datasource (Google Docs, Git FAQ, …) shares this single
inheritance chain: ``BaseDatasourceProcessor`` →
``BaseLLMRoutingDatasourceProcessor`` → concrete processor. The base owns the
common document shape (``content`` + ``metadata{title, content, instructions,
reference}``), table-of-contents storage in the index ``_meta``, reference-prefix
retrieval with checksum deduplication, index naming, the ES client, and the
"resume/incremental not supported" contract.

Subclasses must define ``INDEX_TYPE`` and implement ``_init_loader``,
``_init_index`` and ``_process``.
"""

import hashlib
import uuid
from datetime import datetime
from typing import Any, Dict, List

from elasticsearch import NotFoundError
from elasticsearch.helpers import bulk
from langchain_core.documents import Document

from codemie.clients.elasticsearch import ElasticSearchClient
from codemie.configs import logger
from codemie.core.models import KnowledgeBase
from codemie.datasource.base_datasource_processor import BaseDatasourceProcessor


class BaseLLMRoutingDatasourceProcessor(BaseDatasourceProcessor):
    """Shared indexing and retrieval behavior for whole-article LLM-routing datasources."""

    client = ElasticSearchClient.get_client()

    @property
    def _index_name(self) -> str:
        return KnowledgeBase(name=f"{self.project_name}-{self.datasource_name}", type=self.INDEX_TYPE).get_identifier()

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
            # stats_only=True returns an int count; fail the run loudly instead of
            # completing a partially indexed datasource as success.
            raise RuntimeError(f"Failed to add {failed} of {len(texts)} documents to index {self._index_name}")

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

    @staticmethod
    def _ref_matches(doc_ref: str, ref: str) -> bool:
        """Whether a routing selection ``ref`` resolves to the stored ``doc_ref``.

        Both arguments are non-empty, stripped references. Three cases match:

        - **Exact** — ``doc_ref == ref``.
        - **Section prefix** — ``doc_ref`` starts with ``ref``: routing to a
          section also selects its sub-sections (e.g. ``"ref1"`` → ``"ref1-sub"``).
          This is the original one-directional behavior and is preserved.
        - **Echoed TOC line** — ``ref`` starts with ``doc_ref`` *and the next
          character is whitespace*. The routing LLM frequently echoes the whole
          TOC line ``"<reference> — <title>"`` instead of the bare reference, so
          the stored reference is a whole prefix followed by the " — title"
          remainder. Requiring a whitespace boundary is what stops a shorter
          reference from swallowing a longer sibling/child: ``"1"`` must not match
          a routing selection of ``"1.1"`` (next char ``"."`` is not whitespace),
          while ``"1 — Overview"`` still resolves to ``"1"``.
        """
        if doc_ref == ref:
            return True
        if doc_ref.startswith(ref):
            return True
        return ref.startswith(doc_ref) and ref[len(doc_ref) : len(doc_ref) + 1].isspace()

    def get_documents_by_checksum(self, refs) -> dict[Any, dict[str, Any]]:
        """
        Returns list of documents by refs.

        Refs may arrive as bare references ("troubleshooting/es-oom") or as full
        TOC lines ("troubleshooting/es-oom — Elasticsearch keeps restarting…") —
        the routing LLM frequently echoes the whole line. ``_ref_matches`` resolves
        both spellings while keeping a shorter reference from over-fetching a longer
        one; empty refs are skipped.
        """
        data = self._read_chapters()
        docs = {}
        for raw_ref in refs:
            ref = str(raw_ref).strip()
            if not ref:
                continue
            for doc in data:
                doc_ref = str(doc.get("reference", "")).strip()
                if not doc_ref:
                    continue
                if self._ref_matches(doc_ref, ref):
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
        except NotFoundError:
            # Index not created yet (first run pending) or already deleted.
            return {}

    def _update_kb_info(self, version_id: str = ""):
        """
        Updates KB version in metadata
        """
        timestamp = datetime.now()

        self._update_metadata(
            {
                "kb_index_timestamp": timestamp,
            }
        )

        logger.info(f"Updated KB info: {version_id} / {timestamp}")

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
