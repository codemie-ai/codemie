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

"""Unit tests for CodeExecutor models."""

import os
import unittest
from unittest.mock import patch

import pytest
from llm_sandbox.security import SecurityIssueSeverity

from codemie_tools.data_management.code_executor.models import (
    CodeExecutorConfig,
    ExecutionMode,
)


class TestExecutionMode(unittest.TestCase):
    """Test suite for ExecutionMode enum."""

    def test_execution_mode_values(self):
        """Test ExecutionMode enum values."""
        assert ExecutionMode.SANDBOX.value == "sandbox"
        assert ExecutionMode.LOCAL.value == "local"

    def test_execution_mode_is_string_enum(self):
        """Test that ExecutionMode inherits from str."""
        assert isinstance(ExecutionMode.SANDBOX, str)
        assert isinstance(ExecutionMode.LOCAL, str)


class TestCodeExecutorConfigDefaults(unittest.TestCase):
    """Test suite for CodeExecutorConfig default values."""

    def test_default_values(self):
        """Test default configuration values."""
        config = CodeExecutorConfig()

        assert config.workdir_base == "/home/codemie"
        assert config.namespace == "codemie-runtime"
        assert config.docker_image == "epamairun/codemie-python:2.2.13-1"
        assert config.execution_timeout == 30.0
        assert config.session_timeout == 300.0
        assert config.default_timeout == 30.0
        assert config.memory_limit == "256Mi"
        assert config.memory_request == "256Mi"
        assert config.cpu_limit == "1"
        assert config.cpu_request == "500m"
        assert config.max_pod_pool_size == 5
        assert config.pod_name_prefix == "codemie-executor-"
        assert config.run_as_user == 1001
        assert config.run_as_group == 1001
        assert config.fs_group == 1001
        assert config.security_threshold == SecurityIssueSeverity.LOW
        assert config.yaml_policy_path == ""
        assert config.execution_mode == ExecutionMode.LOCAL
        assert config.verbose is False
        assert config.keep_template is True
        assert config.skip_environment_setup is False
        assert config.kubeconfig_path == ""


class TestCodeExecutorConfigValidation(unittest.TestCase):
    """Test suite for CodeExecutorConfig validation."""

    def test_execution_timeout_must_be_positive(self):
        """Test that execution_timeout must be greater than 0."""
        with pytest.raises(ValueError):
            CodeExecutorConfig(execution_timeout=0)

        with pytest.raises(ValueError):
            CodeExecutorConfig(execution_timeout=-1)

    def test_session_timeout_must_be_positive(self):
        """Test that session_timeout must be greater than 0."""
        with pytest.raises(ValueError):
            CodeExecutorConfig(session_timeout=0)

        with pytest.raises(ValueError):
            CodeExecutorConfig(session_timeout=-1)

    def test_default_timeout_must_be_positive(self):
        """Test that default_timeout must be greater than 0."""
        with pytest.raises(ValueError):
            CodeExecutorConfig(default_timeout=0)

        with pytest.raises(ValueError):
            CodeExecutorConfig(default_timeout=-1)

    def test_max_pod_pool_size_must_be_positive(self):
        """Test that max_pod_pool_size must be greater than 0."""
        with pytest.raises(ValueError):
            CodeExecutorConfig(max_pod_pool_size=0)

        with pytest.raises(ValueError):
            CodeExecutorConfig(max_pod_pool_size=-1)

    def test_run_as_user_must_be_positive(self):
        """Test that run_as_user must be greater than 0."""
        with pytest.raises(ValueError):
            CodeExecutorConfig(run_as_user=0)

        with pytest.raises(ValueError):
            CodeExecutorConfig(run_as_user=-1)

    def test_run_as_group_must_be_positive(self):
        """Test that run_as_group must be greater than 0."""
        with pytest.raises(ValueError):
            CodeExecutorConfig(run_as_group=0)

        with pytest.raises(ValueError):
            CodeExecutorConfig(run_as_group=-1)

    def test_fs_group_must_be_positive(self):
        """Test that fs_group must be greater than 0."""
        with pytest.raises(ValueError):
            CodeExecutorConfig(fs_group=0)

        with pytest.raises(ValueError):
            CodeExecutorConfig(fs_group=-1)


class TestCodeExecutorConfigExecutionModeValidator(unittest.TestCase):
    """Test suite for execution_mode field validator."""

    def test_execution_mode_from_string_sandbox(self):
        """Test execution_mode validation with 'sandbox' string."""
        config = CodeExecutorConfig(execution_mode="sandbox")
        assert config.execution_mode == ExecutionMode.SANDBOX

    def test_execution_mode_from_string_local(self):
        """Test execution_mode validation with 'local' string."""
        config = CodeExecutorConfig(execution_mode="local")
        assert config.execution_mode == ExecutionMode.LOCAL

    def test_execution_mode_case_insensitive(self):
        """Test that execution_mode is case insensitive."""
        config = CodeExecutorConfig(execution_mode="SANDBOX")
        assert config.execution_mode == ExecutionMode.SANDBOX

        config = CodeExecutorConfig(execution_mode="Local")
        assert config.execution_mode == ExecutionMode.LOCAL

    def test_execution_mode_from_enum(self):
        """Test execution_mode validation with enum value."""
        config = CodeExecutorConfig(execution_mode=ExecutionMode.SANDBOX)
        assert config.execution_mode == ExecutionMode.SANDBOX

    def test_execution_mode_invalid_string(self):
        """Test that invalid execution_mode string raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            CodeExecutorConfig(execution_mode="invalid")

        assert "Invalid execution_mode" in str(exc_info.value)
        assert "Must be 'sandbox' or 'local'" in str(exc_info.value)

    def test_execution_mode_empty_defaults_to_sandbox(self):
        """Test that empty execution_mode defaults to SANDBOX."""
        config = CodeExecutorConfig(execution_mode="")
        assert config.execution_mode == ExecutionMode.SANDBOX

    def test_execution_mode_none_defaults_to_sandbox(self):
        """Test that None execution_mode defaults to SANDBOX."""
        config = CodeExecutorConfig(execution_mode=None)
        assert config.execution_mode == ExecutionMode.SANDBOX


class TestCodeExecutorConfigSecurityThresholdValidator(unittest.TestCase):
    """Test suite for security_threshold field validator."""

    def test_security_threshold_from_string_safe(self):
        """Test security_threshold validation with 'SAFE' string."""
        config = CodeExecutorConfig(security_threshold="SAFE")
        assert config.security_threshold == SecurityIssueSeverity.SAFE

    def test_security_threshold_from_string_low(self):
        """Test security_threshold validation with 'LOW' string."""
        config = CodeExecutorConfig(security_threshold="LOW")
        assert config.security_threshold == SecurityIssueSeverity.LOW

    def test_security_threshold_from_string_medium(self):
        """Test security_threshold validation with 'MEDIUM' string."""
        config = CodeExecutorConfig(security_threshold="MEDIUM")
        assert config.security_threshold == SecurityIssueSeverity.MEDIUM

    def test_security_threshold_from_string_high(self):
        """Test security_threshold validation with 'HIGH' string."""
        config = CodeExecutorConfig(security_threshold="HIGH")
        assert config.security_threshold == SecurityIssueSeverity.HIGH

    def test_security_threshold_case_insensitive(self):
        """Test that security_threshold is case insensitive."""
        config = CodeExecutorConfig(security_threshold="low")
        assert config.security_threshold == SecurityIssueSeverity.LOW

    def test_security_threshold_from_enum(self):
        """Test security_threshold validation with enum value."""
        config = CodeExecutorConfig(security_threshold=SecurityIssueSeverity.MEDIUM)
        assert config.security_threshold == SecurityIssueSeverity.MEDIUM

    def test_security_threshold_from_integer(self):
        """Test security_threshold validation with integer value."""
        config = CodeExecutorConfig(security_threshold=0)
        assert config.security_threshold == SecurityIssueSeverity.SAFE

        config = CodeExecutorConfig(security_threshold=1)
        assert config.security_threshold == SecurityIssueSeverity.LOW

        config = CodeExecutorConfig(security_threshold=2)
        assert config.security_threshold == SecurityIssueSeverity.MEDIUM

        config = CodeExecutorConfig(security_threshold=3)
        assert config.security_threshold == SecurityIssueSeverity.HIGH

    def test_security_threshold_invalid_string(self):
        """Test that invalid security_threshold string raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            CodeExecutorConfig(security_threshold="INVALID")

        assert "Invalid security_threshold" in str(exc_info.value)
        assert "SAFE, LOW, MEDIUM, HIGH" in str(exc_info.value)

    def test_security_threshold_invalid_integer(self):
        """Test that invalid security_threshold integer raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            CodeExecutorConfig(security_threshold=99)

        assert "Invalid security_threshold" in str(exc_info.value)

    def test_security_threshold_none_for_unrestricted(self):
        """Test that None security_threshold means no restrictions."""
        config = CodeExecutorConfig(security_threshold=None)
        assert config.security_threshold is None

    def test_security_threshold_empty_string_for_unrestricted(self):
        """Test that empty string security_threshold means no restrictions."""
        config = CodeExecutorConfig(security_threshold="")
        assert config.security_threshold is None


class TestCodeExecutorConfigFromEnv(unittest.TestCase):
    """Test suite for from_env class method."""

    def test_from_env_with_defaults(self):
        """Test from_env with no environment variables set."""
        with patch.dict(os.environ, {}, clear=True):
            config = CodeExecutorConfig.from_env()

            assert config.execution_mode == ExecutionMode.LOCAL
            assert config.workdir_base == "/home/codemie"
            assert config.namespace == "codemie-runtime"

    def test_from_env_execution_mode(self):
        """Test from_env with CODE_EXECUTOR_EXECUTION_MODE."""
        with patch.dict(os.environ, {"CODE_EXECUTOR_EXECUTION_MODE": "sandbox"}):
            config = CodeExecutorConfig.from_env()
            assert config.execution_mode == ExecutionMode.SANDBOX

    def test_from_env_workdir_base(self):
        """Test from_env with CODE_EXECUTOR_WORKDIR_BASE."""
        with patch.dict(os.environ, {"CODE_EXECUTOR_WORKDIR_BASE": "/custom/workdir"}):
            config = CodeExecutorConfig.from_env()
            assert config.workdir_base == "/custom/workdir"

    def test_from_env_namespace(self):
        """Test from_env with CODE_EXECUTOR_NAMESPACE."""
        with patch.dict(os.environ, {"CODE_EXECUTOR_NAMESPACE": "custom-namespace"}):
            config = CodeExecutorConfig.from_env()
            assert config.namespace == "custom-namespace"

    def test_from_env_docker_image(self):
        """Test from_env with CODE_EXECUTOR_DOCKER_IMAGE."""
        with patch.dict(os.environ, {"CODE_EXECUTOR_DOCKER_IMAGE": "custom-image:1.0"}):
            config = CodeExecutorConfig.from_env()
            assert config.docker_image == "custom-image:1.0"

    def test_from_env_timeouts(self):
        """Test from_env with timeout environment variables."""
        with patch.dict(
            os.environ,
            {
                "CODE_EXECUTOR_EXECUTION_TIMEOUT": "60.0",
                "CODE_EXECUTOR_SESSION_TIMEOUT": "600.0",
                "CODE_EXECUTOR_DEFAULT_TIMEOUT": "45.0",
            },
        ):
            config = CodeExecutorConfig.from_env()
            assert config.execution_timeout == 60.0
            assert config.session_timeout == 600.0
            assert config.default_timeout == 45.0

    def test_from_env_resource_limits(self):
        """Test from_env with resource limit environment variables."""
        with patch.dict(
            os.environ,
            {
                "CODE_EXECUTOR_MEMORY_LIMIT": "512Mi",
                "CODE_EXECUTOR_MEMORY_REQUEST": "512Mi",
                "CODE_EXECUTOR_CPU_LIMIT": "2",
                "CODE_EXECUTOR_CPU_REQUEST": "1",
            },
        ):
            config = CodeExecutorConfig.from_env()
            assert config.memory_limit == "512Mi"
            assert config.memory_request == "512Mi"
            assert config.cpu_limit == "2"
            assert config.cpu_request == "1"

    def test_from_env_pod_pool_config(self):
        """Test from_env with pod pool configuration."""
        with patch.dict(
            os.environ, {"CODE_EXECUTOR_MAX_POD_POOL_SIZE": "10", "CODE_EXECUTOR_POD_NAME_PREFIX": "custom-executor-"}
        ):
            config = CodeExecutorConfig.from_env()
            assert config.max_pod_pool_size == 10
            assert config.pod_name_prefix == "custom-executor-"

    def test_from_env_security_config(self):
        """Test from_env with security configuration."""
        with patch.dict(
            os.environ,
            {
                "CODE_EXECUTOR_RUN_AS_USER": "2000",
                "CODE_EXECUTOR_RUN_AS_GROUP": "2000",
                "CODE_EXECUTOR_FS_GROUP": "2000",
                "CODE_EXECUTOR_SECURITY_THRESHOLD": "HIGH",
                "CODE_EXECUTOR_YAML_POLICY_PATH": "/path/to/policy.yaml",
            },
        ):
            config = CodeExecutorConfig.from_env()
            assert config.run_as_user == 2000
            assert config.run_as_group == 2000
            assert config.fs_group == 2000
            assert config.security_threshold == SecurityIssueSeverity.HIGH
            assert config.yaml_policy_path == "/path/to/policy.yaml"

    def test_from_env_boolean_flags(self):
        """Test from_env with boolean flag environment variables."""
        with patch.dict(
            os.environ,
            {
                "CODE_EXECUTOR_VERBOSE": "true",
                "CODE_EXECUTOR_KEEP_TEMPLATE": "false",
                "CODE_EXECUTOR_SKIP_ENVIRONMENT_SETUP": "1",
            },
        ):
            config = CodeExecutorConfig.from_env()
            assert config.verbose is True
            assert config.keep_template is False
            assert config.skip_environment_setup is True

    def test_from_env_kubeconfig_path(self):
        """Test from_env with CODE_EXECUTOR_KUBECONFIG_PATH."""
        with patch.dict(os.environ, {"CODE_EXECUTOR_KUBECONFIG_PATH": "/path/to/kubeconfig"}):
            config = CodeExecutorConfig.from_env()
            assert config.kubeconfig_path == "/path/to/kubeconfig"

    def test_from_env_str_to_bool_variations(self):
        """Test from_env boolean conversion with various string values."""
        # Test "true"
        with patch.dict(os.environ, {"CODE_EXECUTOR_VERBOSE": "true"}):
            config = CodeExecutorConfig.from_env()
            assert config.verbose is True

        # Test "1"
        with patch.dict(os.environ, {"CODE_EXECUTOR_VERBOSE": "1"}):
            config = CodeExecutorConfig.from_env()
            assert config.verbose is True

        # Test "yes"
        with patch.dict(os.environ, {"CODE_EXECUTOR_VERBOSE": "yes"}):
            config = CodeExecutorConfig.from_env()
            assert config.verbose is True

        # Test "false"
        with patch.dict(os.environ, {"CODE_EXECUTOR_VERBOSE": "false"}):
            config = CodeExecutorConfig.from_env()
            assert config.verbose is False

        # Test "0"
        with patch.dict(os.environ, {"CODE_EXECUTOR_VERBOSE": "0"}):
            config = CodeExecutorConfig.from_env()
            assert config.verbose is False


class TestCodeExecutorConfigCustomValues(unittest.TestCase):
    """Test suite for custom configuration values."""

    def test_custom_workdir_base(self):
        """Test creating config with custom workdir_base."""
        config = CodeExecutorConfig(workdir_base="/custom/path")
        assert config.workdir_base == "/custom/path"

    def test_custom_namespace(self):
        """Test creating config with custom namespace."""
        config = CodeExecutorConfig(namespace="my-namespace")
        assert config.namespace == "my-namespace"

    def test_custom_docker_image(self):
        """Test creating config with custom docker_image."""
        config = CodeExecutorConfig(docker_image="my-image:latest")
        assert config.docker_image == "my-image:latest"

    def test_custom_timeouts(self):
        """Test creating config with custom timeout values."""
        config = CodeExecutorConfig(execution_timeout=60.0, session_timeout=600.0, default_timeout=45.0)
        assert config.execution_timeout == 60.0
        assert config.session_timeout == 600.0
        assert config.default_timeout == 45.0

    def test_custom_resource_limits(self):
        """Test creating config with custom resource limits."""
        config = CodeExecutorConfig(memory_limit="1Gi", memory_request="512Mi", cpu_limit="2", cpu_request="1")
        assert config.memory_limit == "1Gi"
        assert config.memory_request == "512Mi"
        assert config.cpu_limit == "2"
        assert config.cpu_request == "1"

    def test_custom_pod_pool_size(self):
        """Test creating config with custom max_pod_pool_size."""
        config = CodeExecutorConfig(max_pod_pool_size=10)
        assert config.max_pod_pool_size == 10

    def test_custom_pod_name_prefix(self):
        """Test creating config with custom pod_name_prefix."""
        config = CodeExecutorConfig(pod_name_prefix="my-executor-")
        assert config.pod_name_prefix == "my-executor-"

    def test_custom_security_settings(self):
        """Test creating config with custom security settings."""
        config = CodeExecutorConfig(run_as_user=2000, run_as_group=2000, fs_group=2000)
        assert config.run_as_user == 2000
        assert config.run_as_group == 2000
        assert config.fs_group == 2000
