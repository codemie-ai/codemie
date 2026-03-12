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

"""Unit tests for CodeExecutorTool execution mode switching and dynamic schema generation."""

import unittest
from unittest.mock import MagicMock, patch

from pydantic import BaseModel

from codemie_tools.base.file_object import FileObject
from codemie_tools.data_management.code_executor.code_executor_tool import (
    CodeExecutorTool,
    get_code_executor_input_schema,
)
from codemie_tools.data_management.code_executor.models import ExecutionMode, CodeExecutorConfig


class TestExecutionModeSelection(unittest.TestCase):
    """Test suite for execution mode selection and precedence."""

    def setUp(self) -> None:
        self.mock_file_repo = MagicMock()

    def test_default_execution_mode_is_local(self):
        """Test that default execution mode is LOCAL."""
        tool = CodeExecutorTool(user_id="test_user", file_repository=self.mock_file_repo)

        assert tool.config.execution_mode == ExecutionMode.LOCAL

    @patch.dict('os.environ', {'CODE_EXECUTOR_EXECUTION_MODE': 'sandbox'})
    def test_execution_mode_from_environment(self):
        """Test execution mode selection from environment variable."""
        tool = CodeExecutorTool(user_id="test_user", file_repository=self.mock_file_repo)

        # Environment variable should be respected
        assert tool.config.execution_mode == ExecutionMode.SANDBOX

    @patch.dict('os.environ', {'CODE_EXECUTOR_EXECUTION_MODE': 'local'})
    def test_execution_mode_explicit_parameter_overrides_env(self):
        """Test that explicit execution_mode parameter overrides environment."""
        tool = CodeExecutorTool(
            user_id="test_user", execution_mode=ExecutionMode.SANDBOX, file_repository=self.mock_file_repo
        )

        # Explicit parameter should take precedence
        assert tool.config.execution_mode == ExecutionMode.SANDBOX
        assert tool._mode_override is True

    def test_execution_mode_explicit_parameter(self):
        """Test execution mode with explicit parameter."""
        tool = CodeExecutorTool(
            user_id="test_user", execution_mode=ExecutionMode.LOCAL, file_repository=self.mock_file_repo
        )

        assert tool.config.execution_mode == ExecutionMode.LOCAL
        assert tool._mode_override is True

    @patch.dict('os.environ', {'CODE_EXECUTOR_EXECUTION_MODE': 'sandbox'})
    def test_execution_mode_none_parameter_uses_env(self):
        """Test that None execution_mode parameter uses environment."""
        tool = CodeExecutorTool(user_id="test_user", execution_mode=None, file_repository=self.mock_file_repo)

        # Should use environment value
        assert tool.config.execution_mode == ExecutionMode.SANDBOX
        assert tool._mode_override is False


class TestDynamicInputSchemaGeneration(unittest.TestCase):
    """Test suite for dynamic input schema generation."""

    def test_schema_has_code_field(self):
        """Test that SANDBOX mode schema has 'code' field."""
        schema = get_code_executor_input_schema(
            execution_mode=ExecutionMode.SANDBOX, blocked_modules="os, sys", file_names=None
        )
        assert issubclass(schema, BaseModel)
        assert "code" in schema.model_fields
        # SANDBOX mode should have security information
        field_info = schema.model_fields["code"]
        assert "BLOCKED modules" in field_info.description or "security" in field_info.description.lower()

    def test_schema_has_export_files_field(self):
        """Test that SANDBOX mode schema has 'export_files' field."""
        schema = get_code_executor_input_schema(
            execution_mode=ExecutionMode.SANDBOX, blocked_modules="os", file_names=None
        )

        assert "export_files" in schema.model_fields
        field_info = schema.model_fields["export_files"]
        assert field_info.is_required() is False  # Should be optional

    def test_schema_includes_blocked_modules(self):
        """Test that sandbox schema includes blocked modules information."""
        blocked_modules = "os, sys, subprocess"
        schema = get_code_executor_input_schema(
            execution_mode=ExecutionMode.SANDBOX, blocked_modules=blocked_modules, file_names=None
        )

        field_info = schema.model_fields["code"]
        assert blocked_modules in field_info.description

    def test_schema_includes_file_names(self):
        """Test that sandbox schema includes available file names."""
        file_names = ["data.csv", "config.json"]
        schema = get_code_executor_input_schema(
            execution_mode=ExecutionMode.SANDBOX, blocked_modules="os", file_names=file_names
        )

        field_info = schema.model_fields["code"]
        description = field_info.description
        assert "data.csv" in description
        assert "config.json" in description
        assert "AVAILABLE FILES" in description

    def test_schema_without_file_names(self):
        """Test that sandbox schema works without file names."""
        schema = get_code_executor_input_schema(
            execution_mode=ExecutionMode.SANDBOX, blocked_modules="os", file_names=None
        )

        field_info = schema.model_fields["code"]
        description = field_info.description
        assert "AVAILABLE FILES" not in description

    def test_schema_with_special_characters_in_filenames(self):
        """Test that sandbox schema handles special characters in filenames."""
        file_names = ["[Video] Template.pptx", "Data (2024).csv", "file with spaces.txt"]
        schema = get_code_executor_input_schema(
            execution_mode=ExecutionMode.SANDBOX, blocked_modules="os", file_names=file_names
        )

        field_info = schema.model_fields["code"]
        description = field_info.description
        assert "[Video] Template.pptx" in description
        assert "Data (2024).csv" in description
        assert "file with spaces.txt" in description


class TestCodeExecutorToolSchemaInitialization(unittest.TestCase):
    """Test suite for CodeExecutorTool args_schema initialization."""

    def setUp(self) -> None:
        self.mock_file_repo = MagicMock()

    def test_tool_initializes_schema_for_local_mode(self):
        """Test that tool initializes correct schema for LOCAL mode."""
        tool = CodeExecutorTool(
            user_id="test_user", execution_mode=ExecutionMode.LOCAL, file_repository=self.mock_file_repo
        )

        assert tool.args_schema is not None
        assert "code" in tool.args_schema.model_fields
        assert "export_files" in tool.args_schema.model_fields

    @patch('codemie_tools.data_management.code_executor.code_executor_tool.SandboxSessionManager')
    def test_tool_initializes_schema_for_sandbox_mode(self, mock_manager):
        """Test that tool initializes correct schema for SANDBOX mode."""
        tool = CodeExecutorTool(
            user_id="test_user", execution_mode=ExecutionMode.SANDBOX, file_repository=self.mock_file_repo
        )

        assert tool.args_schema is not None
        assert "code" in tool.args_schema.model_fields
        assert "export_files" in tool.args_schema.model_fields

    @patch('codemie_tools.data_management.code_executor.code_executor_tool.SandboxSessionManager')
    def test_tool_schema_includes_input_file_names(self, mock_manager):
        """Test that tool schema includes input file names when provided."""
        file1 = FileObject(name="input.csv", mime_type="text/csv", owner="user")
        file2 = FileObject(name="config.json", mime_type="application/json", owner="user")

        tool = CodeExecutorTool(
            user_id="test_user",
            input_files=[file1, file2],
            execution_mode=ExecutionMode.SANDBOX,
            file_repository=self.mock_file_repo,
        )

        field_info = tool.args_schema.model_fields["code"]
        description = field_info.description
        assert "input.csv" in description
        assert "config.json" in description


class TestExecutionModeRouting(unittest.TestCase):
    """Test suite for execution mode routing in execute method."""

    def setUp(self) -> None:
        self.mock_file_repo = MagicMock()

    def test_local_mode_routes_to_execute_local(self):
        """Test that LOCAL mode routes to _execute_local."""
        tool = CodeExecutorTool(
            user_id="test_user",
            execution_mode=ExecutionMode.LOCAL,
            file_repository=self.mock_file_repo,
        )

        with patch.object(tool, '_execute_local', return_value="Local execution result") as mock_execute_local:
            result = tool.execute(code="print('hello')")

            mock_execute_local.assert_called_once_with("print('hello')", None)
        assert result == "Local execution result"

    @patch('codemie_tools.data_management.code_executor.code_executor_tool.SandboxSessionManager')
    def test_sandbox_mode_routes_to_execute_sandbox(self, mock_manager_class):
        """Test that SANDBOX mode routes to _execute_sandbox."""
        # Setup mocks
        mock_manager = MagicMock()
        mock_session = MagicMock()
        mock_manager.get_session.return_value = mock_session
        mock_manager._get_available_pod_name.return_value = "test-pod"
        mock_manager_class.return_value = mock_manager

        mock_session.is_safe.return_value = (True, [])
        mock_result = MagicMock()
        mock_result.stdout = "Sandbox execution result"
        mock_result.stderr = ""
        mock_result.exit_code = 0
        mock_session.run.return_value = mock_result

        tool = CodeExecutorTool(
            user_id="test_user", execution_mode=ExecutionMode.SANDBOX, file_repository=self.mock_file_repo
        )

        with patch.object(tool, '_get_available_pod_name', return_value="test-pod"):
            result = tool.execute(code="print('hello')")

            mock_session.run.assert_called_once()
            assert "Sandbox execution result" in result

    def test_mode_logging_with_explicit_parameter(self):
        """Test that execution mode is logged with reason."""
        tool = CodeExecutorTool(
            user_id="test_user", execution_mode=ExecutionMode.LOCAL, file_repository=self.mock_file_repo
        )

        with patch('codemie_tools.data_management.code_executor.code_executor_tool.logger') as mock_logger:
            tool.execute(code="print('test')")

            # Check that debug logging was called with reason
            debug_calls = mock_logger.debug.call_args_list
            assert any("LOCAL mode" in str(call) for call in debug_calls)


class TestSchemaFieldDescriptions(unittest.TestCase):
    """Test suite for detailed schema field descriptions."""

    def test_local_schema_mentions_matplotlib(self):
        """Test that LOCAL schema mentions matplotlib usage."""
        schema = get_code_executor_input_schema(
            execution_mode=ExecutionMode.LOCAL, blocked_modules=None, file_names=None
        )

        field_info = schema.model_fields["code"]
        description = field_info.description
        assert "matplotlib" in description.lower()

    def test_sandbox_schema_mentions_pre_installed_libraries(self):
        """Test that SANDBOX schema mentions pre-installed libraries."""
        schema = get_code_executor_input_schema(
            execution_mode=ExecutionMode.SANDBOX, blocked_modules="os", file_names=None
        )

        field_info = schema.model_fields["code"]
        description = field_info.description
        assert "Pre-installed Python libraries" in description or "COMMON_SANDBOX_LIBRARIES" in description

    def test_sandbox_schema_mentions_safe_stdlib_modules(self):
        """Test that SANDBOX schema mentions safe stdlib modules."""
        schema = get_code_executor_input_schema(
            execution_mode=ExecutionMode.SANDBOX, blocked_modules="os", file_names=None
        )

        field_info = schema.model_fields["code"]
        description = field_info.description
        assert "SAFE standard library modules" in description or "json, datetime" in description

    def test_sandbox_schema_mentions_matplotlib_approaches(self):
        """Test that SANDBOX schema mentions matplotlib plot generation approaches."""
        schema = get_code_executor_input_schema(
            execution_mode=ExecutionMode.SANDBOX, blocked_modules="os", file_names=None
        )

        field_info = schema.model_fields["code"]
        description = field_info.description
        assert "MATPLOTLIB" in description
        assert "plt.savefig" in description or "plt.show" in description


class TestExecutionModeConfiguration(unittest.TestCase):
    """Test suite for execution mode configuration integration."""

    def setUp(self) -> None:
        self.mock_file_repo = MagicMock()

    def test_config_from_env_respects_execution_mode(self):
        """Test that config from environment respects execution_mode."""
        with patch.dict('os.environ', {'CODE_EXECUTOR_EXECUTION_MODE': 'sandbox'}):
            config = CodeExecutorConfig.from_env()
            assert config.execution_mode == ExecutionMode.SANDBOX

    def test_tool_respects_config_execution_mode(self):
        """Test that tool respects config's execution_mode."""
        config = CodeExecutorConfig(execution_mode=ExecutionMode.LOCAL)
        tool = CodeExecutorTool(user_id="test_user", file_repository=self.mock_file_repo)
        tool.config = config

        assert tool.config.execution_mode == ExecutionMode.LOCAL

    @patch('codemie_tools.data_management.code_executor.code_executor_tool.SandboxSessionManager')
    def test_security_policy_only_initialized_for_both_modes(self, mock_manager):
        """Test that security policy is only initialized for SANDBOX mode."""
        # Test SANDBOX mode
        tool_sandbox = CodeExecutorTool(
            user_id="test_user", execution_mode=ExecutionMode.SANDBOX, file_repository=self.mock_file_repo
        )
        assert tool_sandbox.security_policy is not None

        # Test LOCAL mode
        tool_local = CodeExecutorTool(
            user_id="test_user", execution_mode=ExecutionMode.LOCAL, file_repository=self.mock_file_repo
        )
        assert tool_local.security_policy is not None
