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

from typing import Any, List, Tuple, Iterator

from langchain_core.documents import Document
from langchain_core.document_loaders import BaseLoader

from codemie.datasource.loader.base_datasource_loader import BaseDatasourceLoader
from codemie.datasource.loader.util import AssistantKBGoogleDocToJsonParser


class GoogleDocLoader(BaseLoader, BaseDatasourceLoader):
    def __init__(self, *, product_id: str) -> None:
        self.kb_document_parser = AssistantKBGoogleDocToJsonParser(product_id)

    def lazy_load(self) -> Iterator[Document]:
        """Implements this method just for a base class, not really used."""
        articles, titles, document_id = self.kb_document_parser.parse_doc()
        for article in articles:
            doc = Document(page_content=article["content"], metadata=article)
            yield self._normalize_doc(doc)

    def load_with_extra(self) -> Tuple[List[Document], List[str], str]:
        """Provides extra information to be used for LLM routing."""
        articles, titles, document_id = self.kb_document_parser.parse_doc()
        docs = [Document(page_content=x["content"], metadata=x) for x in articles]
        return docs, titles, document_id

    def fetch_remote_stats(self) -> dict[str, Any]:
        articles, titles, document_id = self.kb_document_parser.parse_doc()
        total_docs = len(articles)
        fetched_docs = total_docs  # No filter logic for google doc
        return {
            self.DOCUMENTS_COUNT_KEY: fetched_docs,
            self.TOTAL_DOCUMENTS_KEY: total_docs,
            self.SKIPPED_DOCUMENTS_KEY: total_docs - fetched_docs,
        }

    def _normalize_doc(self, doc: Document) -> Document:
        doc.page_content = self.normalize_line_breaks(doc.page_content)
        for k, v in doc.metadata.items():
            if isinstance(v, str):
                doc.metadata[k] = self.normalize_line_breaks(v)
        return doc

    @staticmethod
    def normalize_line_breaks(text: str) -> str:
        return text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
