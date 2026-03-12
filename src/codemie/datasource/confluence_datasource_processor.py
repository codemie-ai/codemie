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

from typing import List, Optional

from atlassian.errors import ApiValueError
from codemie_tools.core.project_management.confluence.models import ConfluenceConfig
from langchain.text_splitter import MarkdownHeaderTextSplitter
from langchain_community.document_loaders.confluence import ContentFormat
from langchain_core.documents import Document
from pydantic import BaseModel
from requests.exceptions import HTTPError

from codemie.configs import logger
from codemie.core.models import KnowledgeBase
from codemie.datasource.base_datasource_processor import (
    BaseDatasourceProcessor,
    DatasourceProcessorCallback,
)
from codemie.datasource.exceptions import (
    EmptyResultException,
    InvalidQueryException,
    MissingIntegrationException,
    UnauthorizedException,
)
from codemie.datasource.datasources_config import CONFLUENCE_CONFIG
from codemie.datasource.loader.confluence_loader import ConfluenceDatasourceLoader
from codemie.rest_api.models.guardrail import GuardrailAssignmentItem
from codemie.rest_api.models.index import IndexInfo, ConfluenceIndexInfo
from codemie.rest_api.security.user import User
from codemie.service.llm_service.llm_service import llm_service


class IndexKnowledgeBaseConfluenceConfig(BaseModel):
    cql: str
    include_restricted_content: Optional[bool] = False
    include_archived_content: Optional[bool] = False
    include_attachments: Optional[bool] = False
    include_comments: Optional[bool] = False
    keep_markdown_format: Optional[bool] = True
    keep_newlines: Optional[bool] = False
    max_pages: Optional[int] = CONFLUENCE_CONFIG.loader_max_pages
    pages_per_request: Optional[int] = CONFLUENCE_CONFIG.loader_pages_per_request
    loader_timeout: Optional[int] = CONFLUENCE_CONFIG.loader_timeout

    def to_confluence_index_info(self) -> ConfluenceIndexInfo:
        return ConfluenceIndexInfo(
            cql=self.cql,
        )

    @classmethod
    def from_confluence_index_info(cls, index_info: ConfluenceIndexInfo):
        return IndexKnowledgeBaseConfluenceConfig(
            cql=index_info.cql,
        )


class ConfluenceDatasourceProcessor(BaseDatasourceProcessor):
    INDEX_TYPE = "knowledge_base_confluence"
    ATLASSIAN_NET_DOMAIN = "atlassian.net"
    WIKI_PATH = "/wiki"

    markdown_headers_to_split_on = [
        ("#", "Header 1"),
        ("##", "Header 2"),
        ("###", "Header 3"),
    ]
    add_header_to_chunks = False
    use_window_joining = True

    markdown_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=markdown_headers_to_split_on)

    def __init__(
        self,
        datasource_name: str,
        user: User,
        project_name: str,
        confluence: ConfluenceConfig,
        index_knowledge_base_config: IndexKnowledgeBaseConfluenceConfig = None,
        description: str = "",
        project_space_visible: bool = False,
        index: IndexInfo = None,
        callbacks: Optional[list[DatasourceProcessorCallback]] = None,
        request_uuid: Optional[str] = None,
        guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None,
        **kwargs,
    ):
        self.project_name = project_name
        self.description = description
        self.confluence = confluence
        self.project_space_visible = project_space_visible
        self.setting_id = kwargs.get('setting_id')
        self.index_knowledge_base_config = index_knowledge_base_config
        self.embedding_model = kwargs.get('embedding_model')
        super().__init__(
            datasource_name=datasource_name,
            user=user,
            index=index,
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
        return CONFLUENCE_CONFIG.loader_batch_size

    @classmethod
    def process_markdown(cls, markdown: str) -> list[Document]:
        docs = cls.markdown_splitter.split_text(markdown)
        return docs

    @staticmethod
    def get_header_metadata_string(doc: Document) -> str:
        return "\n".join(
            f"{'#' * i} {doc.metadata[f'Header {i}']}" for i in range(1, 11) if f"Header {i}" in doc.metadata
        ).strip()

    @classmethod
    def join_docs_window(cls, window_docs: list[Document]) -> Document:
        parts = []
        for doc in window_docs:
            parts.append(cls.get_header_metadata_string(doc))
            parts.append(doc.page_content)
            parts.append("")
        joined_content = "\n".join(parts).strip()
        base_metadata = window_docs[0].metadata.copy() if hasattr(window_docs[0], 'metadata') else {}
        filtered_metadata = {k: v for k, v in base_metadata.items() if "Header" not in k}
        return Document(page_content=joined_content, metadata=filtered_metadata)

    @classmethod
    def join_markdown_chunks_by_window(
        cls, docs: list[Document], window_size: int = 3, window_overlap: int = 1
    ) -> list[Document]:
        """
        Combines a list of Markdown Documents objects into larger, windowed chunks with overlap.

        For each window of size `window_size` (with `window_overlap` overlap between consecutive windows),
        the method concatenates the markdown content and associated header metadata of the documents in that window.
        The resulting `Document` objects contain the joined content and the base metadata (excluding header metadata)
        from the first document in each window.

        Args:
            docs (list[Document]):
                The list of Document objects to be joined.
            window_size (int, optional):
                The number of documents to include in each window. Must be > 0. Default is 3.
            window_overlap (int, optional):
                The number of documents that overlap between consecutive windows.
                Must be >= 0 and < window_size. Default is 1.

        Returns:
            list[Document]:
                A list of new Document objects, each representing a joined window of markdown chunks.

        Raises:
            ValueError: If `window_size` is not greater than 0.
            ValueError: If `window_overlap` is negative or not less than `window_size`.

        Notes:
            - Each joined Document will contain the concatenated header metadata and content of
            the documents in the window, separated by blank lines for readability.
            - The metadata of the resulting Document is copied from the first document in the window,
            with any header-related metadata keys removed.
            - If the input list `docs` is empty, an empty list is returned.

        Example:
            >>> docs = [Document(page_content="A", metadata={"Header": "H1"}),
            ...         Document(page_content="B", metadata={"Header": "H2"}),
            ...         Document(page_content="C", metadata={"Header": "H3"}),
            ...         Document(page_content="D", metadata={"Header": "H4"})]
            >>> result = ConfluenceDatasourceProcessor.join_markdown_chunks_by_window(docs,
                                                                                      window_size=2,
                                                                                      window_overlap=1)
            >>> for doc in result:
            ...     print(doc.page_content)
        """
        if window_size <= 0:
            raise ValueError("window_size must be > 0")
        if window_overlap < 0 or window_overlap >= window_size:
            raise ValueError("window_overlap must be >= 0 and < window_size")
        if not docs:
            return []

        n = len(docs)
        step = window_size - window_overlap
        new_docs = []

        # Main windows
        for start in range(0, n - window_size + 1, step):
            window = docs[start : start + window_size]
            new_docs.append(cls.join_docs_window(window))

        # Handle leftovers (if any docs at the end weren't included)
        last_window_end = (n - window_size) // step * step + window_size if n >= window_size else 0
        if not new_docs or last_window_end < n:
            window = docs[-window_size:] if n >= window_size else docs
            # Avoid duplicating last window if already included
            if not new_docs or window != docs[start : start + window_size]:
                new_docs.append(cls.join_docs_window(window))

        return new_docs

    @staticmethod
    def _assign_chunk_title_and_header(chunk: Document, source_doc: Document):
        chunk.metadata["title"] = source_doc.metadata.get("title", "")

        for i in range(10, 0, -1):
            if f"Header {i}" in chunk.metadata:
                chunk.metadata["header"] = chunk.metadata[f"Header {i}"]
                return
        chunk.metadata["header"] = ""  # No header found

    @staticmethod
    def _extract_chunk_instructions(chunk: Document):
        instruction_keywords = ["Note:", "Action:", "Important:", "Instruction:", "TODO:"]
        content = chunk.page_content
        instructions = ""
        for keyword in instruction_keywords:
            if keyword in content:
                parts = content.split(keyword, 1)
                chunk.page_content = parts[0].strip()
                instructions = f"{keyword}{parts[1].strip()}"
                break
        chunk.metadata["instructions"] = instructions

    @staticmethod
    def _assign_references(chunks: list['Document']):
        reference_counters = [0] * 10
        prev_level = 0

        for chunk in chunks:
            header_level = 0
            for i in range(10, 0, -1):
                if f"Header {i}" in chunk.metadata:
                    header_level = i
                    break
            if header_level == 0:
                chunk.metadata["reference"] = ""
                prev_level = 0
                continue

            if header_level <= prev_level:
                for i in range(header_level, 10):
                    reference_counters[i] = 0
            reference_counters[header_level - 1] += 1
            chunk.metadata["reference"] = ".".join(str(reference_counters[i]) for i in range(header_level))
            prev_level = header_level

    @classmethod
    def _propagate_metadata(cls, chunks: list[Document], source_doc: Document):
        for chunk in chunks:
            chunk.metadata.update(source_doc.metadata)
            cls._assign_chunk_title_and_header(chunk, source_doc)
            cls._extract_chunk_instructions(chunk)
        cls._assign_references(chunks)

    @classmethod
    def _parse_confluence_docs(cls, docs: list[Document]) -> list[Document]:
        """
        Parses and processes a list of Confluence Document objects into smaller, structured chunks.

        For each input document, the method:
        1. Splits the document's markdown content into smaller chunks using `process_markdown`.
        2. Propagates the original document's metadata to each resulting chunk.
        3. Optionally combines these chunks into windowed groups if `use_window_joining` is enabled.
        4. Collects all processed chunks into a single output list.

        Args:
            docs (list[Document]):
                The list of Document objects to be parsed and processed.

        Returns:
            list[Document]:
                A list of processed Document chunks, each with propagated metadata. If window joining is enabled,
                the chunks are further combined into larger windowed Document objects.

        Notes:
            - Each chunk produced from `process_markdown` receives a copy of the original document's metadata.
            - If `cls.use_window_joining` is True, the chunks are further grouped
            using `join_markdown_chunks_by_window`.
            - The returned list may be longer or shorter than the input list, depending on
            the chunking and joining logic.
        """
        parsed_docs: list[Document] = []
        for doc in docs:
            chunks = cls.process_markdown(doc.page_content)
            if not chunks:
                chunks = [doc.model_copy()]
            cls._propagate_metadata(chunks, source_doc=doc)
            if cls.use_window_joining:
                chunks = cls.join_markdown_chunks_by_window(chunks)
            parsed_docs.extend(chunks)
        return parsed_docs

    def _split_documents(self, docs: list[Document]) -> dict[str, list[Document]]:
        transformed_documents = self._parse_confluence_docs(docs)
        return super()._split_documents(list(transformed_documents))

    def _process_chunk(self, chunk: str, chunk_metadata, document: Document) -> Document:
        # Basic fields
        title = document.metadata.get("title", "")
        source_data = document.metadata.get("source", "")
        content_lines = [f"Page title: {title}.", f"Source: {source_data}."]

        # Add headers if present, up to 10
        if self.add_header_to_chunks:
            content_lines.append(self.get_header_metadata_string(document))

        # Add the chunk itself
        content_lines.append(f"\n\n{chunk}")

        # Join everything into a single string
        content = "\n".join(content_lines)
        return Document(page_content=content, metadata=chunk_metadata)

    def _init_index(self):
        if self.index:
            self.index_knowledge_base_config = IndexKnowledgeBaseConfluenceConfig.from_confluence_index_info(
                self.index.confluence
            )
        else:
            self.index = IndexInfo.new(
                repo_name=self.datasource_name,
                full_name=self.datasource_name,
                project_name=self.project_name,
                description=self.description,
                project_space_visible=self.project_space_visible,
                index_type=self.INDEX_TYPE,
                user=self.user,
                confluence=self.index_knowledge_base_config.to_confluence_index_info(),
                embeddings_model=self.embedding_model or llm_service.default_embedding_model,
                setting_id=self.setting_id,
            )

        self._assign_and_sync_guardrails()

    def _init_loader(self):
        """
        Initializes and configures a ConfluenceLoader instance based on the current configuration.

        This method enhances the CQL (Confluence Query Language) query to ensure it includes an 'ORDER BY' clause,
        specifying the results should be ordered by 'type' in descending order, if it is not already included.

        The ConfluenceLoader is then instantiated with necessary parameters,
        such as URL, token, and the enhanced CQL query.
        Additional configuration for the loader includes setting the maximum number of pages to retrieve,
        the number of pages per request, and options to include archived content, comments, attachments,
        restricted content, maintain markdown
        format, and keep newlines.

        Returns:
            ConfluenceLoader: An instance of ConfluenceLoader configured based on the current settings.
        """
        # Initialize the ConfluenceLoader with the necessary parameters.
        return self._initialize_confluence_loader(self.confluence, self.index_knowledge_base_config)

    @classmethod
    def _initialize_confluence_loader(
        cls, confluence: ConfluenceConfig, index_config: IndexKnowledgeBaseConfluenceConfig
    ):
        if not confluence.url or not confluence.token:
            logger.error("Missing Url or Token for Confluence integration")
            raise MissingIntegrationException("Confluence")

        url = str(confluence.url)
        if cls.ATLASSIAN_NET_DOMAIN in url and cls.WIKI_PATH not in url:
            url = cls._url_joiner(url, cls.WIKI_PATH)

        cql_query = cls._enhance_cql_query(index_config.cql)
        cloud = confluence.cloud
        logger.info(f"Init confluence loader. Url: {url}, CQL:{cql_query}, Cloud: {cloud}")
        if cloud:
            loader = ConfluenceDatasourceLoader(
                url=url,
                username=confluence.username,
                api_key=confluence.token,
                cql=cql_query,
                content_format=ContentFormat.VIEW,
                cloud=cloud,
                keep_markdown_format=True,
            )
        else:
            loader = ConfluenceDatasourceLoader(
                url=url,
                token=confluence.token,
                cql=cql_query,
                content_format=ContentFormat.VIEW,
                cloud=cloud,
                keep_markdown_format=True,
            )

        # Configure the loader based on the index knowledge base configuration.
        loader.max_pages = index_config.max_pages
        loader.limit = index_config.pages_per_request
        loader.include_archived_content = index_config.include_archived_content
        loader.include_comments = index_config.include_comments
        loader.include_attachments = index_config.include_attachments
        loader.include_restricted_content = index_config.include_restricted_content
        loader.keep_newlines = index_config.keep_newlines
        loader.confluence.timeout = index_config.loader_timeout

        # Return the configured loader.
        return loader

    @classmethod
    def _url_joiner(cls, base_url: str, path: str) -> str:
        base_url = base_url.rstrip("/")
        path = path.lstrip("/")
        return f"{base_url}/{path}"

    @classmethod
    def _enhance_cql_query(cls, cql) -> str:
        """
        Enhance the CQL query to ensure it includes an 'ORDER BY' clause and the 'type=page' condition.

        Returns:
            str: The enhanced CQL query.
        """
        cql_query = cql
        # Ensure 'type=page' is included in the query
        if 'type' not in cql_query.lower():
            cql_query = f"type=page AND ({cql_query})"

        # Ensure 'ORDER BY' clause is included in the query
        enhanced_cql = f"({cql_query}) ORDER BY type DESC" if "ORDER BY" not in cql_query.upper() else cql_query

        return enhanced_cql

    @classmethod
    def validate_creds_and_loader(
        cls, confluence: ConfluenceConfig, index_config: IndexKnowledgeBaseConfluenceConfig
    ) -> dict[str, int]:
        loader = cls._initialize_confluence_loader(confluence, index_config)
        try:
            stats = loader.fetch_remote_stats()
            return stats
        except ApiValueError as e:
            logger.error(f"Cannot parse CQL: {index_config.cql}. Failed with error {e}")
            raise InvalidQueryException("CQL", f"Reason: {e.reason}")
        except HTTPError as e:
            logger.error(
                f"Cannot authenticate user. Failed with error {e.response.status_code}: {e.response.reason}",
                exc_info=True,
            )
            raise UnauthorizedException(datasource_type="Confluence")

    @classmethod
    def check_confluence_query(cls, cql: str, confluence: ConfluenceConfig):
        if not cql or not cql.strip():
            logger.warning("CQL was not provided")
            raise InvalidQueryException("CQL", "There is no CQL expression")
        index_stats = ConfluenceDatasourceProcessor.validate_creds_and_loader(
            confluence=confluence, index_config=IndexKnowledgeBaseConfluenceConfig(cql=cql)
        )

        docs_count = index_stats.get(ConfluenceDatasourceLoader.DOCUMENTS_COUNT_KEY, 0)

        if not docs_count:
            logger.error(f"Empty result returned for this query: {cql}.")
            raise EmptyResultException("CQL")

        return docs_count
