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

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.documents import Document

from codemie.datasource.git_faq.git_faq_datasource_processor import GitFaqConfig, GitFaqDatasourceProcessor
from codemie.rest_api.security.user import User

PROJECT_NAME = "test-project"
DATASOURCE_NAME = "test-datasource"
REPO_LINK = "https://git.example.com/team/repo.git"
BRANCH = "main"

ARTICLE_METADATA = {
    "title": "How do I install the local stack?",
    "instructions": "Always mention the Makefile target.",
    "reference": "getting-started/install-local-stack",
    "checksum": "abc123",
    "source": "faq/install-local-stack.md",
    "file_path": "faq/install-local-stack.md",
    "file_name": "install-local-stack.md",
    "file_type": ".md",
}

ARTICLE_CONTENT = """# Install the local stack

Clone the repository first.

## Requirements

Docker and 8 GB of RAM.

## Steps

Run `make up`."""


def make_processor(**overrides) -> GitFaqDatasourceProcessor:
    git_config = GitFaqConfig(
        repo_link=overrides.pop("repo_link", REPO_LINK),
        branch=overrides.pop("branch", BRANCH),
        files_filter=overrides.pop("files_filter", None),
        setting_id=overrides.pop("setting_id", None),
    )
    params = {
        "datasource_name": DATASOURCE_NAME,
        "project_name": PROJECT_NAME,
        "git_config": git_config,
        "description": "test faq datasource",
        "project_space_visible": True,
        "user": User(id="1", username="tester", name="Tester"),
    }
    params.update(overrides)
    return GitFaqDatasourceProcessor(**params)


class TestGitFaqDatasourceProcessor:
    def test_index_type_is_chunked_kb(self):
        assert GitFaqDatasourceProcessor.INDEX_TYPE == "knowledge_base_git_faq"

    def test_index_name_is_kb_scoped(self):
        processor = make_processor()

        assert processor._index_name == f"{PROJECT_NAME}-{DATASOURCE_NAME}"

    def test_resume_and_incremental_rejected(self):
        processor = make_processor()

        processor.is_resume_indexing = True
        with pytest.raises(NotImplementedError, match="knowledge_base_git_faq"):
            processor._on_process_start()

        processor.is_resume_indexing = False
        processor.is_incremental_reindex = True
        with pytest.raises(NotImplementedError, match="knowledge_base_git_faq"):
            processor._on_process_start()

        processor.is_incremental_reindex = False
        processor._on_process_start()

    def test_init_index_creates_index_info_with_faq_fields(self):
        processor = make_processor()

        with patch("codemie.datasource.git_faq.git_faq_datasource_processor.IndexInfo") as mock_index_info:
            mock_index = MagicMock()
            mock_index_info.new.return_value = mock_index
            processor._init_index()

        _, kwargs = mock_index_info.new.call_args
        assert kwargs["index_type"] == "knowledge_base_git_faq"
        assert kwargs["link"] == REPO_LINK
        assert kwargs["branch"] == BRANCH
        assert kwargs["setting_id"] is None
        assert kwargs["embeddings_model"] is not None
        assert processor.index is mock_index

    def test_init_index_reuses_existing(self):
        processor = make_processor()
        existing = MagicMock()
        processor.index = existing

        with patch("codemie.datasource.git_faq.git_faq_datasource_processor.IndexInfo") as mock_index_info:
            processor._init_index()

        mock_index_info.new.assert_not_called()
        assert processor.index is existing

    def test_init_loader_resolves_creds_with_setting_id(self):
        processor = make_processor(setting_id="git-integration-id")

        with (
            patch("codemie.datasource.git_faq.git_faq_datasource_processor.SettingsService") as mock_settings,
            patch("codemie.datasource.git_faq.git_faq_datasource_processor.GitFaqLoader") as mock_loader_cls,
        ):
            creds = MagicMock()
            mock_settings.get_git_creds.return_value = creds
            processor._init_loader()

        mock_settings.get_git_creds.assert_called_once_with(
            user_id="1", project_name=PROJECT_NAME, repo_link=REPO_LINK, setting_id="git-integration-id"
        )
        mock_loader_cls.create_loader.assert_called_once_with(
            project_name=PROJECT_NAME,
            datasource_name=DATASOURCE_NAME,
            repo_link=REPO_LINK,
            branch=BRANCH,
            files_filter="",
            creds=creds,
            request_uuid=None,
        )

    def test_init_loader_public_repo_without_setting_id(self):
        processor = make_processor()

        with patch("codemie.datasource.git_faq.git_faq_datasource_processor.GitFaqLoader") as mock_loader_cls:
            processor._init_loader()

        _, kwargs = mock_loader_cls.create_loader.call_args
        assert kwargs["creds"] is None


class TestParseFaqDocs:
    """Header-based section splitting with article metadata propagation (Confluence-style)."""

    def _article(self, content: str = ARTICLE_CONTENT, metadata: dict | None = None) -> Document:
        return Document(page_content=content, metadata={**(metadata or ARTICLE_METADATA)})

    def test_sections_split_with_headers_baked_into_content(self):
        chunks = GitFaqDatasourceProcessor._parse_faq_docs([self._article()])

        assert chunks, "expected windowed sections"
        joined = "\n\n".join(c.page_content for c in chunks)
        # Headers from every level end up inline in the windowed chunks.
        assert "# Install the local stack" in joined
        assert "## Requirements" in joined
        assert "## Steps" in joined

    def test_article_metadata_propagated_to_chunks(self):
        chunks = GitFaqDatasourceProcessor._parse_faq_docs([self._article()])

        for chunk in chunks:
            assert chunk.metadata["title"] == ARTICLE_METADATA["title"]
            assert chunk.metadata["instructions"] == ARTICLE_METADATA["instructions"]
            assert chunk.metadata["reference"] == ARTICLE_METADATA["reference"]
            assert chunk.metadata["checksum"] == ARTICLE_METADATA["checksum"]
            assert chunk.metadata["source"] == ARTICLE_METADATA["source"]
            # Header keys are consumed by window joining, not left on metadata.
            assert not any(k.startswith("Header ") for k in chunk.metadata)

    def test_deepest_header_recorded_on_section_before_join(self):
        # Window joining consumes Header N keys, so verify the header assignment
        # step in isolation: split -> propagate -> assign, without joining.
        processor_cls = GitFaqDatasourceProcessor
        doc = self._article()
        chunks = processor_cls.markdown_splitter.split_text(doc.page_content)
        for chunk in chunks:
            chunk.metadata.update(doc.metadata)
            processor_cls._assign_chunk_title_and_header(chunk, doc)

        headers = {c.metadata["header"] for c in chunks}
        assert "Requirements" in headers
        assert "Steps" in headers
        assert "Install the local stack" in headers

    def test_article_without_headings_becomes_single_chunk(self):
        doc = self._article(content="Plain answer without any headings.")
        chunks = GitFaqDatasourceProcessor._parse_faq_docs([doc])

        assert len(chunks) == 1
        assert "Plain answer without any headings." in chunks[0].page_content
        assert chunks[0].metadata["header"] == ""

    def test_window_joining_overlaps_consecutive_sections(self):
        # 5 sections with window 3/overlap 1 produce windows [0-2] and [2-4]:
        # only section "two" is shared between the two chunks.
        content = "\n\n".join(["# T", "intro", "## S1", "one", "## S2", "two", "## S3", "three", "## S4", "four"])
        chunks = GitFaqDatasourceProcessor._parse_faq_docs([self._article(content=content)])

        assert len(chunks) == 2
        assert "intro" in chunks[0].page_content and "intro" not in chunks[1].page_content
        assert "one" in chunks[0].page_content and "one" not in chunks[1].page_content
        assert "two" in chunks[0].page_content and "two" in chunks[1].page_content
        assert "three" not in chunks[0].page_content and "three" in chunks[1].page_content
        assert "four" not in chunks[0].page_content and "four" in chunks[1].page_content

    def test_multiple_articles_processed_independently(self):
        second = self._article(
            content="# Other\n\nOther body.",
            metadata={**ARTICLE_METADATA, "title": "Other?", "reference": "other", "source": "faq/other.md"},
        )
        chunks = GitFaqDatasourceProcessor._parse_faq_docs([self._article(), second])

        titles = {c.metadata["title"] for c in chunks}
        assert ARTICLE_METADATA["title"] in titles
        assert "Other?" in titles


class TestSplitAndChunk:
    def test_split_documents_assigns_chunk_numbers_after_transform(self):
        processor = make_processor()
        docs = [
            Document(
                page_content=ARTICLE_CONTENT,
                metadata=dict(ARTICLE_METADATA),
            )
        ]

        with patch.object(processor, "_get_splitter") as mock_splitter:
            mock_splitter.return_value.split_text.side_effect = lambda text: [text]

            result = processor._split_documents(docs)

        chunks = result[ARTICLE_METADATA["source"]]
        assert len(chunks) >= 1
        chunk_nums = [c.metadata["chunk_num"] for c in chunks]
        assert chunk_nums == sorted(chunk_nums), "chunk_num must be monotonically assigned"
        assert len(set(chunk_nums)) == len(chunk_nums), "chunk_num must be unique per source"

    def test_process_chunk_prefixes_article_title_and_source(self):
        processor = make_processor()
        document = Document(page_content=ARTICLE_CONTENT, metadata=dict(ARTICLE_METADATA))

        processed = processor._process_chunk("chunk body", dict(ARTICLE_METADATA), document)

        assert "Article title: How do I install the local stack?" in processed.page_content
        assert "Source: faq/install-local-stack.md." in processed.page_content
        assert "chunk body" in processed.page_content
        assert processed.metadata == ARTICLE_METADATA
