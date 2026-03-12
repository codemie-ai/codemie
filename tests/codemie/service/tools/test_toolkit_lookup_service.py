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

"""Unit tests for the ToolkitLookupService."""

import uuid
from unittest.mock import patch, Mock, MagicMock

import pytest
from langchain_core.documents import Document

from codemie.core.models import AssistantChatRequest
from codemie.service.tools.toolkit_lookup_service import ToolkitLookupService
from codemie_tools.base.models import Tool, ToolKit


class TestToolkitLookupService:
    """Test suite for the ToolkitLookupService class."""

    @pytest.fixture
    def mock_elasticsearch_store(self):
        """Fixture for mocking ElasticsearchStore."""
        mock_store = Mock()
        mock_store.add_documents = Mock()
        return mock_store

    @pytest.fixture
    def mock_elastic_client(self):
        """Fixture for mocking ElasticSearchClient."""
        mock_client = Mock()
        mock_client.indices = Mock()
        mock_client.indices.exists = Mock(return_value=True)
        mock_client.indices.delete = Mock()
        return mock_client

    @pytest.fixture
    def mock_search_and_rerank_tool(self):
        """Fixture for mocking SearchAndRerankTool."""
        mock_tool = Mock()
        mock_tool.execute = Mock(
            return_value=[
                Document(
                    page_content="test tool content",
                    metadata={
                        "name": "test_tool",
                        "description": "A test tool",
                        "label": "Test Tool",
                        "toolkit": "test_toolkit",
                        "toolkit_description": "Test toolkit description",
                        "name_tokens": "test tool",
                    },
                    id=str(uuid.uuid4()),
                ),
                Document(
                    page_content="another tool content",
                    metadata={
                        "name": "another_tool",
                        "description": "Another test tool",
                        "label": "Another Tool",
                        "toolkit": "test_toolkit",
                        "toolkit_description": "Test toolkit description",
                        "name_tokens": "another tool",
                    },
                    id=str(uuid.uuid4()),
                ),
                Document(
                    page_content="different toolkit tool content",
                    metadata={
                        "name": "different_tool",
                        "description": "A tool from a different toolkit",
                        "label": "Different Tool",
                        "toolkit": "different_toolkit",
                        "toolkit_description": "Different toolkit description",
                        "name_tokens": "different tool",
                    },
                    id=str(uuid.uuid4()),
                ),
            ]
        )
        return mock_tool

    @pytest.fixture
    def mock_tools_info(self):
        """Fixture for mocking tools info data."""
        return [
            {
                "toolkit": "test_toolkit",
                "description": "Test toolkit description",
                "tools": [
                    {"name": "test_tool", "description": "A test tool", "label": "Test Tool"},
                    {"name": "another_tool", "description": "Another test tool", "label": "Another Tool"},
                ],
            },
            {
                "toolkit": "different_toolkit",
                "description": "Different toolkit description",
                "tools": [
                    {
                        "name": "different_tool",
                        "description": "A tool from a different toolkit",
                        "label": "Different Tool",
                    }
                ],
            },
        ]

    @patch("codemie.service.tools.toolkit_lookup_service.ElasticSearchClient")
    @patch("codemie.service.tools.toolkit_lookup_service.get_elasticsearch")
    @patch("codemie.service.tools.toolkit_lookup_service.llm_service")
    def test_index_all_tools(
        self,
        mock_llm_service,
        mock_get_elasticsearch,
        mock_elastic_client_class,
        mock_elasticsearch_store,
        mock_tools_info,
    ):
        """Test index_all_tools method."""
        # Setup mocks
        mock_get_elasticsearch.return_value = mock_elasticsearch_store
        mock_elastic_client = MagicMock()
        mock_elastic_client_class.get_client.return_value = mock_elastic_client
        mock_llm_service.default_embedding_model = "text-embedding-ada-002"
        mock_llm_service.get_embedding_deployment_name.return_value = "text-embedding-ada-002"

        # Mock _get_tools_metadata directly
        with patch.object(ToolkitLookupService, '_get_tools_metadata', return_value=mock_tools_info):
            # Call the method
            result = ToolkitLookupService.index_all_tools()

        # Assertions
        assert result == 3  # Total number of tools in mock_tools_info
        mock_get_elasticsearch.assert_called_once()
        mock_elasticsearch_store.add_documents.assert_called_once()

        # Check that the correct number of documents is being indexed
        documents = mock_elasticsearch_store.add_documents.call_args[1]["documents"]
        assert len(documents) == 3

        # Verify document structure
        for doc in documents:
            assert isinstance(doc, Document)
            assert "name" in doc.metadata
            assert doc.metadata["name"] in ["test_tool", "another_tool", "different_tool"]

    @patch("codemie.service.tools.toolkit_lookup_service.SearchAndRerankTool")
    def test_get_tools_by_query(self, mock_search_and_rerank_class, mock_search_and_rerank_tool):
        """Test get_tools_by_query method."""
        # Setup mocks
        mock_search_and_rerank_class.return_value = mock_search_and_rerank_tool

        # Call the method
        result = ToolkitLookupService.get_tools_by_query("test query", limit=5)

        # Assertions
        mock_search_and_rerank_class.assert_called_once_with(
            query="test query", top_k=5, index_name="codemie_tools", tool_names_filter=None
        )
        mock_search_and_rerank_tool.execute.assert_called_once()

        # Verify results
        assert len(result) == 2  # Two toolkits: test_toolkit and different_toolkit

        # Check first toolkit
        test_toolkit = next((tk for tk in result if tk.toolkit == "test_toolkit"), None)
        assert test_toolkit is not None
        assert len(test_toolkit.tools) == 2
        assert test_toolkit.tools[0].name in ["test_tool", "another_tool"]
        assert test_toolkit.tools[1].name in ["test_tool", "another_tool"]

        # Check second toolkit
        diff_toolkit = next((tk for tk in result if tk.toolkit == "different_toolkit"), None)
        assert diff_toolkit is not None
        assert len(diff_toolkit.tools) == 1
        assert diff_toolkit.tools[0].name == "different_tool"

    @patch("codemie.service.tools.toolkit_lookup_service.SearchAndRerankTool")
    def test_get_tools_by_query_handles_errors(self, mock_search_and_rerank_class):
        """Test get_tools_by_query handles exceptions properly."""
        # Setup mock to raise an exception
        mock_search_and_rerank = Mock()
        mock_search_and_rerank.execute.side_effect = Exception("Search failed")
        mock_search_and_rerank_class.return_value = mock_search_and_rerank

        # Call the method - should not raise an exception
        result = ToolkitLookupService.get_tools_by_query("test query")

        # Assertions
        assert result == []  # Empty list returned on error
        mock_search_and_rerank_class.assert_called_once()
        mock_search_and_rerank.execute.assert_called_once()

    @patch("codemie.service.tools.toolkit_lookup_service.SearchAndRerankTool")
    def test_get_tools_by_query_handles_malformed_data(self, mock_search_and_rerank_class, mock_search_and_rerank_tool):
        """Test get_tools_by_query handles malformed document data."""
        # Setup mock to return malformed document metadata
        mock_search_and_rerank_tool.execute.return_value = [
            Document(
                page_content="invalid content",
                metadata={
                    "name": "test_tool",
                    # Missing required toolkit field
                },
                id=str(uuid.uuid4()),
            )
        ]
        mock_search_and_rerank_class.return_value = mock_search_and_rerank_tool

        # Call the method - should handle the malformed data gracefully
        result = ToolkitLookupService.get_tools_by_query("test query")

        # Assertions
        assert result == []  # No valid toolkits could be reconstructed

    def test_build_search_query_with_history(self):
        """Test build_search_query_with_history method."""
        # Test with text only
        request = AssistantChatRequest(text="Find code in repository", history=[])
        query = ToolkitLookupService.build_search_query_with_history(request)
        assert query == "Find code in repository"

        # Test with history
        request = AssistantChatRequest(
            text="Show me how to search code",
            history=[
                {"role": "User", "message": "I need to search for code"},
                {"role": "Assistant", "message": "I can help with that"},
                {"role": "User", "message": "How do I search?"},
            ],
        )
        query = ToolkitLookupService.build_search_query_with_history(request)
        assert query == "Show me how to search code\nI need to search for code\nI can help with that\nHow do I search?"

        # Test with None request
        query = ToolkitLookupService.build_search_query_with_history(None)
        assert query is None

        # Test with empty text
        request = AssistantChatRequest(text="", history=[])
        query = ToolkitLookupService.build_search_query_with_history(request)
        assert query is None

    @patch("codemie.service.tools.toolkit_lookup_service.SearchAndRerankTool.tokenize_tool_name")
    def test_create_tool_document(self, mock_tokenize):
        """Test _create_tool_document method."""
        # Setup mock
        mock_tokenize.return_value = ["test", "tool"]

        # Test data
        tool_meta = {"name": "test_tool", "description": "A test tool", "label": "Test Tool"}
        toolkit_name = "test_toolkit"
        toolkit_level_meta = {"toolkit": "test_toolkit", "toolkit_description": "Test toolkit description"}

        # Call method
        doc = ToolkitLookupService._create_tool_document(tool_meta, toolkit_name, toolkit_level_meta)

        # Assertions
        assert isinstance(doc, Document)
        assert doc.metadata["name"] == "test_tool"
        assert doc.metadata["description"] == "A test tool"
        assert doc.metadata["label"] == "Test Tool"
        assert doc.metadata["toolkit"] == "test_toolkit"
        assert doc.metadata["toolkit_description"] == "Test toolkit description"
        assert doc.metadata["name_tokens"] == "test tool"
        assert "test_tool\ntest tool\nA test tool" in doc.page_content

    def test_extract_toolkit_metadata(self):
        """Test _extract_toolkit_metadata method."""
        toolkit_meta = {
            "toolkit": "test_toolkit",
            "description": "Test toolkit description",
            "icon": "test-icon",
            "tools": [{"name": "test_tool"}],
        }

        result = ToolkitLookupService._extract_toolkit_metadata(toolkit_meta)

        # Assertions
        assert result["toolkit"] == "test_toolkit"  # Not prefixed
        assert result["toolkit_description"] == "Test toolkit description"  # Prefixed
        assert result["toolkit_icon"] == "test-icon"  # Prefixed
        assert "tools" not in result  # Excluded

    def test_reconstruct_toolkit_from_metadata(self):
        """Test _reconstruct_toolkit_from_metadata method."""
        doc_metadata = {
            "name": "test_tool",
            "description": "A test tool",
            "label": "Test Tool",
            "toolkit": "test_toolkit",
            "toolkit_description": "Test toolkit description",
            "name_tokens": "test tool",
        }

        toolkit = ToolkitLookupService._reconstruct_toolkit_from_metadata(doc_metadata)

        # Assertions
        assert isinstance(toolkit, ToolKit)
        assert toolkit.toolkit == "test_toolkit"
        # ToolKit class doesn't have a description field directly, it comes from toolkit_description in metadata
        assert len(toolkit.tools) == 1

        tool = toolkit.tools[0]
        assert isinstance(tool, Tool)
        assert tool.name == "test_tool"
        assert tool.description == "A test tool"
        assert tool.label == "Test Tool"

    def test_extract_history_messages(self):
        """Test _extract_history_messages method."""
        # Test with history as list of dictionaries
        request = AssistantChatRequest(
            text="Current query",
            history=[
                {"role": "User", "message": "First message"},
                {"role": "Assistant", "message": "Bot response"},
                {"role": "User", "message": "Second message"},
            ],
        )

        messages = ToolkitLookupService._extract_history_messages(request)

        # Assertions
        assert len(messages) == 3
        assert messages == ["First message", "Bot response", "Second message"]

        # Test with empty history
        request = AssistantChatRequest(text="Current query", history=[])
        messages = ToolkitLookupService._extract_history_messages(request)
        assert messages == []

        # Test with no history attribute
        request = AssistantChatRequest(text="Current query")
        messages = ToolkitLookupService._extract_history_messages(request)
        assert messages == []

        # Test with more than 5 messages (should only return last 5)
        request = AssistantChatRequest(
            text="Current query", history=[{"role": "User", "message": f"Message {i}"} for i in range(10)]
        )
        messages = ToolkitLookupService._extract_history_messages(request)
        assert len(messages) == 5
        assert messages == ["Message 5", "Message 6", "Message 7", "Message 8", "Message 9"]

        # Test with exception during processing - mock the history attribute to cause exception
        request = AssistantChatRequest(text="Current query", history=[])
        # Replace history with invalid format after validation
        request.__dict__['history'] = [{"invalid_format": "will cause exception"}]
        messages = ToolkitLookupService._extract_history_messages(request)
        assert messages == []

    @patch("codemie.service.tools.toolkit_lookup_service.ElasticSearchClient")
    @patch("codemie.service.tools.toolkit_lookup_service.get_elasticsearch")
    @patch("codemie.service.tools.toolkit_lookup_service.config")
    @patch("codemie.service.tools.toolkit_lookup_service.llm_service")
    def test_setup_elasticsearch_index(
        self, mock_llm_service, mock_config, mock_get_elasticsearch, mock_elastic_client_class, mock_elastic_client
    ):
        """Test _setup_elasticsearch_index method."""
        # Setup mocks
        mock_config.TOOLS_INDEX_NAME = "test_tools_index"
        mock_llm_service.default_embedding_model = "text-embedding-ada-002"
        mock_llm_service.get_embedding_deployment_name.return_value = "text-embedding-ada-002"
        mock_elastic_client_class.get_client.return_value = mock_elastic_client
        mock_store = Mock()
        mock_get_elasticsearch.return_value = mock_store

        # Call the method
        index_name, store = ToolkitLookupService._setup_elasticsearch_index()

        # Assertions
        assert index_name == "test_tools_index"
        assert store == mock_store
        mock_get_elasticsearch.assert_called_once_with("test_tools_index", "text-embedding-ada-002")
        mock_elastic_client.indices.exists.assert_called_once_with(index="test_tools_index")
        mock_elastic_client.indices.delete.assert_called_once_with(index="test_tools_index")

    @patch("codemie.service.tools.toolkit_lookup_service.ElasticSearchClient")
    @patch("codemie.service.tools.toolkit_lookup_service.get_elasticsearch")
    @patch("codemie.service.tools.toolkit_lookup_service.config")
    @patch("codemie.service.tools.toolkit_lookup_service.llm_service")
    def test_setup_elasticsearch_index_handles_errors(
        self, mock_llm_service, mock_config, mock_get_elasticsearch, mock_elastic_client_class
    ):
        """Test _setup_elasticsearch_index method handles errors gracefully."""
        # Setup mocks
        mock_config.TOOLS_INDEX_NAME = "test_tools_index"
        mock_llm_service.default_embedding_model = "text-embedding-ada-002"
        mock_llm_service.get_embedding_deployment_name.return_value = "text-embedding-ada-002"

        # Mock client.indices.exists to raise exception
        mock_elastic_client = Mock()
        mock_elastic_client.indices = Mock()
        mock_elastic_client.indices.exists.side_effect = Exception("Index check failed")
        mock_elastic_client_class.get_client.return_value = mock_elastic_client

        mock_store = Mock()
        mock_get_elasticsearch.return_value = mock_store

        # Call the method - should handle the exception and continue
        index_name, store = ToolkitLookupService._setup_elasticsearch_index()

        # Assertions
        assert index_name == "test_tools_index"
        assert store == mock_store
        mock_elastic_client.indices.exists.assert_called_once()
        mock_elastic_client.indices.delete.assert_not_called()  # Delete not called due to exception

    @patch("codemie.service.tools.toolkit_lookup_service.logger")
    def test_index_documents_handles_exceptions(self, mock_logger):
        """Test _index_documents handles exceptions properly."""
        # Create mock store that raises exception
        mock_store = Mock()
        mock_store.add_documents.side_effect = Exception("Indexing failed")

        # Mock _setup_elasticsearch_index to return our mocks
        with patch.object(ToolkitLookupService, '_setup_elasticsearch_index', return_value=("test_index", mock_store)):
            # Call the method - should raise the exception
            with pytest.raises(Exception) as excinfo:
                ToolkitLookupService._index_documents([Document(page_content="test", metadata={})])

            # Verify exception message
            assert "Indexing failed" in str(excinfo.value)

            # Verify logging
            mock_logger.error.assert_called_once()
            assert "Failed to index tools" in mock_logger.error.call_args[0][0]

    @patch("codemie.service.tools.toolkit_lookup_service.SearchAndRerankTool.tokenize_tool_name")
    def test_prepare_tool_documents(self, mock_tokenize):
        """Test _prepare_tool_documents method."""
        # Setup mock
        mock_tokenize.return_value = ["tokenized", "name"]

        # Test data
        tools_metadata = [
            {
                "toolkit": "test_toolkit",
                "description": "Test toolkit description",
                "icon": "test-icon",
                "tools": [
                    {"name": "test_tool1", "description": "Tool 1 description", "label": "Test Tool 1"},
                    {"name": "test_tool2", "description": "Tool 2 description", "label": "Test Tool 2"},
                ],
            },
            {
                "toolkit": "another_toolkit",
                "description": "Another toolkit description",
                "tools": [{"name": "another_tool", "description": "Another tool description", "label": "Another Tool"}],
            },
        ]

        # Call method
        documents = ToolkitLookupService._prepare_tool_documents(tools_metadata)

        # Assertions
        assert len(documents) == 3  # Total number of tools across all toolkits

        # Verify each document
        for doc in documents:
            assert isinstance(doc, Document)
            assert "name" in doc.metadata
            assert doc.metadata["name"] in ["test_tool1", "test_tool2", "another_tool"]

            # Check for toolkit metadata presence
            assert "toolkit" in doc.metadata
            if doc.metadata["name"].startswith("test_tool"):
                assert doc.metadata["toolkit"] == "test_toolkit"
                assert doc.metadata["toolkit_description"] == "Test toolkit description"
                assert doc.metadata["toolkit_icon"] == "test-icon"
            else:
                assert doc.metadata["toolkit"] == "another_toolkit"
                assert doc.metadata["toolkit_description"] == "Another toolkit description"

            # Check for name_tokens field
            assert "name_tokens" in doc.metadata
            assert doc.metadata["name_tokens"] == "tokenized name"

            # Check page content format
            assert doc.metadata["name"] in doc.page_content
            assert "tokenized name" in doc.page_content
            assert doc.metadata["description"] in doc.page_content
