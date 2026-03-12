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

"""Tests for GenerateDocumentsTree workflow node."""

import pytest
from unittest.mock import Mock, patch

from codemie.core.workflow_models import CustomWorkflowNode
from codemie.service.workflow_execution import WorkflowExecutionService
from codemie.workflows.callbacks.base_callback import BaseCallback
from codemie.workflows.models import AgentMessages
from codemie.workflows.nodes.generate_documents_tree import (
    GenerateDocumentsTree,
    _filter_by_regex,
    _filter_by_file_type,
    _filter_documents_tree,
)


@pytest.fixture
def mock_workflow_execution_service():
    """Create a mock WorkflowExecutionService."""
    return Mock(spec=WorkflowExecutionService)


@pytest.fixture
def mock_thought_queue():
    """Create a mock ThoughtQueue."""
    return Mock()


@pytest.fixture
def mock_callbacks():
    """Create a list of mock callbacks."""
    return [Mock(spec=BaseCallback)]


@pytest.fixture
def sample_documents_tree():
    """Create a sample documents tree for testing."""
    return [
        {"file_path": "/path/to/file1.py", "source": "source1"},
        {"file_path": "/path/to/file2.js", "source": "source2"},
        {"file_path": "/path/to/file3.py", "source": "source3"},
        {"file_path": "/path/to/README.md", "source": "source4"},
        {"file_path": "/path/to/test_file.py", "source": "source5"},
    ]


class TestFilterByRegex:
    """Test _filter_by_regex function."""

    def test_filter_by_regex_matches_file_path(self, sample_documents_tree):
        """Test filtering documents by regex pattern on file_path."""
        pattern = r"\.py$"
        result = _filter_by_regex(sample_documents_tree, pattern)

        assert len(result) == 3
        assert all(doc["file_path"].endswith(".py") for doc in result)

    def test_filter_by_regex_matches_multiple_patterns(self, sample_documents_tree):
        """Test filtering documents by regex with multiple matches."""
        pattern = r"(\.py|\.md)$"
        result = _filter_by_regex(sample_documents_tree, pattern)

        assert len(result) == 4

    def test_filter_by_regex_no_matches(self, sample_documents_tree):
        """Test filtering documents by regex with no matches."""
        pattern = r"\.cpp$"
        result = _filter_by_regex(sample_documents_tree, pattern)

        assert len(result) == 0

    def test_filter_by_regex_with_path_pattern(self, sample_documents_tree):
        """Test filtering documents by regex matching path components."""
        pattern = r"/path/to/test_"
        result = _filter_by_regex(sample_documents_tree, pattern)

        assert len(result) == 1
        assert "test_file.py" in result[0]["file_path"]

    def test_filter_by_regex_uses_source_fallback(self):
        """Test filtering uses source field when file_path is missing."""
        documents = [
            {"source": "file1.py"},
            {"source": "file2.js"},
        ]
        pattern = r"\.py$"
        result = _filter_by_regex(documents, pattern)

        assert len(result) == 1
        assert result[0]["source"] == "file1.py"

    @patch('codemie.workflows.nodes.generate_documents_tree.logger')
    def test_filter_by_regex_invalid_pattern_returns_all(self, mock_logger, sample_documents_tree):
        """Test filtering with invalid regex returns original list and logs error."""
        pattern = r"[invalid(regex"
        result = _filter_by_regex(sample_documents_tree, pattern)

        assert len(result) == len(sample_documents_tree)
        assert result == sample_documents_tree
        mock_logger.error.assert_called_once()
        assert "Error during filtering by regex pattern" in str(mock_logger.error.call_args)

    @patch('codemie.workflows.nodes.generate_documents_tree.logger')
    def test_filter_by_regex_logs_info(self, mock_logger, sample_documents_tree):
        """Test filtering by regex logs info message."""
        pattern = r"\.py$"
        _filter_by_regex(sample_documents_tree, pattern)

        mock_logger.info.assert_called_once()
        assert "Filtering documents tree by regex pattern" in str(mock_logger.info.call_args)


class TestFilterByFileType:
    """Test _filter_by_file_type function."""

    @patch('codemie.workflows.nodes.generate_documents_tree.check_file_type')
    def test_filter_by_file_type_success(self, mock_check_file_type, sample_documents_tree):
        """Test filtering documents by file type."""

        # Mock check_file_type to return True for .py files
        def check_file_type_side_effect(file_name, **kwargs):
            return file_name.endswith(".py")

        mock_check_file_type.side_effect = check_file_type_side_effect

        pattern = "*.py"
        result = _filter_by_file_type(sample_documents_tree, pattern)

        assert len(result) == 3
        assert all(doc["file_path"].endswith(".py") for doc in result)

    @patch('codemie.workflows.nodes.generate_documents_tree.check_file_type')
    def test_filter_by_file_type_no_matches(self, mock_check_file_type, sample_documents_tree):
        """Test filtering by file type with no matches."""
        mock_check_file_type.return_value = False

        pattern = "*.cpp"
        result = _filter_by_file_type(sample_documents_tree, pattern)

        assert len(result) == 0

    @patch('codemie.workflows.nodes.generate_documents_tree.check_file_type')
    def test_filter_by_file_type_uses_source_fallback(self, mock_check_file_type):
        """Test filtering uses source field when file_path is missing."""
        documents = [
            {"source": "file1.py"},
            {"source": "file2.js"},
        ]

        def check_file_type_side_effect(file_name, **kwargs):
            return file_name.endswith(".py")

        mock_check_file_type.side_effect = check_file_type_side_effect

        pattern = "*.py"
        result = _filter_by_file_type(documents, pattern)

        assert len(result) == 1
        assert result[0]["source"] == "file1.py"

    @patch('codemie.workflows.nodes.generate_documents_tree.logger')
    @patch('codemie.workflows.nodes.generate_documents_tree.check_file_type')
    def test_filter_by_file_type_exception_returns_all(self, mock_check_file_type, mock_logger, sample_documents_tree):
        """Test filtering by file type with exception returns original list."""
        mock_check_file_type.side_effect = Exception("Check failed")

        pattern = "*.py"
        result = _filter_by_file_type(sample_documents_tree, pattern)

        assert len(result) == len(sample_documents_tree)
        assert result == sample_documents_tree
        mock_logger.error.assert_called_once()
        assert "Error during filtering by file type pattern" in str(mock_logger.error.call_args)

    @patch('codemie.workflows.nodes.generate_documents_tree.logger')
    def test_filter_by_file_type_logs_info(self, mock_logger, sample_documents_tree):
        """Test filtering by file type logs info message."""
        with patch('codemie.workflows.nodes.generate_documents_tree.check_file_type', return_value=True):
            pattern = "*.py"
            _filter_by_file_type(sample_documents_tree, pattern)

            mock_logger.info.assert_called_once()
            assert "Filtering documents tree by file type pattern" in str(mock_logger.info.call_args)


class TestFilterDocumentsTree:
    """Test _filter_documents_tree function."""

    @patch('codemie.workflows.nodes.generate_documents_tree.logger')
    def test_filter_documents_tree_with_regex_pattern(self, mock_logger, sample_documents_tree):
        """Test filtering documents tree with regex pattern."""
        pattern = r"\.py$"
        result = _filter_documents_tree(
            documents_tree=sample_documents_tree, documents_filtering_pattern=pattern, documents_filter=None
        )

        assert len(result) == 3
        mock_logger.info.assert_called()

    @patch('codemie.workflows.nodes.generate_documents_tree.logger')
    @patch('codemie.workflows.nodes.generate_documents_tree.check_file_type')
    def test_filter_documents_tree_with_file_type_filter(
        self, mock_check_file_type, mock_logger, sample_documents_tree
    ):
        """Test filtering documents tree with file type filter."""
        mock_check_file_type.side_effect = lambda file_name, **kwargs: file_name.endswith(".py")

        pattern = "*.py"
        result = _filter_documents_tree(
            documents_tree=sample_documents_tree, documents_filtering_pattern=None, documents_filter=pattern
        )

        assert len(result) == 3
        mock_logger.info.assert_called()

    @patch('codemie.workflows.nodes.generate_documents_tree.logger')
    def test_filter_documents_tree_without_filters(self, mock_logger, sample_documents_tree):
        """Test filtering documents tree without any filters returns original list."""
        result = _filter_documents_tree(
            documents_tree=sample_documents_tree, documents_filtering_pattern=None, documents_filter=None
        )

        assert len(result) == len(sample_documents_tree)
        assert result == sample_documents_tree
        mock_logger.info.assert_called()

    @patch('codemie.workflows.nodes.generate_documents_tree.logger')
    def test_filter_documents_tree_logs_sizes(self, mock_logger, sample_documents_tree):
        """Test filtering logs original and filtered tree sizes."""
        pattern = r"\.py$"
        _filter_documents_tree(documents_tree=sample_documents_tree, documents_filtering_pattern=pattern)

        # Check that logger.info was called with size information
        info_calls = [str(call) for call in mock_logger.info.call_args_list]
        assert any("OriginalSize: 5" in call for call in info_calls)
        assert any("FilteredTreeSize: 3" in call for call in info_calls)

    @patch('codemie.workflows.nodes.generate_documents_tree.logger')
    def test_filter_documents_tree_prioritizes_regex_over_file_type(self, mock_logger, sample_documents_tree):
        """Test that regex pattern takes priority over file type filter."""
        regex_pattern = r"\.md$"
        file_type_pattern = "*.py"

        result = _filter_documents_tree(
            documents_tree=sample_documents_tree,
            documents_filtering_pattern=regex_pattern,
            documents_filter=file_type_pattern,
        )

        # Should filter by regex pattern (*.md) not file type (*.py)
        assert len(result) == 1
        assert result[0]["file_path"].endswith(".md")


class TestGenerateDocumentsTreeNode:
    """Test GenerateDocumentsTree node."""

    def test_init(self, mock_callbacks, mock_workflow_execution_service, mock_thought_queue):
        """Test GenerateDocumentsTree initialization."""
        node = GenerateDocumentsTree(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
        )

        assert node is not None

    @patch('codemie.workflows.nodes.generate_documents_tree.get_documents_tree_by_datasource_id')
    @patch('codemie.workflows.nodes.generate_documents_tree.logger')
    def test_execute_basic(
        self,
        mock_logger,
        mock_get_documents_tree,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        sample_documents_tree,
    ):
        """Test execute method with basic configuration."""
        node = GenerateDocumentsTree(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
        )

        mock_get_documents_tree.return_value = sample_documents_tree

        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node-id"
        custom_node.config = {"datasource_id": "test-datasource-id"}

        state_schema = Mock(spec=AgentMessages)
        execution_context = {"custom_node": custom_node}

        result = node.execute(state_schema, execution_context)

        assert "documents_tree" in result
        assert len(result["documents_tree"]) == 5
        mock_get_documents_tree.assert_called_once_with("test-datasource-id", include_content=False)

    @patch('codemie.workflows.nodes.generate_documents_tree.get_documents_tree_by_datasource_id')
    @patch('codemie.workflows.nodes.generate_documents_tree.logger')
    def test_execute_with_custom_output_key(
        self,
        mock_logger,
        mock_get_documents_tree,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        sample_documents_tree,
    ):
        """Test execute method with custom output key."""
        node = GenerateDocumentsTree(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
        )

        mock_get_documents_tree.return_value = sample_documents_tree

        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node-id"
        custom_node.config = {"datasource_id": "test-datasource-id", "output_key": "custom_tree"}

        state_schema = Mock(spec=AgentMessages)
        execution_context = {"custom_node": custom_node}

        result = node.execute(state_schema, execution_context)

        assert "custom_tree" in result
        assert "documents_tree" not in result
        assert len(result["custom_tree"]) == 5

    @patch('codemie.workflows.nodes.generate_documents_tree.get_documents_tree_by_datasource_id')
    @patch('codemie.workflows.nodes.generate_documents_tree.logger')
    def test_execute_with_include_content(
        self,
        mock_logger,
        mock_get_documents_tree,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        sample_documents_tree,
    ):
        """Test execute method with include_content flag."""
        node = GenerateDocumentsTree(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
        )

        mock_get_documents_tree.return_value = sample_documents_tree

        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node-id"
        custom_node.config = {"datasource_id": "test-datasource-id", "include_content": True}

        state_schema = Mock(spec=AgentMessages)
        execution_context = {"custom_node": custom_node}

        node.execute(state_schema, execution_context)

        mock_get_documents_tree.assert_called_once_with("test-datasource-id", include_content=True)

    @patch('codemie.workflows.nodes.generate_documents_tree.get_documents_tree_by_datasource_id')
    @patch('codemie.workflows.nodes.generate_documents_tree.logger')
    def test_execute_with_regex_filtering(
        self,
        mock_logger,
        mock_get_documents_tree,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        sample_documents_tree,
    ):
        """Test execute method with regex filtering pattern."""
        node = GenerateDocumentsTree(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
        )

        mock_get_documents_tree.return_value = sample_documents_tree

        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node-id"
        custom_node.config = {"datasource_id": "test-datasource-id", "documents_filtering_pattern": r"\.py$"}

        state_schema = Mock(spec=AgentMessages)
        execution_context = {"custom_node": custom_node}

        result = node.execute(state_schema, execution_context)

        assert len(result["documents_tree"]) == 3
        assert all(doc["file_path"].endswith(".py") for doc in result["documents_tree"])

    @patch('codemie.workflows.nodes.generate_documents_tree.get_documents_tree_by_datasource_id')
    @patch('codemie.workflows.nodes.generate_documents_tree.check_file_type')
    @patch('codemie.workflows.nodes.generate_documents_tree.logger')
    def test_execute_with_file_type_filtering(
        self,
        mock_logger,
        mock_check_file_type,
        mock_get_documents_tree,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        sample_documents_tree,
    ):
        """Test execute method with file type filtering."""
        node = GenerateDocumentsTree(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
        )

        mock_get_documents_tree.return_value = sample_documents_tree
        mock_check_file_type.side_effect = lambda file_name, **kwargs: file_name.endswith(".js")

        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node-id"
        custom_node.config = {"datasource_id": "test-datasource-id", "documents_filter": "*.js"}

        state_schema = Mock(spec=AgentMessages)
        execution_context = {"custom_node": custom_node}

        result = node.execute(state_schema, execution_context)

        assert len(result["documents_tree"]) == 1
        assert result["documents_tree"][0]["file_path"].endswith(".js")

    @patch('codemie.workflows.nodes.generate_documents_tree.get_documents_tree_by_datasource_id')
    @patch('codemie.workflows.nodes.generate_documents_tree.logger')
    def test_execute_logs_execution_details(
        self,
        mock_logger,
        mock_get_documents_tree,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        sample_documents_tree,
    ):
        """Test execute method logs execution details."""
        node = GenerateDocumentsTree(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
        )

        mock_get_documents_tree.return_value = sample_documents_tree

        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node-id"
        custom_node.config = {
            "datasource_id": "test-datasource-id",
            "documents_filtering_pattern": r"\.py$",
            "include_content": True,
        }

        state_schema = Mock(spec=AgentMessages)
        execution_context = {"custom_node": custom_node}

        node.execute(state_schema, execution_context)

        # Verify logging was called
        assert mock_logger.info.call_count >= 1
        log_message = str(mock_logger.info.call_args_list[0])
        assert "Execute test-node-id" in log_message
        assert "DatasourceId=test-datasource-id" in log_message

    def test_get_task(self, mock_callbacks, mock_workflow_execution_service, mock_thought_queue):
        """Test get_task method returns correct task description."""
        node = GenerateDocumentsTree(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
        )

        state_schema = Mock(spec=AgentMessages)
        task = node.get_task(state_schema)

        assert task == "List documents from datasource"

    @patch('codemie.workflows.nodes.generate_documents_tree.get_documents_tree_by_datasource_id')
    @patch('codemie.workflows.nodes.generate_documents_tree.logger')
    def test_execute_with_empty_documents_tree(
        self,
        mock_logger,
        mock_get_documents_tree,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
    ):
        """Test execute method with empty documents tree."""
        node = GenerateDocumentsTree(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
        )

        mock_get_documents_tree.return_value = []

        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node-id"
        custom_node.config = {"datasource_id": "test-datasource-id"}

        state_schema = Mock(spec=AgentMessages)
        execution_context = {"custom_node": custom_node}

        result = node.execute(state_schema, execution_context)

        assert "documents_tree" in result
        assert len(result["documents_tree"]) == 0
