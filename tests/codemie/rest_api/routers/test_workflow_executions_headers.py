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

"""
Test suite for workflow_executions router with request_headers propagation.

Tests that workflow execution endpoints properly extract and propagate request_headers
to WorkflowExecutor for MCP header propagation.
"""

from unittest.mock import Mock, patch

from fastapi import BackgroundTasks, Request

from codemie.core.workflow_models import CreateWorkflowExecutionRequest
from codemie.rest_api.routers.workflow_executions import (
    create_workflow_execution,
    resume_workflow_execution,
)
from codemie.rest_api.security.user import User


class TestWorkflowExecutionsRouterWithHeaders:
    """Test cases for workflow_executions router with request_headers."""

    @patch('codemie.rest_api.routers.workflow_executions.WorkflowExecutor.create_executor')
    @patch('codemie.rest_api.routers.workflow_executions.WorkflowService')
    @patch('codemie.rest_api.routers.workflow_executions.request_summary_manager_module')
    @patch('codemie.rest_api.routers.workflow_executions.extract_custom_headers')
    @patch('codemie.rest_api.routers.workflow_executions.Ability')
    @patch('codemie.rest_api.routers.workflow_executions._validate_remote_entities_and_raise')
    @patch('codemie.rest_api.routers.workflow_executions._validate_workflow_supports_files_and_raise')
    def test_create_workflow_execution_with_header_propagation(
        self,
        mock_validate_files,
        mock_validate_remote,
        mock_ability,
        mock_extract_headers,
        mock_request_summary,
        mock_workflow_service,
        mock_create_executor,
    ):
        """
        TC-3.2.1: Verify create_workflow_execution with header propagation.

        Priority: Critical

        Tests that when propagate_headers=True, headers are extracted and passed to WorkflowExecutor.
        """
        # Arrange
        from codemie.core.workflow_models.workflow_config import WorkflowMode

        mock_workflow_config = Mock()
        mock_workflow_config.mode = WorkflowMode.SEQUENTIAL
        mock_workflow_config.project = 'test-project'

        mock_workflow_service_instance = Mock()
        mock_workflow_service_instance.get_workflow.return_value = mock_workflow_config
        mock_execution = Mock()
        mock_execution.execution_id = 'exec-123'
        mock_workflow_service_instance.create_workflow_execution.return_value = mock_execution
        mock_workflow_service.return_value = mock_workflow_service_instance

        mock_ability_instance = Mock()
        mock_ability_instance.can.return_value = True
        mock_ability.return_value = mock_ability_instance

        test_headers = {'X-Tenant-ID': 'tenant-123', 'X-User-ID': 'user-456'}
        mock_extract_headers.return_value = test_headers

        mock_executor = Mock()
        mock_executor.stream = Mock()
        mock_create_executor.return_value = mock_executor

        request = CreateWorkflowExecutionRequest(
            user_input='test input',
            file_name='test.txt',
            propagate_headers=True,
        )

        raw_request = Mock(spec=Request)
        user = Mock(spec=User)
        user.as_user_model.return_value = Mock()
        background_tasks = Mock(spec=BackgroundTasks)

        # Act
        create_workflow_execution(
            request=request,
            workflow_id='wf-123',
            background_tasks=background_tasks,
            raw_request=raw_request,
            user=user,
        )

        # Assert - extract_custom_headers called with propagate_headers=True
        mock_extract_headers.assert_called_once_with(raw_request, True)

        # Assert - WorkflowExecutor.create_executor called with request_headers
        mock_create_executor.assert_called_once()
        call_kwargs = mock_create_executor.call_args[1]
        assert 'request_headers' in call_kwargs
        assert call_kwargs['request_headers'] == test_headers

    @patch('codemie.rest_api.routers.workflow_executions.WorkflowExecutor.create_executor')
    @patch('codemie.rest_api.routers.workflow_executions.WorkflowService')
    @patch('codemie.rest_api.routers.workflow_executions.request_summary_manager_module')
    @patch('codemie.rest_api.routers.workflow_executions.extract_custom_headers')
    @patch('codemie.rest_api.routers.workflow_executions.Ability')
    @patch('codemie.rest_api.routers.workflow_executions._validate_remote_entities_and_raise')
    @patch('codemie.rest_api.routers.workflow_executions._validate_workflow_supports_files_and_raise')
    def test_create_workflow_execution_without_propagation(
        self,
        mock_validate_files,
        mock_validate_remote,
        mock_ability,
        mock_extract_headers,
        mock_request_summary,
        mock_workflow_service,
        mock_create_executor,
    ):
        """
        TC-3.2.2: Verify create_workflow_execution without propagation.

        Priority: High

        Tests that when propagate_headers=False (default), headers are not propagated.
        """
        # Arrange
        from codemie.core.workflow_models.workflow_config import WorkflowMode

        mock_workflow_config = Mock()
        mock_workflow_config.mode = WorkflowMode.SEQUENTIAL
        mock_workflow_config.project = 'test-project'

        mock_workflow_service_instance = Mock()
        mock_workflow_service_instance.get_workflow.return_value = mock_workflow_config
        mock_execution = Mock()
        mock_execution.execution_id = 'exec-123'
        mock_workflow_service_instance.create_workflow_execution.return_value = mock_execution
        mock_workflow_service.return_value = mock_workflow_service_instance

        mock_ability_instance = Mock()
        mock_ability_instance.can.return_value = True
        mock_ability.return_value = mock_ability_instance

        mock_extract_headers.return_value = None  # No headers when propagate=False

        mock_executor = Mock()
        mock_executor.stream = Mock()
        mock_create_executor.return_value = mock_executor

        request = CreateWorkflowExecutionRequest(
            user_input='test input',
            propagate_headers=False,  # Explicit False
        )

        raw_request = Mock(spec=Request)
        user = Mock(spec=User)
        user.as_user_model.return_value = Mock()
        background_tasks = Mock(spec=BackgroundTasks)

        # Act
        create_workflow_execution(
            request=request,
            workflow_id='wf-123',
            background_tasks=background_tasks,
            raw_request=raw_request,
            user=user,
        )

        # Assert - extract_custom_headers called with propagate_headers=False
        mock_extract_headers.assert_called_once_with(raw_request, False)

        # Assert - WorkflowExecutor.create_executor called with request_headers=None
        mock_create_executor.assert_called_once()
        call_kwargs = mock_create_executor.call_args[1]
        assert 'request_headers' in call_kwargs
        assert call_kwargs['request_headers'] is None

    @patch('codemie.rest_api.routers.workflow_executions.WorkflowExecutor.create_executor')
    @patch('codemie.rest_api.routers.workflow_executions.WorkflowService')
    @patch('codemie.rest_api.routers.workflow_executions.request_summary_manager_module')
    @patch('codemie.rest_api.routers.workflow_executions.extract_custom_headers')
    @patch('codemie.rest_api.routers.workflow_executions.Ability')
    @patch('codemie.rest_api.routers.workflow_executions._validate_remote_entities_and_raise')
    @patch('codemie.rest_api.routers.workflow_executions._validate_workflow_supports_files_and_raise')
    def test_create_workflow_execution_with_blocked_headers(
        self,
        mock_validate_files,
        mock_validate_remote,
        mock_ability,
        mock_extract_headers,
        mock_request_summary,
        mock_workflow_service,
        mock_create_executor,
    ):
        """
        TC-3.2.3: Verify create_workflow_execution with blocked headers.

        Priority: Critical

        Tests that blocked headers are filtered out by extract_custom_headers.
        """
        # Arrange
        from codemie.core.workflow_models.workflow_config import WorkflowMode

        mock_workflow_config = Mock()
        mock_workflow_config.mode = WorkflowMode.SEQUENTIAL
        mock_workflow_config.project = 'test-project'

        mock_workflow_service_instance = Mock()
        mock_workflow_service_instance.get_workflow.return_value = mock_workflow_config
        mock_execution = Mock()
        mock_execution.execution_id = 'exec-123'
        mock_workflow_service_instance.create_workflow_execution.return_value = mock_execution
        mock_workflow_service.return_value = mock_workflow_service_instance

        mock_ability_instance = Mock()
        mock_ability_instance.can.return_value = True
        mock_ability.return_value = mock_ability_instance

        # extract_custom_headers returns only non-blocked headers
        allowed_headers = {'X-Tenant-ID': 'tenant-123'}
        mock_extract_headers.return_value = allowed_headers

        mock_executor = Mock()
        mock_executor.stream = Mock()
        mock_create_executor.return_value = mock_executor

        request = CreateWorkflowExecutionRequest(
            user_input='test input',
            propagate_headers=True,
        )

        raw_request = Mock(spec=Request)
        # Simulate raw request having both allowed and blocked headers
        raw_request.headers = {
            'X-Tenant-ID': 'tenant-123',
            'X-Auth-Token': 'secret',  # Should be blocked
            'Authorization': 'Bearer token',  # Not X-* header
        }

        user = Mock(spec=User)
        user.as_user_model.return_value = Mock()
        background_tasks = Mock(spec=BackgroundTasks)

        # Act
        create_workflow_execution(
            request=request,
            workflow_id='wf-123',
            background_tasks=background_tasks,
            raw_request=raw_request,
            user=user,
        )

        # Assert - WorkflowExecutor receives only allowed headers (blocking done by extract_custom_headers)
        mock_create_executor.assert_called_once()
        call_kwargs = mock_create_executor.call_args[1]
        assert call_kwargs['request_headers'] == allowed_headers
        assert 'X-Auth-Token' not in call_kwargs['request_headers']
        assert 'Authorization' not in call_kwargs['request_headers']

    @patch('codemie.rest_api.routers.workflow_executions.WorkflowExecutor.create_executor')
    @patch('codemie.rest_api.routers.workflow_executions.WorkflowService')
    @patch('codemie.rest_api.routers.workflow_executions.request_summary_manager_module')
    @patch('codemie.rest_api.routers.workflow_executions.extract_custom_headers')
    @patch('codemie.rest_api.routers.workflow_executions.Ability')
    def test_resume_workflow_execution_with_headers(
        self,
        mock_ability,
        mock_extract_headers,
        mock_request_summary,
        mock_workflow_service,
        mock_create_executor,
    ):
        """
        TC-3.2.4: Verify resume_workflow_execution with headers.

        Priority: Critical

        Tests that headers are propagated when resuming a workflow with propagate_headers=True.
        """
        # Arrange
        from codemie.core.workflow_models.workflow_config import WorkflowMode

        mock_workflow_config = Mock()
        mock_workflow_config.mode = WorkflowMode.SEQUENTIAL
        mock_workflow_config.project = 'test-project'

        mock_workflow_service_instance = Mock()
        mock_workflow_service_instance.get_workflow.return_value = mock_workflow_config
        mock_execution = Mock()
        mock_execution.execution_id = 'exec-123'
        mock_workflow_service_instance.find_workflow_execution_by_id.return_value = mock_execution
        mock_workflow_service.return_value = mock_workflow_service_instance

        mock_ability_instance = Mock()
        mock_ability_instance.can.return_value = True
        mock_ability.return_value = mock_ability_instance

        test_headers = {'X-Tenant-ID': 'tenant-123', 'X-Workflow-ID': 'wf-789'}
        mock_extract_headers.return_value = test_headers

        mock_executor = Mock()
        mock_executor.stream = Mock()
        mock_create_executor.return_value = mock_executor

        raw_request = Mock(spec=Request)
        user = Mock(spec=User)
        user.as_user_model.return_value = Mock()
        background_tasks = Mock(spec=BackgroundTasks)

        # Act
        resume_workflow_execution(
            workflow_id='wf-123',
            execution_id='exec-123',
            background_tasks=background_tasks,
            raw_request=raw_request,
            user=user,
            propagate_headers=True,  # Enable header propagation
        )

        # Assert - extract_custom_headers called with propagate_headers=True
        mock_extract_headers.assert_called_once_with(raw_request, True)

        # Assert - WorkflowExecutor.create_executor called with request_headers
        mock_create_executor.assert_called_once()
        call_kwargs = mock_create_executor.call_args[1]
        assert 'request_headers' in call_kwargs
        assert call_kwargs['request_headers'] == test_headers
        assert call_kwargs['resume_execution'] is True

    @patch('codemie.rest_api.routers.workflow_executions.WorkflowExecutor.create_executor')
    @patch('codemie.rest_api.routers.workflow_executions.WorkflowService')
    @patch('codemie.rest_api.routers.workflow_executions.request_summary_manager_module')
    @patch('codemie.rest_api.routers.workflow_executions.extract_custom_headers')
    @patch('codemie.rest_api.routers.workflow_executions.Ability')
    def test_resume_workflow_execution_without_headers(
        self,
        mock_ability,
        mock_extract_headers,
        mock_request_summary,
        mock_workflow_service,
        mock_create_executor,
    ):
        """
        TC-3.2.5: Verify resume_workflow_execution without headers.

        Priority: High

        Tests that resume works without header propagation (backward compatibility).
        """
        # Arrange
        from codemie.core.workflow_models.workflow_config import WorkflowMode

        mock_workflow_config = Mock()
        mock_workflow_config.mode = WorkflowMode.SEQUENTIAL
        mock_workflow_config.project = 'test-project'

        mock_workflow_service_instance = Mock()
        mock_workflow_service_instance.get_workflow.return_value = mock_workflow_config
        mock_execution = Mock()
        mock_execution.execution_id = 'exec-123'
        mock_workflow_service_instance.find_workflow_execution_by_id.return_value = mock_execution
        mock_workflow_service.return_value = mock_workflow_service_instance

        mock_ability_instance = Mock()
        mock_ability_instance.can.return_value = True
        mock_ability.return_value = mock_ability_instance

        mock_extract_headers.return_value = None

        mock_executor = Mock()
        mock_executor.stream = Mock()
        mock_create_executor.return_value = mock_executor

        raw_request = Mock(spec=Request)
        user = Mock(spec=User)
        user.as_user_model.return_value = Mock()
        background_tasks = Mock(spec=BackgroundTasks)

        # Act - call with propagate_headers=False (default)
        resume_workflow_execution(
            workflow_id='wf-123',
            execution_id='exec-123',
            background_tasks=background_tasks,
            raw_request=raw_request,
            user=user,
            propagate_headers=False,  # Default value
        )

        # Assert - extract_custom_headers called with propagate_headers=False
        mock_extract_headers.assert_called_once_with(raw_request, False)

        # Assert - WorkflowExecutor.create_executor called with request_headers=None
        mock_create_executor.assert_called_once()
        call_kwargs = mock_create_executor.call_args[1]
        assert 'request_headers' in call_kwargs
        assert call_kwargs['request_headers'] is None
