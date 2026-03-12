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

import unittest
from unittest.mock import MagicMock, patch

from codemie_tools.core.project_management.confluence.models import ConfluenceConfig
from langchain_community.document_loaders.confluence import ContentFormat
from langchain_core.documents import Document

from codemie.core.models import KnowledgeBase
from codemie.datasource.exceptions import MissingIntegrationException, InvalidQueryException
from codemie.datasource.confluence_datasource_processor import (
    IndexKnowledgeBaseConfluenceConfig,
    ConfluenceDatasourceProcessor,
)
from codemie.rest_api.models.index import ConfluenceIndexInfo, IndexInfo
from codemie.rest_api.security.user import User


class TestIndexKnowledgeBaseConfluenceConfig(unittest.TestCase):
    def setUp(self):
        self.config = IndexKnowledgeBaseConfluenceConfig(cql='cql')

    def test_to_confluence_index_info(self):
        result = self.config.to_confluence_index_info()

        self.assertIsInstance(
            result, ConfluenceIndexInfo, "DatasourceProcessingResult should be an instance of ConfluenceIndexInfo"
        )
        self.assertEqual(result.cql, 'cql', "Expected cql to be 'cql'")
        self.assertEqual(result.pages_per_request, 20, "Expected pages_per_request to be 20")
        self.assertEqual(result.max_pages, 1000, "Expected max_pages to be 1000")
        self.assertFalse(result.include_restricted_content, "Expected include_restricted_content to be False")
        self.assertFalse(result.include_archived_content, "Expected include_archived_content to be False")
        self.assertFalse(result.include_attachments, "Expected include_attachments to be False")
        self.assertFalse(result.include_comments, "Expected include_comments to be False")

    def test_from_confluence_index_info(self):
        index_info = MagicMock()
        index_info.cql = 'cql'

        result = IndexKnowledgeBaseConfluenceConfig.from_confluence_index_info(index_info)

        self.assertEqual(result.cql, 'cql', "Expected cql to be 'cql'")
        self.assertEqual(result.pages_per_request, 20, "Expected pages_per_request to be 20")
        self.assertEqual(result.max_pages, 1000, "Expected max_pages to be 1000")
        self.assertFalse(result.include_restricted_content, "Expected include_restricted_content to be False")
        self.assertFalse(result.include_archived_content, "Expected include_archived_content to be False")
        self.assertFalse(result.include_attachments, "Expected include_attachments to be False")
        self.assertFalse(result.include_comments, "Expected include_comments to be False")


class TestConfluenceDatasourceProcessor(unittest.TestCase):
    def setUp(self):
        self.datasource_name = "test_datasource"
        self.project_name = "test_project"
        self.confluence = ConfluenceConfig(url="https://example.com", token="token", cloud=False)
        self.index_knowledge_base_config = IndexKnowledgeBaseConfluenceConfig(cql="cql")
        self.mock_user = User(id="test_user", username="testuser", name='testname')
        self.processor = ConfluenceDatasourceProcessor(
            datasource_name=self.datasource_name,
            project_name=self.project_name,
            user=self.mock_user,
            confluence=self.confluence,
            index_knowledge_base_config=self.index_knowledge_base_config,
            index=IndexInfo(
                repo_name=self.datasource_name,
                full_name=self.datasource_name,
                project_name=self.project_name,
                project_space_visible=True,
                index_type="type",
                confluence=self.index_knowledge_base_config.to_confluence_index_info(),
                embeddings_model='Model',
                setting_id='ID',
            ),
        )
        self.doc1 = Document(page_content="A1\nA2", metadata={"id": 1, "Header 1": "foo"})
        self.doc2 = Document(page_content="B1\nB2", metadata={"id": 2, "Header 1": "bar"})

    def test_initialization(self):
        self.assertEqual(self.processor.project_name, self.project_name)
        self.assertEqual(self.processor.description, "")
        self.assertEqual(self.processor.confluence, self.confluence)
        self.assertEqual(self.processor.index_knowledge_base_config, self.index_knowledge_base_config)

    def test_index_name_property(self):
        expected_index_name = KnowledgeBase(
            name=f"{self.project_name}-{self.datasource_name}", type=self.processor.INDEX_TYPE
        ).get_identifier()
        self.assertEqual(self.processor._index_name, expected_index_name)

    def test_on_process_start(self):
        # Make sure that cloud confluence won't throw any issue
        self.processor.confluence.cloud = True
        self.processor._on_process_start()

        self.processor.confluence.cloud = False
        with self.assertRaises(
            MissingIntegrationException, msg="Confluence integration are not configured. Invalid Url or Token"
        ):
            self.processor.confluence.url = None
            self.processor._init_loader()

        self.processor.confluence.url = "https://example.com"
        self.processor.confluence.token = None
        with self.assertRaises(
            MissingIntegrationException, msg="Confluence integration are not configured. Invalid Url or Token"
        ):
            self.processor._init_loader()

        self.processor.confluence.token = "token"
        self.processor.callbacks = []
        self.processor._on_process_start()

    def test_get_metadata_header_string(self):
        doc = Document(page_content="Text", metadata={"Header 1": "First", "Header 2": "Second"})
        result = self.processor.get_header_metadata_string(doc)
        expected_result = "# First\n## Second"
        self.assertEqual(result, expected_result)

    def test_join_markdown_by_chunks(self):
        chunks = []
        for i in range(5):
            chunks.append(Document(page_content=f"Document {i}", metadata={"Header 1": "Header"}))

        result = self.processor.join_markdown_chunks_by_window(chunks, window_size=3, window_overlap=1)

        # Assert
        # There should be 3 windows: [0,1,2], [2,3,4]
        self.assertEqual(len(result), 2)

        # Check the content of the first window
        expected_0 = "# Header\nDocument 0\n\n# Header\nDocument 1\n\n# Header\nDocument 2"
        self.assertEqual(result[0].page_content, expected_0)
        # Metadata should have header removed
        self.assertEqual(result[0].metadata, {})

        # Check the content of the second window
        expected_1 = "# Header\nDocument 2\n\n# Header\nDocument 3\n\n# Header\nDocument 4"
        self.assertEqual(result[1].page_content, expected_1)
        self.assertEqual(result[1].metadata, {})

    def test_empty_markdown_chunks(self):
        result = self.processor.join_markdown_chunks_by_window([], window_size=2, window_overlap=1)
        self.assertEqual(result, [])

    def test_markdown_joining_invalid_params(self):
        with self.assertRaises(ValueError):
            self.processor.join_markdown_chunks_by_window([Document("A", metadata={})], window_size=0)
        with self.assertRaises(ValueError):
            self.processor.join_markdown_chunks_by_window([Document("A", metadata={})], window_size=2, window_overlap=2)
        with self.assertRaises(ValueError):
            self.processor.join_markdown_chunks_by_window(
                [Document("A", metadata={})], window_size=2, window_overlap=-1
            )

    def test_parse_without_window_joining(self):
        # Simulate process_markdown splitting into two chunks per doc
        ConfluenceDatasourceProcessor.use_window_joining = False
        docs = [
            Document(
                "# Content \nThis is some text under header 1.\n\n## More content\nMore text.", metadata={"docid": 123}
            ),
        ]
        result = ConfluenceDatasourceProcessor._parse_confluence_docs(docs)
        # We expect two chunks, both with propagated docid and their respective header in metadata,
        # and the header should not be in their page_content.
        expected = [
            Document(
                "This is some text under header 1.",
                metadata={
                    "Header 1": "Content",
                    "docid": 123,
                    "title": "",
                    "header": "Content",
                    "instructions": "",
                    "reference": "1",
                },
            ),
            Document(
                "More text.",
                metadata={
                    "Header 1": "Content",
                    "Header 2": "More content",
                    "docid": 123,
                    "title": "",
                    "header": "More content",
                    "instructions": "",
                    "reference": "1.1",
                },
            ),
        ]
        self.assertEqual(result, expected)
        # Also, headers should not be in page_content
        for doc in result:
            self.assertNotIn("Header 1", doc.page_content)
            self.assertNotIn("Header 2", doc.page_content)

    def test_empty_docs(self):
        ConfluenceDatasourceProcessor.use_window_joining = False
        result = ConfluenceDatasourceProcessor._parse_confluence_docs([])
        self.assertEqual(result, [])

    def test_parse_with_window_joining(self):
        ConfluenceDatasourceProcessor.use_window_joining = True
        docs = [
            Document(
                "# Content1 \nText 1.\n\n# Content2 \nText 2.\n\n# Content3 \nText 3.\n\n# Content4 \nText 4.\n\n",
                metadata={"docid": 123},
            ),
        ]
        result = ConfluenceDatasourceProcessor._parse_confluence_docs(docs)

        self.assertEqual(2, len(result))
        self.assertEqual(result[0].page_content, "# Content1\nText 1.\n\n# Content2\nText 2.\n\n# Content3\nText 3.")
        self.assertEqual(result[1].page_content, "# Content2\nText 2.\n\n# Content3\nText 3.\n\n# Content4\nText 4.")
        self.assertEqual(
            result[0].metadata,
            {"docid": 123, "title": "", "header": "Content1", "instructions": "", "reference": "1"},
        )

    def test_split_documents(self):
        docs = [
            Document(
                "# Content1 \nText 1.\n\n# Content2 \nText 2.\n\n# Content3 \nText 3.\n\n# Content4 \nText 4.\n\n",
                metadata={"source": "source_url", "title": "Title1"},
            ),
            Document("# Content1 \nText 1", metadata={"source": "source_url", "title": "Title1"}),
            Document("# Content1 \nText 1", metadata={"source": "source_url2", "title": "Title2"}),
        ]
        ConfluenceDatasourceProcessor.use_window_joining = True
        result = self.processor._split_documents(docs)

        expected_result = {
            "source_url": [
                Document(
                    metadata={
                        "source": "source_url",
                        "title": "Title1",
                        "header": "Content1",
                        "instructions": "",
                        "reference": "1",
                    },
                    page_content="Page title: Title1.\nSource: source_url.\n\n\n# Content1\nText 1.\n\n# Content2\nText 2.\n\n# Content3\nText 3.",
                ),
                Document(
                    metadata={
                        "source": "source_url",
                        "title": "Title1",
                        "header": "Content2",
                        "instructions": "",
                        "reference": "2",
                    },
                    page_content="Page title: Title1.\nSource: source_url.\n\n\n# Content2\nText 2.\n\n# Content3\nText 3.\n\n# Content4\nText 4.",
                ),
                Document(
                    metadata={
                        "source": "source_url",
                        "title": "Title1",
                        "header": "Content1",
                        "instructions": "",
                        "reference": "1",
                    },
                    page_content="Page title: Title1.\nSource: source_url.\n\n\n# Content1\nText 1",
                ),
            ],
            "source_url2": [
                Document(
                    metadata={
                        "source": "source_url2",
                        "title": "Title2",
                        "header": "Content1",
                        "instructions": "",
                        "reference": "1",
                    },
                    page_content="Page title: Title2.\nSource: source_url2.\n\n\n# Content1\nText 1",
                )
            ],
        }

        def docs_to_tuples(docs):
            return [(d.metadata, d.page_content) for d in docs]

        for src, expected_docs in expected_result.items():
            actual_docs = result[src]
            self.assertEqual(len(actual_docs), len(expected_docs), f"Number of docs mismatch for source {src}")
            for actual, expected in zip(actual_docs, expected_docs):
                # Only compare the expected fields: real output may include more,
                # but must at least match the expected.
                for k, v in expected.metadata.items():
                    self.assertIn(
                        k, actual.metadata, f"Metadata key '{k}' missing for source {src}, doc:\n{actual.metadata}"
                    )
                    self.assertEqual(
                        actual.metadata[k],
                        v,
                        f"Incorrect metadata value for key '{k}', got {actual.metadata[k]}, expected {v}",
                    )
                self.assertEqual(
                    actual.page_content, expected.page_content, f"Page content does not match for source {src}"
                )

    def test_process_chunk(self):
        chunk = "chunk content"
        chunk_metadata = {"meta_key": "meta_value"}
        document = Document(page_content="original content", metadata={"title": "Test Title", "source": "Test Source"})
        result = self.processor._process_chunk(chunk, chunk_metadata, document)

        expected_content = "Page title: Test Title.\nSource: Test Source.\n\n\nchunk content"
        self.assertEqual(result.page_content, expected_content)
        self.assertEqual(result.metadata, chunk_metadata)

    def test_init_loader(self):
        self.processor.index_knowledge_base_config.cql = 'type = page'
        # Call the method
        loader = self.processor._init_loader()

        # Verify the CQL query enhancement
        expected_cql = "(type = page) ORDER BY type DESC"
        self.assertEqual(loader.cql, expected_cql)

        # Verify additional configurations
        self.assertEqual(loader.max_pages, 1000)
        self.assertEqual(loader.limit, 20)
        self.assertFalse(loader.include_archived_content)
        self.assertFalse(loader.include_comments)
        self.assertFalse(loader.include_attachments)
        self.assertFalse(loader.include_restricted_content)
        self.assertFalse(loader.keep_newlines)
        self.assertTrue(loader.keep_markdown_format)

    def test_add_type_and_order_by(self):
        # Test case where neither 'type=page' nor 'ORDER BY' are in the query
        cql = "space=DEV"
        expected = "(type=page AND (space=DEV)) ORDER BY type DESC"
        self.assertEqual(ConfluenceDatasourceProcessor._enhance_cql_query(cql), expected)

    def test_add_order_by_only(self):
        # Test case where 'type=page' is already in the query but not 'ORDER BY'
        cql = "type=page AND space=DEV"
        expected = "(type=page AND space=DEV) ORDER BY type DESC"
        self.assertEqual(ConfluenceDatasourceProcessor._enhance_cql_query(cql), expected)

    def test_add_type_only(self):
        # Test case where 'ORDER BY' is already in the query but not 'type=page'
        cql = "space=DEV ORDER BY title ASC"
        expected = "type=page AND (space=DEV ORDER BY title ASC)"
        self.assertEqual(ConfluenceDatasourceProcessor._enhance_cql_query(cql), expected)

    def test_no_modification_needed(self):
        # Test case where both 'type=page' and 'ORDER BY' are already in the query
        cql = "type=page AND space=DEV ORDER BY title ASC"
        expected = "type=page AND space=DEV ORDER BY title ASC"
        self.assertEqual(ConfluenceDatasourceProcessor._enhance_cql_query(cql), expected)

    def test_case_insensitivity_type(self):
        # Test case to check case insensitivity for 'type='
        cql = "TYPE=page AND space=DEV"
        expected = "(TYPE=page AND space=DEV) ORDER BY type DESC"
        self.assertEqual(ConfluenceDatasourceProcessor._enhance_cql_query(cql), expected)

    def test_project_query(self):
        # Test case to check case insensitivity for 'type='
        cql = "project=KAN"
        expected = "(type=page AND (project=KAN)) ORDER BY type DESC"
        self.assertEqual(ConfluenceDatasourceProcessor._enhance_cql_query(cql), expected)

    def test_project_query_is_empty(self):
        cfg = ConfluenceConfig(url="https://conf.org", token="token")
        with self.assertRaises(InvalidQueryException):
            ConfluenceDatasourceProcessor.check_confluence_query(cql="", confluence=cfg)

        with self.assertRaises(InvalidQueryException):
            ConfluenceDatasourceProcessor.check_confluence_query(cql="   ", confluence=cfg)

        with self.assertRaises(InvalidQueryException):
            ConfluenceDatasourceProcessor.check_confluence_query(cql=None, confluence=cfg)

    def test_url_joiner(self):
        self.assertEqual(
            ConfluenceDatasourceProcessor._url_joiner("https://example.com", "path"), "https://example.com/path"
        )
        self.assertEqual(
            ConfluenceDatasourceProcessor._url_joiner("https://example.com/", "path"), "https://example.com/path"
        )
        self.assertEqual(
            ConfluenceDatasourceProcessor._url_joiner("https://example.com", "/path"), "https://example.com/path"
        )
        self.assertEqual(
            ConfluenceDatasourceProcessor._url_joiner("https://example.com/", "/path"), "https://example.com/path"
        )

    def test_atlassian_url_handling(self):
        # Create a mock loader to test URL handling
        with patch('codemie.datasource.confluence_datasource_processor.ConfluenceDatasourceLoader') as mock_loader:
            # Test Atlassian URL without /wiki
            confluence = ConfluenceConfig(url="https://company.atlassian.net", token="token", cloud=True)
            config = IndexKnowledgeBaseConfluenceConfig(cql="space=DEV")
            ConfluenceDatasourceProcessor._initialize_confluence_loader(confluence, config)
            mock_loader.assert_called_with(
                url="https://company.atlassian.net/wiki",
                username=confluence.username,
                api_key="token",
                cql="(type=page AND (space=DEV)) ORDER BY type DESC",
                cloud=True,
                content_format=ContentFormat.VIEW,
                keep_markdown_format=True,
            )

            # Test Atlassian URL with /wiki already
            mock_loader.reset_mock()
            confluence = ConfluenceConfig(url="https://company.atlassian.net/wiki", token="token", cloud=True)
            ConfluenceDatasourceProcessor._initialize_confluence_loader(confluence, config)
            mock_loader.assert_called_with(
                url="https://company.atlassian.net/wiki",
                username=confluence.username,
                api_key="token",
                cql="(type=page AND (space=DEV)) ORDER BY type DESC",
                cloud=True,
                content_format=ContentFormat.VIEW,
                keep_markdown_format=True,
            )

            # Test non-Atlassian URL (should not be modified)
            mock_loader.reset_mock()
            confluence = ConfluenceConfig(url="https://confluence.example.com", token="token", cloud=True)
            ConfluenceDatasourceProcessor._initialize_confluence_loader(confluence, config)
            mock_loader.assert_called_with(
                url="https://confluence.example.com",
                username=confluence.username,
                api_key="token",
                cql="(type=page AND (space=DEV)) ORDER BY type DESC",
                cloud=True,
                content_format=ContentFormat.VIEW,
                keep_markdown_format=True,
            )

    def test_case_insensitivity_order_by(self):
        # Test case to check case insensitivity for 'ORDER BY'
        cql = "type=page AND space=DEV order by title ASC"
        expected = "type=page AND space=DEV order by title ASC"
        self.assertEqual(ConfluenceDatasourceProcessor._enhance_cql_query(cql), expected)

    def test_without_order_by(self):
        # Test case where 'ORDER BY' is not in the query but 'type=page' is present
        cql = "type=page AND space=DEV"
        expected = "(type=page AND space=DEV) ORDER BY type DESC"
        self.assertEqual(ConfluenceDatasourceProcessor._enhance_cql_query(cql), expected)
