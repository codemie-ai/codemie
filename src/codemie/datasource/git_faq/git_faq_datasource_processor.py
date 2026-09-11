# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the "License”);
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

from dataclasses import dataclass
from typing import List, Optional

from langchain_text_splitters import MarkdownHeaderTextSplitter
from langchain_core.documents import Document

from codemie.core.models import KnowledgeBase
from codemie.datasource.base_datasource_processor import BaseDatasourceProcessor
from codemie.datasource.loader.git_faq_loader import GitFaqLoader
from codemie.rest_api.models.guardrail import GuardrailAssignmentItem
from codemie.rest_api.models.index import IndexInfo
from codemie.rest_api.security.user import User
from codemie.service.constants import FullDatasourceTypes
from codemie.service.llm_service.llm_service import llm_service
from codemie.service.settings.settings import SettingsService


@dataclass
class GitFaqConfig:
    """Git repository configuration for the FAQ datasource."""

    repo_link: str
    branch: str
    files_filter: Optional[str] = None
    setting_id: Optional[str] = None


class GitFaqDatasourceProcessor(BaseDatasourceProcessor):
    """Git-based FAQ datasource: clones a repo, parses every ``*.md``
    file into an article, and indexes header-based chunks with embeddings —
    the same markdown-section pipeline the Confluence datasource uses.

    Retrieval is the standard hybrid KB flow (kNN + BM25 + RRF). Config lives
    on ``IndexInfo`` (link/branch/setting_id/files_filter) — there is
    no ``GitRepo`` row for this datasource type.
    """

    INDEX_TYPE = FullDatasourceTypes.GIT_FAQ.value

    # Markdown section splitting, mirroring ConfluenceDatasourceProcessor:
    # split on H1-H3, then window-join sections so each chunk carries its
    # headers inline; the base class size-splits the joined windows after.
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
        *,
        datasource_name: str,
        project_name: str,
        git_config: "GitFaqConfig",
        description: str = "",
        project_space_visible: bool = False,
        user: Optional[User] = None,
        index_info: Optional[IndexInfo] = None,
        callbacks: Optional[list] = None,
        request_uuid: Optional[str] = None,
        embedding_model: Optional[str] = None,
        guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None,
        cron_expression: Optional[str] = None,
    ):
        self.project_name = project_name
        self.description = description
        self.repo_link = git_config.repo_link
        self.branch = git_config.branch
        self.files_filter = (git_config.files_filter or "").strip()
        self.project_space_visible = project_space_visible
        self.embedding_model = embedding_model
        self.setting_id = git_config.setting_id

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
        creds = None
        if self.setting_id:
            creds = SettingsService.get_git_creds(
                user_id=self.user.id,
                project_name=self.project_name,
                repo_link=self.repo_link,
                setting_id=self.setting_id,
            )
        # Empty creds (or no setting_id) => public repo, anonymous clone.
        return GitFaqLoader.create_loader(
            project_name=self.project_name,
            datasource_name=self.datasource_name,
            repo_link=self.repo_link,
            branch=self.branch,
            files_filter=self.files_filter,
            creds=creds,
            request_uuid=self.request_uuid,
        )

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
                link=self.repo_link,
                branch=self.branch,
                setting_id=self.setting_id,
                files_filter=self.files_filter,
                embeddings_model=self.embedding_model or llm_service.default_embedding_model,
            )

        self._assign_and_sync_guardrails()

    def _on_process_start(self):
        if self.is_resume_indexing:
            raise NotImplementedError(f"Resume indexing is not supported for {self.INDEX_TYPE}")
        if self.is_incremental_reindex:
            raise NotImplementedError(f"Incremental reindex is not supported for {self.INDEX_TYPE}")

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

    @classmethod
    def _parse_faq_docs(cls, docs: list[Document]) -> list[Document]:
        """
        Parses FAQ articles into header-based section chunks (Confluence-style).

        For each article:
        1. Splits the markdown content into sections on H1-H3 headers.
        2. Propagates the article's metadata (title/instructions/reference/checksum)
           to each section and records the deepest header on it.
        3. Window-joins consecutive sections so each chunk carries its headers inline.

        Unlike Confluence, instructions are extracted at article level by the loader's
        parser (frontmatter / ``Prompt Instruction:`` marker), so no per-chunk
        instruction extraction happens here; the article ``reference`` is kept
        (path-derived) rather than replaced with section numbering.
        """
        parsed_docs: list[Document] = []
        for doc in docs:
            chunks = cls.markdown_splitter.split_text(doc.page_content)
            if not chunks:
                chunks = [doc.model_copy()]
            for chunk in chunks:
                chunk.metadata.update(doc.metadata)
                cls._assign_chunk_title_and_header(chunk, doc)
            if cls.use_window_joining:
                chunks = cls.join_markdown_chunks_by_window(chunks)
            parsed_docs.extend(chunks)
        return parsed_docs

    def _split_documents(self, docs: list[Document]) -> dict[str, list[Document]]:
        transformed_documents = self._parse_faq_docs(docs)
        return super()._split_documents(list(transformed_documents))

    def _process_chunk(self, chunk: str, chunk_metadata, document: Document) -> Document:
        title = document.metadata.get("title", "")
        source_data = document.metadata.get("source", "")
        content_lines = [f"Article title: {title}.", f"Source: {source_data}."]

        # Add headers if present, up to 10
        if self.add_header_to_chunks:
            content_lines.append(self.get_header_metadata_string(document))

        content_lines.append(f"\n\n{chunk}")
        content = "\n".join(content_lines)
        return Document(page_content=content, metadata=chunk_metadata)
