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

from typing import Callable, List, Optional

from codemie_tools.base.constants import SOURCE_DOCUMENT_KEY, SOURCE_FIELD_KEY, FILE_CONTENT_FIELD_KEY
from langchain_core.documents import Document

from codemie.agents.tools.code.tools_models import FilteredDocuments
from codemie.configs import logger, config
from codemie.core.dependecies import get_llm_by_credentials
from codemie.core.models import CodeFields
from codemie.templates.coding_prompts import CODE_FILTER_RELEVANCE_PROMPT


class BaseCodeToolMixin:
    code_fields: CodeFields
    throw_truncated_error: bool = False
    with_filtering: Optional[bool] = False


class CodeRepoBaseToolMixin(BaseCodeToolMixin):
    is_react: bool = True

    @staticmethod
    def _create_batches(
        items: list[str] | list[Document], max_tokens: int, calculate_tokens_count: Callable
    ) -> list[list[str]] | list[list[Document]]:
        batches = []
        current_batch = []
        current_batch_tokens = 0

        for item in items:
            tokens_base = item.page_content if isinstance(item, Document) else item
            item_tokens = calculate_tokens_count(tokens_base)

            if current_batch_tokens + item_tokens > max_tokens:
                batches.append(current_batch)
                current_batch = []
                current_batch_tokens = 0
            current_batch.append(item)
            current_batch_tokens += item_tokens

        if current_batch:  # Add the last batch if it's not empty
            batches.append(current_batch)
        return batches


class SearchCodeRepoBaseToolMixin(CodeRepoBaseToolMixin):
    top_k: int
    tokens_size_limit: int = config.MAX_CODE_TOOLS_OUTPUT_SIZE
    user_input: Optional[str] = None
    max_tokens_per_batch: int = 70000

    def _filter_documents_by_relevance(
        self,
        query: str,
        documents: List[Document],
        calculate_tokens_count: Callable,
        request_id: str | None,
        llm_model: str,
        keywords_list: Optional[List[str]] = None,
        limit_docs_count: Optional[int] = None,
    ) -> List[Document]:
        """
        Filters documents by relevance for a given query, ensuring each batch of
        documents does not exceed 70,000 tokens.

        Args:
            query (str): The query to filter documents by.
            documents (List[Document]): A list of documents to be filtered.

        Returns:
            List[Document]: A list of documents filtered by relevance.
        """
        try:
            logger.debug(
                f"Filtering documents by relevance for query: {query}. "
                f"Input: {self.user_input}. InitialDocs: {len(documents)}"
            )
            if limit_docs_count:
                logger.debug(f"Filtering documents limited to count: {limit_docs_count}")
            llm = get_llm_by_credentials(llm_model=llm_model, request_id=request_id)
            filter_chain = CODE_FILTER_RELEVANCE_PROMPT | llm.with_structured_output(FilteredDocuments)
            logger.debug(
                f"Initial documents: "
                f"{'\n'.join([f"{doc.metadata['source']}_{doc.metadata.get('chunk_num', '')}" for doc in documents])}"
            )

            batches: List[List[Document]] = self._create_batches(
                documents, max_tokens=self.max_tokens_per_batch, calculate_tokens_count=calculate_tokens_count
            )
            final_filtered_documents = []
            for batch in batches:
                logger.debug(
                    f"Initial count: {len(batch)}, "
                    f"documents: {
                        '\n'.join([f"{doc.metadata['source']}_{doc.metadata.get('chunk_num', '')}" for doc in batch])
                    }"
                )
                filtered_documents = self._filter_batch_by_relevance(
                    batch, query, filter_chain, keywords_list, limit_docs_count
                )
                logger.debug(
                    f"Filtered count: {len(filtered_documents)}, "
                    f"documents: {
                        '\n'.join([f"{doc.metadata['source']}_{doc.metadata.get('chunk_num', '')}" for doc in batch])
                    }"
                )
                final_filtered_documents.extend(filtered_documents)

            logger.debug(f"Filtered sources size: {len(final_filtered_documents)}, docs: {final_filtered_documents}")

            return final_filtered_documents
        except Exception as e:
            logger.error(f"Error filtering documents by relevance: {str(e)}")
            return documents

    def _filter_and_format_documents(self, documents: List[Document], calculate_tokens_count: Callable) -> str:
        """
        Filter documents to fit within a total token size limit, then format the included documents.

        :param documents: List of Document objects
        :return: Formatted string of documents that fit within the token size limit
        """
        cumulative_tokens_count = 0
        tokens_limit = self.tokens_size_limit * 0.9  # 90% of the limit
        included_documents = []
        excluded_document_ids = []
        logger.debug(f"Filter documents to pass limit tokens count: {self.tokens_size_limit}")

        for doc in documents:
            doc_tokens_count = calculate_tokens_count(doc)
            chunk_num = doc.metadata.get("chunk_num", "")
            identifier = f"-{chunk_num}" if chunk_num else ""
            doc_id = doc.metadata['source'] + identifier
            if cumulative_tokens_count + doc_tokens_count <= tokens_limit:
                included_documents.append({"doc_id": doc_id, "page_content": doc.page_content})
                cumulative_tokens_count += doc_tokens_count
            else:
                logger.debug(
                    f"Excluding: {doc_id}, because current cumulative_tokens_count={cumulative_tokens_count} and file:"
                    f"{doc_tokens_count}."
                )

                excluded_document_ids.append(doc_id)

        excluded_references = "\n".join(excluded_document_ids)
        # Formatting documents
        final_response = "\n".join(
            [
                f"\n{SOURCE_DOCUMENT_KEY}\n"
                f"{SOURCE_FIELD_KEY} {doc["doc_id"]}\n"
                f"{FILE_CONTENT_FIELD_KEY} \n{doc["page_content"]}\n"
                for doc in included_documents
            ]
        )

        if excluded_references:
            final_response += (
                f"\n###{SOURCE_DOCUMENT_KEY}###\n"
                f"Excluded Documents due to LLM tokens limitation (reached {cumulative_tokens_count} for limit"
                f" {tokens_limit}, you MUST search them additionally using file_path with chunk number:\n "
                f"{excluded_references}"
            )
        return final_response

    def _filter_batch_by_relevance(
        self,
        batch: List[Document],
        query: str,
        filter_chain,
        keywords_list: list[str] | None,
        limit_docs_count: int | None,
    ) -> List[Document]:
        """
        Filters a batch of documents by relevance using the filter chain.

        Args:
            batch (List[Document]): A batch of documents to be filtered.
            query (str): The query to filter documents by.
            filter_chain: The filter chain to be used for filtering.

        Returns:
            List[Document]: A list of documents filtered by relevance.
        """
        documents_str = "\n".join(
            [
                f"Source_part: {doc.metadata['source']}_{doc.metadata.get('chunk_num', '')}\n"
                f"Content: {doc.page_content} \n === \n"
                for doc in batch
            ]
        )

        filtered_sources = filter_chain.invoke(
            {
                "documents": str(documents_str),
                "input": self.user_input,
                "query": query,
                "keywords_list": keywords_list or [],
                "limit_docs_count": limit_docs_count or "not limit to specific count",
            }
        )

        filtered_documents = [
            doc
            for doc in batch
            if f"{doc.metadata['source']}_{doc.metadata.get('chunk_num', '')}" in filtered_sources.sources
        ]

        return filtered_documents
