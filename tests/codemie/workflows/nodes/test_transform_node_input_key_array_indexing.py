# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""Tests for TransformNode array indexing with chaining support."""

from unittest.mock import Mock

import pytest

from codemie.core.workflow_models import CustomWorkflowNode, WorkflowState
from codemie.service.workflow_execution import WorkflowExecutionService
from codemie.workflows.callbacks.base_callback import BaseCallback
from codemie.workflows.nodes.transform_node import TransformNode
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


class TestParseArrayIndexWithChaining:
    """Test parse_array_index utility function with chaining support."""

    def test_parse_single_index_positive(self):
        """Test parsing single positive index without chaining."""
        base_path, index, remaining = parse_array_index('users[0]')
        assert base_path == 'users'
        assert index == 0
        assert remaining is None

    def test_parse_single_index_negative(self):
        """Test parsing single negative index without chaining."""
        base_path, index, remaining = parse_array_index('items[-1]')
        assert base_path == 'items'
        assert index == -1
        assert remaining is None

    def test_parse_slice_syntax(self):
        """Test parsing slice syntax without chaining."""
        base_path, index, remaining = parse_array_index('data[1:5]')
        assert base_path == 'data'
        assert isinstance(index, slice)
        assert index.start == 1
        assert index.stop == 5
        assert index.step is None
        assert remaining is None

    def test_parse_slice_with_step(self):
        """Test parsing slice with step without chaining."""
        base_path, index, remaining = parse_array_index('items[0:10:2]')
        assert base_path == 'items'
        assert isinstance(index, slice)
        assert index.start == 0
        assert index.stop == 10
        assert index.step == 2
        assert remaining is None

    def test_parse_nested_path_with_index(self):
        """Test parsing nested path with array index (no chaining after index)."""
        base_path, index, remaining = parse_array_index('api.response.users[0]')
        assert base_path == 'api.response.users'
        assert index == 0
        assert remaining is None

    def test_parse_no_index(self):
        """Test parsing key without index returns None for index and remaining."""
        base_path, index, remaining = parse_array_index('users')
        assert base_path == 'users'
        assert index is None
        assert remaining is None

    # New tests for chained array indexing
    def test_parse_chained_single_field(self):
        """Test parsing array index with single chained field."""
        base_path, index, remaining = parse_array_index('users[0].first_name')
        assert base_path == 'users'
        assert index == 0
        assert remaining == 'first_name'

    def test_parse_chained_nested_path(self):
        """Test parsing array index with nested chained path."""
        base_path, index, remaining = parse_array_index('api.users[0].profile.email')
        assert base_path == 'api.users'
        assert index == 0
        assert remaining == 'profile.email'

    def test_parse_chained_deeply_nested(self):
        """Test parsing array index with deeply nested chained path."""
        base_path, index, remaining = parse_array_index('response.data.items[2].meta.info.status')
        assert base_path == 'response.data.items'
        assert index == 2
        assert remaining == 'meta.info.status'

    def test_parse_chained_negative_index(self):
        """Test parsing negative index with chained path."""
        base_path, index, remaining = parse_array_index('events[-1].timestamp')
        assert base_path == 'events'
        assert index == -1
        assert remaining == 'timestamp'

    def test_parse_chained_slice_with_path(self):
        """Test parsing slice with chained path."""
        base_path, index, remaining = parse_array_index('items[0:5].status')
        assert base_path == 'items'
        assert isinstance(index, slice)
        assert index.start == 0
        assert index.stop == 5
        assert remaining == 'status'

    def test_parse_simple_key_chained(self):
        """Test parsing simple key (no dots before index) with chained path."""
        base_path, index, remaining = parse_array_index('users[1].name')
        assert base_path == 'users'
        assert index == 1
        assert remaining == 'name'


class TestTransformNodeChainedArrayIndexing:
    """Test TransformNode with chained array indexing in input_key."""

    def test_chained_single_field_extraction(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test extracting single field from array element using chained syntax."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'input_key': 'users[0].first_name',
            'mappings': [
                {'output_field': 'name', 'type': 'extract', 'source_path': 'first_name'},
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
                'users': [
                    {'first_name': 'John', 'last_name': 'Doe'},
                    {'first_name': 'Jane', 'last_name': 'Smith'},
                ]
            },
            'messages': [],
        }

        result = node.execute(state_schema, {})

        # Should extract 'John' and wrap it with key 'first_name'
        assert result['name'] == 'John'

    def test_chained_nested_field_extraction(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test extracting nested field from array element using chained syntax."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'input_key': 'api.users[0].profile.email',
            'mappings': [
                {'output_field': 'user_email', 'type': 'extract', 'source_path': 'email'},
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
                'api': {
                    'users': [
                        {'profile': {'email': 'john@example.com', 'phone': '555-1234'}},
                        {'profile': {'email': 'jane@example.com', 'phone': '555-5678'}},
                    ]
                }
            },
            'messages': [],
        }

        result = node.execute(state_schema, {})

        # Should extract 'john@example.com' and wrap it with key 'email' (last part of chain)
        assert result['user_email'] == 'john@example.com'

    def test_chained_deeply_nested_extraction(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test extracting deeply nested field using chained array indexing."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'input_key': 'response.data.items[2].metadata.info.status',
            'mappings': [
                {'output_field': 'item_status', 'type': 'extract', 'source_path': 'status'},
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
                        'items': [
                            {'metadata': {'info': {'status': 'pending'}}},
                            {'metadata': {'info': {'status': 'processing'}}},
                            {'metadata': {'info': {'status': 'completed'}}},
                            {'metadata': {'info': {'status': 'failed'}}},
                        ]
                    }
                }
            },
            'messages': [],
        }

        result = node.execute(state_schema, {})

        assert result['item_status'] == 'completed'

    def test_chained_negative_index_extraction(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test chained extraction with negative array index."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'input_key': 'events[-1].timestamp',
            'mappings': [
                {'output_field': 'last_event_time', 'type': 'extract', 'source_path': 'timestamp'},
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
                    {'timestamp': '2024-01-01T10:00:00Z', 'type': 'login'},
                    {'timestamp': '2024-01-01T11:00:00Z', 'type': 'purchase'},
                    {'timestamp': '2024-01-01T12:00:00Z', 'type': 'logout'},
                ]
            },
            'messages': [],
        }

        result = node.execute(state_schema, {})

        assert result['last_event_time'] == '2024-01-01T12:00:00Z'

    def test_chained_with_mappings_transformation(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test chained extraction with multiple mappings and transformations."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'input_key': 'api.users[0].contact',
            'mappings': [
                {'output_field': 'email', 'type': 'extract', 'source_path': 'contact.email'},
                {'output_field': 'phone', 'type': 'extract', 'source_path': 'contact.phone'},
                {'output_field': 'email_lower', 'type': 'script', 'script': 'email.lower()'},
                {
                    'output_field': 'has_phone',
                    'type': 'condition',
                    'condition': 'phone is not None and len(phone) > 0',
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
                'api': {
                    'users': [
                        {'contact': {'email': 'JOHN@EXAMPLE.COM', 'phone': '555-1234'}},
                        {'contact': {'email': 'jane@example.com', 'phone': ''}},
                    ]
                }
            },
            'messages': [],
        }

        result = node.execute(state_schema, {})

        assert result['email'] == 'JOHN@EXAMPLE.COM'
        assert result['phone'] == '555-1234'
        assert result['email_lower'] == 'john@example.com'
        assert result['has_phone'] is True

    def test_chained_missing_field_returns_none(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test that chained extraction returns None for missing nested field."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'input_key': 'users[0].profile.bio',
            'mappings': [
                {'output_field': 'bio', 'type': 'extract', 'source_path': 'bio', 'default': 'No bio available'},
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
            'context_store': {'users': [{'profile': {'name': 'John'}}, {'profile': {'name': 'Jane'}}]},
            'messages': [],
        }

        result = node.execute(state_schema, {})

        # Should use default value when chained field doesn't exist
        assert result['bio'] == 'No bio available'

    def test_chained_simple_key_no_dots_before_index(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test chained extraction with simple key (no dots before index)."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'input_key': 'users[1].age',
            'mappings': [
                {'output_field': 'user_age', 'type': 'extract', 'source_path': 'age'},
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
                'users': [
                    {'name': 'Alice', 'age': 25},
                    {'name': 'Bob', 'age': 30},
                    {'name': 'Charlie', 'age': 35},
                ]
            },
            'messages': [],
        }

        result = node.execute(state_schema, {})

        assert result['user_age'] == 30


class TestTransformNodeChainedComplexScenarios:
    """Test complex scenarios combining chained array indexing with other features."""

    def test_chained_etl_pipeline(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test complex ETL pipeline using chained array indexing."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'input_key': 'api.response.data[0].user',
            'mappings': [
                # Extract user details
                {'output_field': 'first_name', 'type': 'extract', 'source_path': 'user.first_name'},
                {'output_field': 'last_name', 'type': 'extract', 'source_path': 'user.last_name'},
                {'output_field': 'email', 'type': 'extract', 'source_path': 'user.email'},
                # Transform
                {'output_field': 'full_name', 'type': 'script', 'script': "f'{first_name} {last_name}'"},
                {'output_field': 'email_lower', 'type': 'script', 'script': 'email.lower().strip()'},
                # Validate
                {
                    'output_field': 'is_valid_email',
                    'type': 'condition',
                    'condition': "'@' in email_lower and '.' in email_lower",
                    'then_value': True,
                    'else_value': False,
                },
                # Template
                {
                    'output_field': 'summary',
                    'type': 'template',
                    'template': 'User: {{ full_name }} ({{ email_lower }})',
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
                'api': {
                    'response': {
                        'data': [
                            {
                                'user': {
                                    'first_name': 'John',
                                    'last_name': 'Doe',
                                    'email': '  JOHN.DOE@EXAMPLE.COM  ',
                                }
                            },
                            {'user': {'first_name': 'Jane', 'last_name': 'Smith', 'email': 'jane@example.com'}},
                        ]
                    }
                }
            },
            'messages': [],
        }

        result = node.execute(state_schema, {})

        assert result['first_name'] == 'John'
        assert result['last_name'] == 'Doe'
        assert result['email'] == '  JOHN.DOE@EXAMPLE.COM  '
        assert result['full_name'] == 'John Doe'
        assert result['email_lower'] == 'john.doe@example.com'
        assert result['is_valid_email'] is True
        assert result['summary'] == 'User: John Doe (john.doe@example.com)'

    def test_chained_multiple_input_keys_comparison(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test extracting and comparing values from chained input_key."""
        # Note: This test uses input_key with chaining, then extracts from the result
        # Chained syntax in source_path is not currently supported
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'input_key': 'results[0]',
            'mappings': [
                {'output_field': 'first_score', 'type': 'extract', 'source_path': 'results.score'},
                {'output_field': 'first_name', 'type': 'extract', 'source_path': 'results.name'},
                {
                    'output_field': 'is_passing',
                    'type': 'condition',
                    'condition': 'first_score >= 70',
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
                'results': [
                    {'name': 'Test 1', 'score': 75},
                    {'name': 'Test 2', 'score': 80},
                    {'name': 'Test 3', 'score': 90},
                ]
            },
            'messages': [],
        }

        result = node.execute(state_schema, {})

        assert result['first_score'] == 75
        assert result['first_name'] == 'Test 1'
        assert result['is_passing'] is True

    def test_chained_with_array_map(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test chained extraction combined with array_map on the result."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'input_key': 'departments[0].employees',
            'mappings': [
                {
                    'output_field': 'employee_names',
                    'type': 'array_map',
                    'source_path': 'employees',
                    'item_field': 'name',
                },
                {'output_field': 'employee_count', 'type': 'script', 'script': 'len(employees)'},
                {
                    'output_field': 'active_employees',
                    'type': 'array_map',
                    'source_path': 'employees',
                    'item_field': 'name',
                    'filter_condition': "item.get('active', False)",
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
                'departments': [
                    {
                        'name': 'Engineering',
                        'employees': [
                            {'name': 'Alice', 'active': True},
                            {'name': 'Bob', 'active': False},
                            {'name': 'Charlie', 'active': True},
                        ],
                    },
                    {
                        'name': 'Sales',
                        'employees': [
                            {'name': 'David', 'active': True},
                        ],
                    },
                ]
            },
            'messages': [],
        }

        result = node.execute(state_schema, {})

        assert result['employee_names'] == ['Alice', 'Bob', 'Charlie']
        assert result['employee_count'] == 3
        assert result['active_employees'] == ['Alice', 'Charlie']


class TestTransformNodeMultipleChainedArrayIndices:
    """Test TransformNode with multiple chained array indices like users[0].social_media[0].url."""

    def test_double_array_index_simple(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test extraction with two consecutive array indices."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'input_key': 'users[0].social_media[0].profile_url',
            'mappings': [
                {'output_field': 'url', 'type': 'extract', 'source_path': 'profile_url'},
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
                'users': [
                    {
                        'name': 'John',
                        'social_media': [
                            {'platform': 'twitter', 'profile_url': 'twitter.com/john'},
                            {'platform': 'linkedin', 'profile_url': 'linkedin.com/john'},
                        ],
                    },
                    {
                        'name': 'Jane',
                        'social_media': [
                            {'platform': 'twitter', 'profile_url': 'twitter.com/jane'},
                        ],
                    },
                ]
            },
            'messages': [],
        }

        result = node.execute(state_schema, {})

        # Should extract the profile_url from users[0].social_media[0]
        assert result['url'] == 'twitter.com/john'

    def test_triple_array_index_nested(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test extraction with three consecutive array indices."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'input_key': 'companies[0].departments[1].teams[0].lead',
            'mappings': [
                {'output_field': 'team_lead', 'type': 'extract', 'source_path': 'lead'},
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
                'companies': [
                    {
                        'name': 'ACME Corp',
                        'departments': [
                            {'name': 'Sales', 'teams': [{'name': 'Team A', 'lead': 'Alice'}]},
                            {
                                'name': 'Engineering',
                                'teams': [
                                    {'name': 'Backend', 'lead': 'Bob'},
                                    {'name': 'Frontend', 'lead': 'Charlie'},
                                ],
                            },
                        ],
                    }
                ]
            },
            'messages': [],
        }

        result = node.execute(state_schema, {})

        # Should extract companies[0].departments[1].teams[0].lead
        assert result['team_lead'] == 'Bob'

    def test_user_specific_case_profile_contacts(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test the user's specific case: api_response.users[0].profile.contacts[0]."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'input_key': 'api_response.users[0].profile.contacts[0].email',
            'mappings': [
                {'output_field': 'contact_email', 'type': 'extract', 'source_path': 'email'},
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
                        {
                            'id': 1,
                            'first_name': 'John',
                            'last_name': 'Doe',
                            'status': 'active',
                            'profile': {
                                'contacts': [
                                    {'email': 'john@example.com', 'phone': '555-1234'},
                                    {'email': 'john.doe@work.com', 'phone': '555-5678'},
                                ]
                            },
                        },
                        {
                            'id': 2,
                            'first_name': 'Jane',
                            'last_name': 'Smith',
                            'status': 'inactive',
                            'profile': {
                                'contacts': [
                                    {'email': 'jane@example.com', 'phone': '555-9999'},
                                ]
                            },
                        },
                    ]
                }
            },
            'messages': [],
        }

        result = node.execute(state_schema, {})

        # Should extract the email from users[0].profile.contacts[0]
        assert result['contact_email'] == 'john@example.com'

    def test_user_specific_case_without_final_field(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test extracting entire contact object without specifying final field."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'input_key': 'api_response.users[0].profile.contacts[0]',
            'mappings': [
                {'output_field': 'email', 'type': 'extract', 'source_path': 'contacts.email'},
                {'output_field': 'phone', 'type': 'extract', 'source_path': 'contacts.phone'},
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
                        {
                            'id': 1,
                            'first_name': 'John',
                            'last_name': 'Doe',
                            'status': 'active',
                            'profile': {
                                'contacts': [
                                    {'email': 'john@example.com', 'phone': '555-1234'},
                                ]
                            },
                        }
                    ]
                }
            },
            'messages': [],
        }

        result = node.execute(state_schema, {})

        # Should extract the entire contact object and wrap it with 'contacts' key
        assert result['email'] == 'john@example.com'
        assert result['phone'] == '555-1234'

    def test_multiple_indices_with_negative_index(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test multiple array indices with negative indexing."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'input_key': 'users[-1].orders[-1].item',
            'mappings': [
                {'output_field': 'last_item', 'type': 'extract', 'source_path': 'item'},
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
                'users': [
                    {'name': 'Alice', 'orders': [{'item': 'laptop'}, {'item': 'mouse'}]},
                    {'name': 'Bob', 'orders': [{'item': 'keyboard'}, {'item': 'monitor'}, {'item': 'mouse'}]},
                ]
            },
            'messages': [],
        }

        result = node.execute(state_schema, {})

        # Should extract users[-1].orders[-1].item = 'mouse'
        assert result['last_item'] == 'mouse'

    def test_complex_nested_with_multiple_indices(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test complex nested structure with multiple array indices and fields."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'input_key': 'data.items[0].tags[1].metadata.description',
            'mappings': [
                {'output_field': 'tag_desc', 'type': 'extract', 'source_path': 'description'},
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
                'data': {
                    'items': [
                        {
                            'name': 'Product A',
                            'tags': [
                                {'name': 'python', 'metadata': {'description': 'Python programming language'}},
                                {'name': 'fastapi', 'metadata': {'description': 'Modern web framework'}},
                            ],
                        }
                    ]
                }
            },
            'messages': [],
        }

        result = node.execute(state_schema, {})

        assert result['tag_desc'] == 'Modern web framework'

    def test_multiple_indices_with_etl_transformation(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test multiple array indices combined with ETL transformations."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'input_key': 'api_response.users[0].profile.contacts[0]',
            'mappings': [
                {'output_field': 'email', 'type': 'extract', 'source_path': 'contacts.email'},
                {'output_field': 'phone', 'type': 'extract', 'source_path': 'contacts.phone'},
                {'output_field': 'email_lower', 'type': 'script', 'script': 'email.lower()'},
                {
                    'output_field': 'has_valid_phone',
                    'type': 'condition',
                    'condition': "phone is not None and phone.startswith('555')",
                    'then_value': True,
                    'else_value': False,
                },
                {
                    'output_field': 'contact_summary',
                    'type': 'template',
                    'template': 'Email: {{ email_lower }}, Phone: {{ phone }}',
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
                        {
                            'id': 1,
                            'first_name': 'John',
                            'profile': {
                                'contacts': [
                                    {'email': 'JOHN@EXAMPLE.COM', 'phone': '555-1234'},
                                ]
                            },
                        }
                    ]
                }
            },
            'messages': [],
        }

        result = node.execute(state_schema, {})

        assert result['email'] == 'JOHN@EXAMPLE.COM'
        assert result['phone'] == '555-1234'
        assert result['email_lower'] == 'john@example.com'
        assert result['has_valid_phone'] is True
        assert result['contact_summary'] == 'Email: john@example.com, Phone: 555-1234'

    def test_multiple_indices_missing_intermediate_field(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test that missing intermediate fields in chain return appropriate defaults."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'input_key': 'users[0].social_media[0].profile_url',
            'mappings': [
                {'output_field': 'url', 'type': 'extract', 'source_path': 'profile_url', 'default': 'N/A'},
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
                'users': [
                    {
                        'name': 'John',
                        # Missing 'social_media' field
                    }
                ]
            },
            'messages': [],
        }

        result = node.execute(state_schema, {})

        # Should use default value when chained field path fails
        assert result['url'] == 'N/A'

    def test_multiple_indices_out_of_bounds(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test that out of bounds array access returns appropriate defaults."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'input_key': 'users[0].social_media[5].profile_url',
            'mappings': [
                {'output_field': 'url', 'type': 'extract', 'source_path': 'profile_url', 'default': 'Not found'},
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
                'users': [
                    {
                        'name': 'John',
                        'social_media': [
                            {'platform': 'twitter', 'profile_url': 'twitter.com/john'},
                        ],
                    }
                ]
            },
            'messages': [],
        }

        result = node.execute(state_schema, {})

        # Should use default value when array index is out of bounds
        assert result['url'] == 'Not found'

    def test_multiple_indices_extract_entire_nested_array(
        self,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test extracting an entire nested array without final field access."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.config = {
            'input_source': 'context_store',
            'input_key': 'users[0].social_media',
            'mappings': [
                {
                    'output_field': 'platform_list',
                    'type': 'array_map',
                    'source_path': 'social_media',
                    'item_field': 'platform',
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
                'users': [
                    {
                        'name': 'John',
                        'social_media': [
                            {'platform': 'twitter', 'profile_url': 'twitter.com/john'},
                            {'platform': 'linkedin', 'profile_url': 'linkedin.com/john'},
                            {'platform': 'github', 'profile_url': 'github.com/john'},
                        ],
                    }
                ]
            },
            'messages': [],
        }

        result = node.execute(state_schema, {})

        assert result['platform_list'] == ['twitter', 'linkedin', 'github']
