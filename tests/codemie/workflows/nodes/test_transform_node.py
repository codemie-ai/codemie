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

"""Tests for TransformNode workflow node."""

import json
import pytest
from unittest.mock import Mock

from codemie.core.workflow_models import WorkflowState, CustomWorkflowNode
from codemie.service.workflow_execution import WorkflowExecutionService
from codemie.workflows.callbacks.base_callback import BaseCallback
from codemie.workflows.models import AgentMessages
from codemie.workflows.nodes.transform_node import TransformNode, TransformationError
from codemie.workflows.utils.transform_node_utils import parse_array_index


@pytest.fixture
def mock_workflow_execution_service():
    """Create a mock WorkflowExecutionService."""
    service = Mock(spec=WorkflowExecutionService)
    service.workflow_execution_id = "test-execution-id"
    return service


@pytest.fixture
def mock_thought_queue():
    """Create a mock ThoughtQueue."""
    return Mock()


@pytest.fixture
def mock_callbacks():
    """Create a list of mock callbacks."""
    return [Mock(spec=BaseCallback)]


@pytest.fixture
def mock_workflow_state():
    """Create a mock WorkflowState."""
    state = Mock(spec=WorkflowState)
    state.task = "Transform data using configured mappings"
    return state


@pytest.fixture
def basic_custom_node():
    """Create a basic CustomWorkflowNode with extract mappings."""
    node = Mock(spec=CustomWorkflowNode)
    node.id = "test-transform-node"
    node.config = {
        'input_source': 'context_store',
        'mappings': [
            {'output_field': 'title', 'type': 'extract', 'source_path': 'issue.title'},
            {'output_field': 'status', 'type': 'extract', 'source_path': 'issue.status', 'default': 'unknown'},
        ],
    }
    return node


@pytest.fixture
def sample_state_schema():
    """Create a sample state schema with context store."""
    return {
        'context_store': {'issue': {'title': 'Test Issue', 'status': 'open', 'priority': 'high'}},
        'messages': [],
        'user_input': '',
    }


class TestTransformNodeInitialization:
    """Test TransformNode initialization."""

    def test_init(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        basic_custom_node,
    ):
        """Test successful initialization."""
        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=basic_custom_node,
            execution_id="test-exec-id",
        )

        assert node.workflow_state == mock_workflow_state
        assert node.custom_node == basic_custom_node
        assert node.execution_id == "test-exec-id"


class TestTransformNodeExtractField:
    """Test _extract_field method."""

    def test_extract_simple_field(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        basic_custom_node,
    ):
        """Test extracting a simple field with dot notation."""
        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=basic_custom_node,
        )

        source_data = {'user': {'name': 'John', 'age': 30}}
        result = node._extract_field(source_data, 'user.name')

        assert result == 'John'

    def test_extract_nested_field(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        basic_custom_node,
    ):
        """Test extracting a deeply nested field."""
        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=basic_custom_node,
        )

        source_data = {'company': {'department': {'team': {'lead': 'Alice'}}}}
        result = node._extract_field(source_data, 'company.department.team.lead')

        assert result == 'Alice'

    def test_extract_field_not_found_returns_default(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        basic_custom_node,
    ):
        """Test extracting non-existent field returns default value."""
        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=basic_custom_node,
        )

        source_data = {'user': {'name': 'John'}}
        result = node._extract_field(source_data, 'user.email', default='no-email')

        assert result == 'no-email'

    def test_extract_field_empty_path_returns_default(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        basic_custom_node,
    ):
        """Test extracting with empty path returns default."""
        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=basic_custom_node,
        )

        source_data = {'user': {'name': 'John'}}
        result = node._extract_field(source_data, '', default='default_value')

        assert result == 'default_value'


class TestTransformNodeExtractSourceData:
    """Test _extract_source_data method."""

    def test_extract_from_context_store(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        basic_custom_node,
    ):
        """Test extracting data from context_store."""
        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=basic_custom_node,
        )

        state_schema = {'context_store': {'key1': 'value1', 'key2': 'value2'}, 'messages': []}
        result = node._extract_source_data(state_schema)

        assert result == {'key1': 'value1', 'key2': 'value2'}

    def test_extract_from_messages_with_json_content(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test extracting data from messages with JSON content."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {'input_source': 'messages', 'mappings': []}

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        message = Mock()
        message.content = '{"status": "completed", "result": "success"}'
        state_schema = {'context_store': {}, 'messages': [message]}

        result = node._extract_source_data(state_schema)

        assert result == {'status': 'completed', 'result': 'success'}

    def test_extract_from_user_input_with_json(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test extracting data from user_input with JSON."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {'input_source': 'user_input', 'mappings': []}

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {'context_store': {}, 'messages': [], 'user_input': '{"query": "test query"}'}

        result = node._extract_source_data(state_schema)

        assert result == {'query': 'test query'}


class TestTransformNodeApplyMappings:
    """Test _apply_mappings and mapping types."""

    def test_apply_extract_mapping(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        basic_custom_node,
        sample_state_schema,
    ):
        """Test applying extract type mapping."""
        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=basic_custom_node,
        )

        result = node.execute(sample_state_schema, {})

        assert result['title'] == 'Test Issue'
        assert result['status'] == 'open'

    def test_apply_constant_mapping(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test applying constant type mapping."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'mappings': [{'output_field': 'type', 'type': 'constant', 'value': 'bug'}],
        }

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {'context_store': {}, 'messages': []}
        result = node.execute(state_schema, {})

        assert result['type'] == 'bug'

    def test_apply_condition_mapping_true(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test applying condition mapping when condition is true."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'mappings': [
                {
                    'output_field': 'is_critical',
                    'type': 'condition',
                    'condition': 'priority == "high"',
                    'then_value': True,
                    'else_value': False,
                }
            ],
        }

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {'context_store': {'priority': 'high'}, 'messages': []}
        result = node.execute(state_schema, {})

        assert result['is_critical'] is True

    def test_apply_condition_mapping_false(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test applying condition mapping when condition is false."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'mappings': [
                {
                    'output_field': 'is_critical',
                    'type': 'condition',
                    'condition': 'priority == "high"',
                    'then_value': True,
                    'else_value': False,
                }
            ],
        }

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {'context_store': {'priority': 'low'}, 'messages': []}
        result = node.execute(state_schema, {})

        assert result['is_critical'] is False

    def test_apply_template_mapping(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test applying Jinja2 template mapping."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'mappings': [
                {
                    'output_field': 'description',
                    'type': 'template',
                    'template': 'Issue: {{ title }} - Status: {{ status }}',
                }
            ],
        }

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {'context_store': {'title': 'Bug Report', 'status': 'open'}, 'messages': []}
        result = node.execute(state_schema, {})

        assert result['description'] == 'Issue: Bug Report - Status: open'

    def test_apply_script_mapping(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test applying script type mapping."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'mappings': [{'output_field': 'total', 'type': 'script', 'script': 'price * quantity'}],
        }

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {'context_store': {'price': 10, 'quantity': 5}, 'messages': []}
        result = node.execute(state_schema, {})

        assert result['total'] == 50

    def test_apply_multiple_mappings(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test applying multiple mappings in sequence."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'mappings': [
                {'output_field': 'title', 'type': 'extract', 'source_path': 'issue.title'},
                {'output_field': 'status', 'type': 'extract', 'source_path': 'issue.status'},
                {'output_field': 'type', 'type': 'constant', 'value': 'bug'},
            ],
        }

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {'context_store': {'issue': {'title': 'Test', 'status': 'open'}}, 'messages': []}
        result = node.execute(state_schema, {})

        assert result['title'] == 'Test'
        assert result['status'] == 'open'
        assert result['type'] == 'bug'


class TestTransformNodeArrayMapping:
    """Test _map_array and array mapping functionality."""

    def test_map_array_extract_field_from_dicts(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test mapping array of dictionaries to extract specific field."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'mappings': [
                {'output_field': 'label_names', 'type': 'array_map', 'source_path': 'labels', 'item_field': 'name'}
            ],
        }

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {
            'context_store': {'labels': [{'name': 'bug', 'color': 'red'}, {'name': 'urgent', 'color': 'yellow'}]},
            'messages': [],
        }
        result = node.execute(state_schema, {})

        assert result['label_names'] == ['bug', 'urgent']

    def test_map_array_no_item_field_returns_items(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test mapping array without item_field returns all items."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'mappings': [{'output_field': 'numbers', 'type': 'array_map', 'source_path': 'values'}],
        }

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {'context_store': {'values': [1, 2, 3, 4, 5]}, 'messages': []}
        result = node.execute(state_schema, {})

        assert result['numbers'] == [1, 2, 3, 4, 5]

    def test_map_array_with_filter_condition(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test mapping array with filter condition."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'mappings': [
                {
                    'output_field': 'high_priority_titles',
                    'type': 'array_map',
                    'source_path': 'issues',
                    'item_field': 'title',
                    'filter_condition': 'item["priority"] == "high"',
                }
            ],
        }

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {
            'context_store': {
                'issues': [
                    {'title': 'Bug 1', 'priority': 'high'},
                    {'title': 'Bug 2', 'priority': 'low'},
                    {'title': 'Bug 3', 'priority': 'high'},
                ]
            },
            'messages': [],
        }
        result = node.execute(state_schema, {})

        assert result['high_priority_titles'] == ['Bug 1', 'Bug 3']

    def test_map_array_non_list_returns_empty_array(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test mapping non-list value returns empty array."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'mappings': [{'output_field': 'items', 'type': 'array_map', 'source_path': 'not_an_array'}],
        }

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {'context_store': {'not_an_array': 'string_value'}, 'messages': []}
        result = node.execute(state_schema, {})

        assert result['items'] == []

    def test_map_array_object_attribute_access(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test mapping array with object attribute access."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'mappings': [
                {'output_field': 'names', 'type': 'array_map', 'source_path': 'objects', 'item_field': 'name'}
            ],
        }

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        # Create mock objects with name attribute
        obj1 = Mock()
        obj1.name = 'Object1'
        obj2 = Mock()
        obj2.name = 'Object2'

        state_schema = {'context_store': {'objects': [obj1, obj2]}, 'messages': []}
        result = node.execute(state_schema, {})

        assert result['names'] == ['Object1', 'Object2']


class TestTransformNodeValidation:
    """Test _validate_output, _check_required_fields, and type coercion."""

    def test_validate_output_with_required_fields(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test output validation with required fields present."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'mappings': [
                {'output_field': 'title', 'type': 'extract', 'source_path': 'title'},
                {'output_field': 'status', 'type': 'extract', 'source_path': 'status'},
            ],
            'output_schema': {
                'properties': {'title': {'type': 'string'}, 'status': {'type': 'string'}},
                'required': ['title'],
            },
        }

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {'context_store': {'title': 'Test', 'status': 'open'}, 'messages': []}
        result = node.execute(state_schema, {})

        assert result['title'] == 'Test'
        assert result['status'] == 'open'

    def test_validate_output_missing_required_field_raises_error(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test output validation fails when required field is missing."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'mappings': [{'output_field': 'status', 'type': 'extract', 'source_path': 'status'}],
            'output_schema': {
                'properties': {'title': {'type': 'string'}, 'status': {'type': 'string'}},
                'required': ['title'],
            },
        }

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {'context_store': {'status': 'open'}, 'messages': []}

        with pytest.raises(TransformationError, match="Required field missing: title"):
            node.execute(state_schema, {})

    def test_coerce_string_type(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test type coercion to string."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'mappings': [{'output_field': 'count', 'type': 'extract', 'source_path': 'count'}],
            'output_schema': {'properties': {'count': {'type': 'string'}}},
        }

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {'context_store': {'count': 123}, 'messages': []}
        result = node.execute(state_schema, {})

        assert result['count'] == '123'
        assert isinstance(result['count'], str)

    def test_coerce_integer_type(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test type coercion to integer."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'mappings': [{'output_field': 'count', 'type': 'extract', 'source_path': 'count'}],
            'output_schema': {'properties': {'count': {'type': 'integer'}}},
        }

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {'context_store': {'count': '42'}, 'messages': []}
        result = node.execute(state_schema, {})

        assert result['count'] == 42
        assert isinstance(result['count'], int)

    def test_coerce_boolean_type(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test type coercion to boolean."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'mappings': [{'output_field': 'active', 'type': 'extract', 'source_path': 'active'}],
            'output_schema': {'properties': {'active': {'type': 'boolean'}}},
        }

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {'context_store': {'active': 1}, 'messages': []}
        result = node.execute(state_schema, {})

        assert result['active'] is True
        assert isinstance(result['active'], bool)

    def test_coerce_number_type(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test type coercion to float."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'mappings': [{'output_field': 'price', 'type': 'extract', 'source_path': 'price'}],
            'output_schema': {'properties': {'price': {'type': 'number'}}},
        }

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {'context_store': {'price': '19.99'}, 'messages': []}
        result = node.execute(state_schema, {})

        assert result['price'] == 19.99
        assert isinstance(result['price'], float)


class TestTransformNodeErrorHandling:
    """Test error handling scenarios."""

    def test_error_strategy_fail_raises_exception(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test on_error='fail' raises TransformationError."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'mappings': [{'output_field': 'result', 'type': 'script', 'script': '1 / 0'}],
            'on_error': 'fail',
        }

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {'context_store': {}, 'messages': []}

        with pytest.raises(TransformationError):
            node.execute(state_schema, {})

    def test_error_strategy_skip_returns_empty_dict(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test on_error='skip' returns empty dict."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'mappings': [{'output_field': 'result', 'type': 'script', 'script': '1 / 0'}],
            'on_error': 'skip',
        }

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {'context_store': {}, 'messages': []}
        result = node.execute(state_schema, {})

        assert result == {}

    def test_error_strategy_default_returns_default_output(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test on_error='default' returns default_output."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'mappings': [{'output_field': 'result', 'type': 'script', 'script': '1 / 0'}],
            'on_error': 'default',
            'default_output': {'status': 'error', 'message': 'Default output'},
        }

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {'context_store': {}, 'messages': []}
        result = node.execute(state_schema, {})

        assert result == {'status': 'error', 'message': 'Default output'}

    def test_safe_eval_blocks_dangerous_operations(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test _safe_eval blocks dangerous operations."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'mappings': [{'output_field': 'result', 'type': 'script', 'script': '__import__("os").system("ls")'}],
        }

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {'context_store': {}, 'messages': []}

        with pytest.raises(TransformationError, match="potentially dangerous construct"):
            node.execute(state_schema, {})

    def test_template_syntax_error_raises_transformation_error(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test template with syntax error raises TransformationError."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'mappings': [
                {'output_field': 'result', 'type': 'template', 'template': '{% if %}missing condition{% endif %}'}
            ],
        }

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {'context_store': {}, 'messages': []}

        with pytest.raises(TransformationError, match="Template syntax error"):
            node.execute(state_schema, {})

    def test_unknown_mapping_type_raises_error(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test unknown mapping type raises TransformationError."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'mappings': [{'output_field': 'result', 'type': 'unknown_type'}],
        }

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {'context_store': {}, 'messages': []}

        with pytest.raises(TransformationError, match="Unknown mapping type"):
            node.execute(state_schema, {})


class TestTransformNodeExtractFromContextStore:
    """Test _extract_from_context_store method with nested key extraction."""

    def test_extract_with_simple_key_dict_merged(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        basic_custom_node,
    ):
        """Test extracting with simple key (no dots) - dict is merged."""
        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=basic_custom_node,
        )

        state_schema = {
            'context_store': {'rules': {'type': 'standard', 'count': 5}},
            'messages': [],
        }
        result = node._extract_from_context_store(state_schema, 'rules', {})

        # Simple key (no dots) - dict is merged
        assert result == {'type': 'standard', 'count': 5}

    def test_extract_with_nested_key_dict_wrapped(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        basic_custom_node,
    ):
        """Test extracting with nested key (with dots) - dict is wrapped with last part."""
        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=basic_custom_node,
        )

        state_schema = {
            'context_store': {'stats': {'total': {'count': 42, 'sum': 1050, 'average': 25.0}}},
            'messages': [],
        }
        result = node._extract_from_context_store(state_schema, 'stats.total', {})

        # Nested key (with dots) - dict is wrapped with 'total'
        assert result == {'total': {'count': 42, 'sum': 1050, 'average': 25.0}}
        assert 'total' in result
        assert isinstance(result['total'], dict)

    def test_extract_with_nested_key_uses_last_part_as_wrapper(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        basic_custom_node,
    ):
        """Test extracting with nested key uses last part of path as wrapper key for arrays."""
        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=basic_custom_node,
        )

        state_schema = {
            'context_store': {
                'standard_rules': {'headings': [{'heading': 'H1', 'level': 1}, {'heading': 'H2', 'level': 2}]}
            },
            'messages': [],
        }
        result = node._extract_from_context_store(state_schema, 'standard_rules.headings', {})

        # The key should be 'headings' (last part of path), not 'data'
        assert 'headings' in result
        assert result['headings'] == [{'heading': 'H1', 'level': 1}, {'heading': 'H2', 'level': 2}]
        assert 'data' not in result

    def test_extract_with_deeply_nested_key_array(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        basic_custom_node,
    ):
        """Test extracting deeply nested array uses last part as wrapper."""
        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=basic_custom_node,
        )

        state_schema = {
            'context_store': {'config': {'validation': {'rules': {'items': ['rule1', 'rule2', 'rule3']}}}},
            'messages': [],
        }
        result = node._extract_from_context_store(state_schema, 'config.validation.rules.items', {})

        # The key should be 'items' (last part of path)
        assert 'items' in result
        assert result['items'] == ['rule1', 'rule2', 'rule3']

    def test_extract_with_deeply_nested_key_dict(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        basic_custom_node,
    ):
        """Test extracting deeply nested dict uses last part as wrapper."""
        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=basic_custom_node,
        )

        state_schema = {
            'context_store': {'config': {'app': {'settings': {'debug': True, 'port': 8080, 'timeout': 30}}}},
            'messages': [],
        }
        result = node._extract_from_context_store(state_schema, 'config.app.settings', {})

        # The key should be 'settings' (last part of path), dict is wrapped
        assert result == {'settings': {'debug': True, 'port': 8080, 'timeout': 30}}
        assert 'settings' in result
        assert isinstance(result['settings'], dict)

    def test_extract_with_no_input_key_returns_full_context_store(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        basic_custom_node,
    ):
        """Test extracting without input_key returns entire context store."""
        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=basic_custom_node,
        )

        state_schema = {
            'context_store': {'key1': 'value1', 'key2': 'value2'},
            'messages': [],
        }
        result = node._extract_from_context_store(state_schema, None, {})

        assert result == {'key1': 'value1', 'key2': 'value2'}

    def test_extract_with_nonexistent_key_returns_empty(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        basic_custom_node,
    ):
        """Test extracting with non-existent key returns empty source_data."""
        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=basic_custom_node,
        )

        state_schema = {
            'context_store': {'existing_key': 'value'},
            'messages': [],
        }
        result = node._extract_from_context_store(state_schema, 'nonexistent.key', {})

        assert result == {}

    def test_extract_with_array_index_nested_path(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        basic_custom_node,
    ):
        """Test extracting array element with nested path using positive index."""
        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=basic_custom_node,
        )

        state_schema = {
            'context_store': {'api_response': {'users': [{'name': 'John', 'age': 30}, {'name': 'Jane', 'age': 25}]}},
            'messages': [],
        }
        result = node._extract_from_context_store(state_schema, 'api_response.users[0]', {})

        # Should wrap with 'users' key (array name)
        assert 'users' in result
        assert result['users'] == {'name': 'John', 'age': 30}

    def test_extract_with_array_index_negative(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        basic_custom_node,
    ):
        """Test extracting array element with negative index."""
        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=basic_custom_node,
        )

        state_schema = {
            'context_store': {'api_response': {'users': [{'name': 'John', 'age': 30}, {'name': 'Jane', 'age': 25}]}},
            'messages': [],
        }
        result = node._extract_from_context_store(state_schema, 'api_response.users[-1]', {})

        # Should get last element
        assert 'users' in result
        assert result['users'] == {'name': 'Jane', 'age': 25}

    def test_extract_with_array_index_simple_key(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        basic_custom_node,
    ):
        """Test extracting array element with simple key (no dots)."""
        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=basic_custom_node,
        )

        state_schema = {
            'context_store': {'users': [{'id': 1}, {'id': 2}, {'id': 3}]},
            'messages': [],
        }
        result = node._extract_from_context_store(state_schema, 'users[1]', {})

        # Should wrap with 'users' key
        assert 'users' in result
        assert result['users'] == {'id': 2}

    def test_extract_with_array_slice(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        basic_custom_node,
    ):
        """Test extracting array slice."""
        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=basic_custom_node,
        )

        state_schema = {
            'context_store': {'items': ['a', 'b', 'c', 'd', 'e']},
            'messages': [],
        }
        result = node._extract_from_context_store(state_schema, 'items[1:4]', {})

        # Should get slice [1:4] = ['b', 'c', 'd']
        assert 'items' in result
        assert result['items'] == ['b', 'c', 'd']

    def test_extract_with_array_index_out_of_bounds(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        basic_custom_node,
    ):
        """Test extracting with out of bounds index returns empty dict."""
        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=basic_custom_node,
        )

        state_schema = {
            'context_store': {'users': [{'id': 1}, {'id': 2}]},
            'messages': [],
        }
        result = node._extract_from_context_store(state_schema, 'users[10]', {})

        # Should return empty dict and log warning
        assert result == {}

    def test_extract_with_array_index_on_non_array(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        basic_custom_node,
    ):
        """Test extracting with array index on non-array type returns empty dict."""
        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=basic_custom_node,
        )

        state_schema = {
            'context_store': {'config': {'debug': True}},
            'messages': [],
        }
        result = node._extract_from_context_store(state_schema, 'config[0]', {})

        # Should return empty dict and log warning
        assert result == {}


class TestTransformNodeParseArrayIndex:
    """Test _parse_array_index method for parsing array indexing syntax."""

    def test_parse_single_index_positive(
        self,
    ):
        """Test parsing single positive index."""
        base_path, index, remaining = parse_array_index('users[0]')
        assert base_path == 'users'
        assert index == 0
        assert remaining is None

    def test_parse_single_index_negative(
        self,
    ):
        """Test parsing single negative index."""
        base_path, index, remaining = parse_array_index('items[-1]')
        assert base_path == 'items'
        assert index == -1
        assert remaining is None

    def test_parse_slice_syntax(
        self,
    ):
        """Test parsing slice syntax."""
        base_path, index, remaining = parse_array_index('data[1:5]')
        assert base_path == 'data'
        assert isinstance(index, slice)
        assert index.start == 1
        assert index.stop == 5
        assert index.step is None
        assert remaining is None

    def test_parse_slice_with_step(
        self,
    ):
        """Test parsing slice with step."""
        base_path, index, remaining = parse_array_index('items[0:10:2]')
        assert base_path == 'items'
        assert isinstance(index, slice)
        assert index.start == 0
        assert index.stop == 10
        assert index.step == 2
        assert remaining is None

    def test_parse_nested_path_with_index(
        self,
    ):
        """Test parsing nested path with array index."""
        base_path, index, remaining = parse_array_index('api.response.users[0]')
        assert base_path == 'api.response.users'
        assert index == 0
        assert remaining is None

    def test_parse_no_index(
        self,
    ):
        """Test parsing key without index returns None."""
        base_path, index, remaining = parse_array_index('users')
        assert base_path == 'users'
        assert index is None
        assert remaining is None


class TestTransformNodeArrayIndexingEndToEnd:
    """End-to-end tests for array indexing with full transformation pipeline."""

    def test_single_index_with_mappings(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test extracting single array element and applying mappings."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'input_key': 'api_response.users[0]',
            'mappings': [
                {
                    'output_field': 'full_name',
                    'type': 'script',
                    'script': "f\"{users.get('first_name', '')} {users.get('last_name', '')}\"",
                },
                {'output_field': 'email_lower', 'type': 'script', 'script': "users.get('email', '').lower()"},
                {
                    'output_field': 'is_active',
                    'type': 'condition',
                    'condition': "users.get('status') == 'active'",
                    'then_value': True,
                    'else_value': False,
                },
            ],
        }

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {
            'context_store': {
                'api_response': {
                    'users': [
                        {'first_name': 'John', 'last_name': 'Doe', 'email': 'JOHN.DOE@example.com', 'status': 'active'},
                        {
                            'first_name': 'Jane',
                            'last_name': 'Smith',
                            'email': 'jane.smith@example.com',
                            'status': 'inactive',
                        },
                    ]
                }
            },
            'messages': [],
        }

        result = node.execute(state_schema, {})

        assert result['full_name'] == 'John Doe'
        assert result['email_lower'] == 'john.doe@example.com'
        assert result['is_active'] is True

    def test_negative_index_with_mappings(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test extracting last array element with negative index."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'input_key': 'events[-1]',
            'mappings': [
                {'output_field': 'event_type', 'type': 'extract', 'source_path': 'events.type'},
                {'output_field': 'event_id', 'type': 'extract', 'source_path': 'events.id'},
                {
                    'output_field': 'summary',
                    'type': 'template',
                    'template': 'Latest event: {{ event_type }} ({{ event_id }})',
                },
            ],
        }

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {
            'context_store': {
                'events': [
                    {'id': 'evt-1', 'type': 'login', 'timestamp': '2024-01-01'},
                    {'id': 'evt-2', 'type': 'purchase', 'timestamp': '2024-01-02'},
                    {'id': 'evt-3', 'type': 'logout', 'timestamp': '2024-01-03'},
                ]
            },
            'messages': [],
        }

        result = node.execute(state_schema, {})

        assert result['event_type'] == 'logout'
        assert result['event_id'] == 'evt-3'
        assert result['summary'] == 'Latest event: logout (evt-3)'

    def test_slice_with_array_map(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test extracting array slice and processing with array_map."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'input_key': 'items[1:4]',
            'mappings': [
                {'output_field': 'item_ids', 'type': 'array_map', 'source_path': 'items', 'item_field': 'id'},
                {'output_field': 'item_count', 'type': 'script', 'script': 'len(items)'},
                {
                    'output_field': 'active_items',
                    'type': 'array_map',
                    'source_path': 'items',
                    'item_field': 'id',
                    'filter_condition': "item.get('status') == 'active'",
                },
            ],
        }

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {
            'context_store': {
                'items': [
                    {'id': 'item-1', 'status': 'pending'},
                    {'id': 'item-2', 'status': 'active'},
                    {'id': 'item-3', 'status': 'inactive'},
                    {'id': 'item-4', 'status': 'active'},
                    {'id': 'item-5', 'status': 'pending'},
                ]
            },
            'messages': [],
        }

        result = node.execute(state_schema, {})

        # Slice [1:4] should get items 2, 3, 4
        assert result['item_ids'] == ['item-2', 'item-3', 'item-4']
        assert result['item_count'] == 3
        assert result['active_items'] == ['item-2', 'item-4']

    def test_nested_path_with_index(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test deeply nested path with array indexing."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'input_key': 'response.data.results[0]',
            'mappings': [
                {'output_field': 'result_name', 'type': 'extract', 'source_path': 'results.name'},
                {'output_field': 'result_score', 'type': 'extract', 'source_path': 'results.score'},
                {
                    'output_field': 'is_passing',
                    'type': 'condition',
                    'condition': 'result_score >= 70',
                    'then_value': True,
                    'else_value': False,
                },
            ],
        }

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {
            'context_store': {
                'response': {
                    'data': {
                        'results': [
                            {'name': 'Test A', 'score': 85},
                            {'name': 'Test B', 'score': 60},
                            {'name': 'Test C', 'score': 95},
                        ]
                    }
                }
            },
            'messages': [],
        }

        result = node.execute(state_schema, {})

        assert result['result_name'] == 'Test A'
        assert result['result_score'] == 85
        assert result['is_passing'] is True

    def test_simple_key_with_index(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test simple key (no dots) with array indexing."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'input_key': 'users[1]',
            'mappings': [
                {'output_field': 'user_id', 'type': 'extract', 'source_path': 'users.id'},
                {'output_field': 'user_name', 'type': 'extract', 'source_path': 'users.name'},
            ],
        }

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {
            'context_store': {
                'users': [{'id': 1, 'name': 'Alice'}, {'id': 2, 'name': 'Bob'}, {'id': 3, 'name': 'Charlie'}]
            },
            'messages': [],
        }

        result = node.execute(state_schema, {})

        assert result['user_id'] == 2
        assert result['user_name'] == 'Bob'

    def test_slice_with_step(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test array slice with step parameter."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'input_key': 'numbers[0:10:2]',
            'mappings': [
                {'output_field': 'even_numbers', 'type': 'extract', 'source_path': 'numbers'},
                {'output_field': 'sum', 'type': 'script', 'script': 'sum(numbers)'},
                {'output_field': 'count', 'type': 'script', 'script': 'len(numbers)'},
            ],
        }

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {
            'context_store': {'numbers': [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]},
            'messages': [],
        }

        result = node.execute(state_schema, {})

        # [0:10:2] should get [0, 2, 4, 6, 8]
        assert result['even_numbers'] == [0, 2, 4, 6, 8]
        assert result['sum'] == 20
        assert result['count'] == 5

    def test_index_with_output_schema_validation(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test array indexing with output schema validation."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'input_key': 'products[0]',
            'mappings': [
                {'output_field': 'product_name', 'type': 'extract', 'source_path': 'products.name'},
                {'output_field': 'product_price', 'type': 'extract', 'source_path': 'products.price'},
                {'output_field': 'in_stock', 'type': 'extract', 'source_path': 'products.in_stock'},
            ],
            'output_schema': {
                'type': 'object',
                'properties': {
                    'product_name': {'type': 'string'},
                    'product_price': {'type': 'number'},
                    'in_stock': {'type': 'boolean'},
                },
                'required': ['product_name', 'product_price'],
            },
        }

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {
            'context_store': {
                'products': [
                    {'name': 'Laptop', 'price': 999.99, 'in_stock': True},
                    {'name': 'Mouse', 'price': 29.99, 'in_stock': False},
                ]
            },
            'messages': [],
        }

        result = node.execute(state_schema, {})

        assert result['product_name'] == 'Laptop'
        assert result['product_price'] == 999.99
        assert result['in_stock'] is True

    def test_complex_etl_pipeline_with_indexing(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test complex ETL pipeline: extract first user, transform, validate."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'input_key': 'api.response.users[0]',
            'mappings': [
                # Extract and clean name
                {'output_field': 'first_name', 'type': 'script', 'script': "users.get('first_name', '').strip()"},
                {'output_field': 'last_name', 'type': 'script', 'script': "users.get('last_name', '').strip()"},
                {'output_field': 'full_name', 'type': 'script', 'script': "f'{first_name} {last_name}'"},
                # Clean and normalize email
                {'output_field': 'email', 'type': 'script', 'script': "users.get('email', '').lower().strip()"},
                # Extract tags and count
                {'output_field': 'tags', 'type': 'extract', 'source_path': 'users.tags', 'default': []},
                {'output_field': 'tag_count', 'type': 'script', 'script': 'len(tags)'},
                # Determine user level
                {
                    'output_field': 'level',
                    'type': 'condition',
                    'condition': "tag_count >= 3",
                    'then_value': 'premium',
                    'else_value': 'standard',
                },
                # Create summary
                {
                    'output_field': 'summary',
                    'type': 'template',
                    'template': '{{ full_name }} ({{ email }}) - {{ level }} user with {{ tag_count }} tags',
                },
            ],
            'output_schema': {
                'type': 'object',
                'properties': {
                    'full_name': {'type': 'string'},
                    'email': {'type': 'string'},
                    'level': {'type': 'string'},
                    'tag_count': {'type': 'integer'},
                },
                'required': ['full_name', 'email', 'level'],
            },
        }

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {
            'context_store': {
                'api': {
                    'response': {
                        'users': [
                            {
                                'first_name': '  John  ',
                                'last_name': ' Doe ',
                                'email': '  JOHN.DOE@EXAMPLE.COM  ',
                                'tags': ['vip', 'early-adopter', 'beta-tester', 'contributor'],
                            },
                            {'first_name': 'Jane', 'last_name': 'Smith', 'email': 'jane@example.com', 'tags': ['user']},
                        ]
                    }
                }
            },
            'messages': [],
        }

        result = node.execute(state_schema, {})

        assert result['full_name'] == 'John Doe'
        assert result['email'] == 'john.doe@example.com'
        assert result['level'] == 'premium'
        assert result['tag_count'] == 4
        assert result['summary'] == 'John Doe (john.doe@example.com) - premium user with 4 tags'


class TestTransformNodeAddIterationState:
    """Test _add_iteration_state method for TransformNode."""

    def test_add_iteration_state_preserves_task_key(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        basic_custom_node,
    ):
        """Test that _add_iteration_state preserves task key for iteration continuation."""
        # Setup workflow state with iteration configuration
        workflow_state = Mock(spec=WorkflowState)
        workflow_state.task = "Transform task"
        workflow_state.next = Mock()
        workflow_state.next.iter_key = 'iteration_items'
        workflow_state.next.override_task = False

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=workflow_state,
            custom_node=basic_custom_node,
        )

        # State schema with task key
        state_schema = {
            'task': 'original task content',
            'iteration_node_number': 2,
            'total_iterations': 5,
            'messages': [],
        }

        final_state = {}
        processed_output = '{"transformed": "data"}'

        node._add_iteration_state(final_state, state_schema, processed_output)

        # Check that iteration state is set
        assert 'iteration_source' in final_state
        assert final_state['iteration_source'] == processed_output
        assert final_state['iteration_node_number'] == 2
        assert final_state['total_iterations'] == 5

        # Check that task is preserved in iter_key (not overridden)
        assert final_state['iteration_items'] == 'original task content'

    def test_add_iteration_state_with_override_task(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        basic_custom_node,
    ):
        """Test that _add_iteration_state uses processed_output when override_task is True."""
        workflow_state = Mock(spec=WorkflowState)
        workflow_state.task = "Transform task"
        workflow_state.next = Mock()
        workflow_state.next.iter_key = 'iteration_items'
        workflow_state.next.override_task = True

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=workflow_state,
            custom_node=basic_custom_node,
        )

        state_schema = {
            'task': 'original task content',
            'iteration_node_number': 1,
            'total_iterations': 3,
            'messages': [],
        }

        final_state = {}
        processed_output = '{"new": "output"}'

        node._add_iteration_state(final_state, state_schema, processed_output)

        # Check that processed_output is used instead of task
        assert final_state['iteration_items'] == processed_output

    def test_add_iteration_state_without_iter_key_does_nothing(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        basic_custom_node,
    ):
        """Test that _add_iteration_state does nothing when iter_key is not set."""
        workflow_state = Mock(spec=WorkflowState)
        workflow_state.task = "Transform task"
        workflow_state.next = Mock()
        workflow_state.next.iter_key = None

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=workflow_state,
            custom_node=basic_custom_node,
        )

        state_schema = {
            'task': 'original task content',
            'messages': [],
        }

        final_state = {}
        processed_output = '{"output": "data"}'

        node._add_iteration_state(final_state, state_schema, processed_output)

        # Final state should be empty since no iter_key is set
        assert final_state == {}

    def test_add_iteration_state_without_task_key_in_schema(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        basic_custom_node,
    ):
        """Test that _add_iteration_state handles missing task key gracefully."""
        workflow_state = Mock(spec=WorkflowState)
        workflow_state.task = "Transform task"
        workflow_state.next = Mock()
        workflow_state.next.iter_key = 'iteration_items'
        workflow_state.next.override_task = False

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=workflow_state,
            custom_node=basic_custom_node,
        )

        # State schema without task key
        state_schema = {
            'iteration_node_number': 1,
            'total_iterations': 3,
            'messages': [],
        }

        final_state = {}
        processed_output = '{"output": "data"}'

        node._add_iteration_state(final_state, state_schema, processed_output)

        # Should still set iteration_source and iteration metadata
        assert final_state['iteration_source'] == processed_output
        assert final_state['iteration_node_number'] == 1
        assert final_state['total_iterations'] == 3

        # iter_key should not be set since task is not in state_schema
        assert 'iteration_items' not in final_state

    def test_add_iteration_state_without_workflow_state(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        basic_custom_node,
    ):
        """Test that _add_iteration_state returns early when workflow_state is None."""
        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=None,
            custom_node=basic_custom_node,
        )

        state_schema = {
            'task': 'original task content',
            'messages': [],
        }

        final_state = {}
        processed_output = '{"output": "data"}'

        node._add_iteration_state(final_state, state_schema, processed_output)

        # Final state should remain empty
        assert final_state == {}


class TestTransformNodeHelperMethods:
    """Test helper methods like get_task and post_process_output."""

    def test_get_task_returns_workflow_state_task(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        basic_custom_node,
    ):
        """Test get_task returns the workflow state task."""
        mock_workflow_state.task = "Custom transformation task"

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=basic_custom_node,
        )

        state_schema = Mock(spec=AgentMessages)
        task = node.get_task(state_schema)

        assert task == "Custom transformation task"

    def test_get_task_returns_default_when_no_task(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        basic_custom_node,
    ):
        """Test get_task returns default message when workflow_state.task is None."""
        mock_workflow_state.task = None

        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=basic_custom_node,
        )

        state_schema = Mock(spec=AgentMessages)
        task = node.get_task(state_schema)

        assert task == "Transform data using configured mappings"

    def test_post_process_output_returns_json_string(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        basic_custom_node,
    ):
        """Test post_process_output converts dict to JSON string."""
        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=basic_custom_node,
        )

        output = {'status': 'success', 'count': 42, 'items': ['a', 'b', 'c']}
        result = node.post_process_output(AgentMessages, "task", output)

        assert isinstance(result, str)
        parsed = json.loads(result)
        assert parsed == output


TEMPLATE_SSTI_PAYLOADS = [
    # MRO traversal to reach Python internals
    "{{ ''.__class__.__mro__[1].__subclasses__() }}",
    # __import__ direct call
    "{{ __import__('os').popen('id').read() }}",
    # File read via open()
    "{{ open('/etc/passwd').read() }}",
    # __globals__ access via class chain
    "{{ config.__class__.__init__.__globals__ }}",
]


class TestTransformNodeTemplateSecurity:
    """Security regression tests for EPMCDME-10987.

    Before the fix, _render_template() used jinja2.Template directly (unsandboxed),
    allowing arbitrary OS command execution via Jinja2 Server-Side Template Injection.
    After the fix, render_secure_template() with RestrictedSandboxEnvironment is used.
    """

    def test_exact_rce_exploit_from_ticket_is_blocked(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Exact RCE payload from EPMCDME-10987 must raise TransformationError, not execute."""
        exploit_template = "Hello {{self._init.globals.builtins.import_('os').popen('whoami').read()}}"
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-rce-node"
        custom_node.config = {
            'input_source': 'context_store',
            'mappings': [{'output_field': 'result', 'type': 'template', 'template': exploit_template}],
        }
        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {'context_store': {}, 'messages': []}

        with pytest.raises(TransformationError, match="Template security violation"):
            node.execute(state_schema, {})

    @pytest.mark.parametrize("malicious_template", TEMPLATE_SSTI_PAYLOADS)
    def test_ssti_payloads_raise_transformation_error(
        self,
        malicious_template,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Known SSTI attack payloads must raise TransformationError, not execute code."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-ssti-node"
        custom_node.config = {
            'input_source': 'context_store',
            'mappings': [{'output_field': 'result', 'type': 'template', 'template': malicious_template}],
        }
        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {'context_store': {}, 'messages': []}

        with pytest.raises(TransformationError, match="Template security violation"):
            node.execute(state_schema, {})

    def test_template_does_not_html_escape_ampersand(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Template output must not HTML-escape '&' — Transform node output is not HTML."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-autoescape-node"
        custom_node.config = {
            'input_source': 'context_store',
            'mappings': [{'output_field': 'result', 'type': 'template', 'template': '{{ status }}'}],
        }
        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {'context_store': {'status': 'open & running'}, 'messages': []}
        result = node.execute(state_schema, {})

        assert result['result'] == 'open & running'
        assert '&amp;' not in result['result']

    def test_template_does_not_html_escape_angle_brackets(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Template output must not HTML-escape '<' or '>' — Transform node output is not HTML."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-autoescape-angle-node"
        custom_node.config = {
            'input_source': 'context_store',
            'mappings': [{'output_field': 'result', 'type': 'template', 'template': '{{ description }}'}],
        }
        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {'context_store': {'description': 'score <10 or >90'}, 'messages': []}
        result = node.execute(state_schema, {})

        assert result['result'] == 'score <10 or >90'
        assert '&lt;' not in result['result']
        assert '&gt;' not in result['result']

    def test_legitimate_template_renders_correctly_in_sandbox(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Sandboxed rendering must not break legitimate template variable substitution."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-legit-template-node"
        custom_node.config = {
            'input_source': 'context_store',
            'mappings': [
                {
                    'output_field': 'greeting',
                    'type': 'template',
                    'template': 'Hello {{ name }}, your status is {{ status }}',
                }
            ],
        }
        node = TransformNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            custom_node=custom_node,
        )

        state_schema = {'context_store': {'name': 'Alice', 'status': 'active'}, 'messages': []}
        result = node.execute(state_schema, {})

        assert result == {'greeting': 'Hello Alice, your status is active'}
