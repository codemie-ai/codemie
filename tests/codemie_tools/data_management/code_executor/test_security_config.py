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

"""Tests for CodeExecutor security configuration."""

import os
import unittest
from unittest.mock import MagicMock, patch

import pytest
from llm_sandbox.security import SecurityIssueSeverity

from codemie_tools.data_management.code_executor.models import CodeExecutorConfig, ExecutionMode
from codemie_tools.data_management.code_executor.security_policies import get_codemie_security_policy


class TestSecurityThresholdConfig(unittest.TestCase):
    """Test suite for security threshold configuration."""

    def test_default_security_threshold(self):
        """Test that default security threshold is LOW."""
        config = CodeExecutorConfig()
        assert config.security_threshold == SecurityIssueSeverity.LOW

    def test_security_threshold_from_env_low(self):
        """Test loading LOW threshold from environment."""
        with patch.dict(os.environ, {"CODE_EXECUTOR_SECURITY_THRESHOLD": "LOW"}):
            config = CodeExecutorConfig.from_env()
            assert config.security_threshold == SecurityIssueSeverity.LOW

    def test_security_threshold_from_env_medium(self):
        """Test loading MEDIUM threshold from environment."""
        with patch.dict(os.environ, {"CODE_EXECUTOR_SECURITY_THRESHOLD": "MEDIUM"}):
            config = CodeExecutorConfig.from_env()
            assert config.security_threshold == SecurityIssueSeverity.MEDIUM

    def test_security_threshold_from_env_high(self):
        """Test loading HIGH threshold from environment."""
        with patch.dict(os.environ, {"CODE_EXECUTOR_SECURITY_THRESHOLD": "HIGH"}):
            config = CodeExecutorConfig.from_env()
            assert config.security_threshold == SecurityIssueSeverity.HIGH

    def test_security_threshold_from_env_safe(self):
        """Test loading SAFE threshold from environment."""
        with patch.dict(os.environ, {"CODE_EXECUTOR_SECURITY_THRESHOLD": "SAFE"}):
            config = CodeExecutorConfig.from_env()
            assert config.security_threshold == SecurityIssueSeverity.SAFE

    def test_security_threshold_case_insensitive(self):
        """Test that threshold validation is case insensitive."""
        config = CodeExecutorConfig(security_threshold="low")
        assert config.security_threshold == SecurityIssueSeverity.LOW

        config = CodeExecutorConfig(security_threshold="MeDiUm")
        assert config.security_threshold == SecurityIssueSeverity.MEDIUM

    def test_security_threshold_invalid_value(self):
        """Test that invalid threshold value raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            CodeExecutorConfig(security_threshold="INVALID")

        assert "Invalid security_threshold" in str(exc_info.value)
        assert "SAFE, LOW, MEDIUM, HIGH" in str(exc_info.value)

    def test_security_threshold_validator_accepts_empty(self):
        """Test that empty threshold value is rejected."""
        with pytest.raises(ValueError):
            CodeExecutorConfig(security_threshold="")


class TestSecurityPolicyThreshold(unittest.TestCase):
    """Test suite for security policy threshold mapping."""

    def test_policy_with_safe_threshold(self):
        """Test policy creation with SAFE threshold."""
        policy = get_codemie_security_policy(severity_threshold=SecurityIssueSeverity.SAFE)
        assert policy.severity_threshold == SecurityIssueSeverity.SAFE

    def test_policy_with_low_threshold(self):
        """Test policy creation with LOW threshold."""
        policy = get_codemie_security_policy(severity_threshold=SecurityIssueSeverity.LOW)
        assert policy.severity_threshold == SecurityIssueSeverity.LOW

    def test_policy_with_medium_threshold(self):
        """Test policy creation with MEDIUM threshold."""
        policy = get_codemie_security_policy(severity_threshold=SecurityIssueSeverity.MEDIUM)
        assert policy.severity_threshold == SecurityIssueSeverity.MEDIUM

    def test_policy_with_high_threshold(self):
        """Test policy creation with HIGH threshold."""
        policy = get_codemie_security_policy(severity_threshold=SecurityIssueSeverity.HIGH)
        assert policy.severity_threshold == SecurityIssueSeverity.HIGH

    def test_policy_default_threshold(self):
        """Test that policy with no threshold returns None (unrestricted)."""
        policy = get_codemie_security_policy(severity_threshold=None)
        assert policy is None


class TestRequestsSecurityPatterns(unittest.TestCase):
    """Test suite for requests library security patterns."""

    def test_requests_module_is_medium_severity(self):
        """Test that requests module is marked as MEDIUM severity."""
        policy = get_codemie_security_policy(severity_threshold=SecurityIssueSeverity.LOW)

        # Find requests module in restricted modules
        requests_module = None
        for module in policy.restricted_modules:
            if module.name == "requests":
                requests_module = module
                break

        assert requests_module is not None, "requests module not found in restricted modules"
        assert requests_module.severity == SecurityIssueSeverity.MEDIUM

    def test_requests_http_pattern_is_medium_severity(self):
        """Test that requests HTTP operations pattern is MEDIUM severity."""
        policy = get_codemie_security_policy(severity_threshold=SecurityIssueSeverity.LOW)

        # Find requests pattern (get, post, put, delete)
        requests_pattern = None
        for pattern in policy.patterns:
            if "requests\\." in pattern.pattern and (
                "get|post|put|delete" in pattern.pattern or "post|put|delete|get" in pattern.pattern
            ):
                requests_pattern = pattern
                break

        assert requests_pattern is not None, "requests HTTP pattern not found"
        assert requests_pattern.severity == SecurityIssueSeverity.MEDIUM

    def test_requests_pattern_includes_all_methods(self):
        """Test that requests pattern includes all major HTTP methods."""
        policy = get_codemie_security_policy(severity_threshold=SecurityIssueSeverity.LOW)

        # Find requests pattern
        requests_pattern = None
        for pattern in policy.patterns:
            if "requests\\." in pattern.pattern:
                requests_pattern = pattern
                break

        assert requests_pattern is not None, "requests pattern not found"
        # Pattern should include get, post, put, delete
        pattern_text = requests_pattern.pattern
        assert "get" in pattern_text.lower()
        assert "post" in pattern_text.lower()
        assert "put" in pattern_text.lower()
        assert "delete" in pattern_text.lower()


class TestSecurityThresholdIntegration(unittest.TestCase):
    """Integration tests for security threshold configuration."""

    def setUp(self) -> None:
        self.mock_file_repo = MagicMock()

    @patch('codemie_tools.data_management.code_executor.code_executor_tool.SandboxSessionManager')
    def test_tool_uses_config_threshold(self, mock_manager_class):
        """Test that CodeExecutorTool uses threshold from config."""
        from codemie_tools.data_management.code_executor.code_executor_tool import CodeExecutorTool

        # Mock environment variable and force sandbox mode
        with patch.dict(os.environ, {"CODE_EXECUTOR_SECURITY_THRESHOLD": "MEDIUM"}):
            tool = CodeExecutorTool(
                user_id="test_user", file_repository=self.mock_file_repo, execution_mode=ExecutionMode.SANDBOX
            )

            # Verify tool's security policy uses the config threshold
            assert tool.config.security_threshold == SecurityIssueSeverity.MEDIUM
            assert tool.security_policy.severity_threshold == SecurityIssueSeverity.MEDIUM

    @patch('codemie_tools.data_management.code_executor.code_executor_tool.SandboxSessionManager')
    def test_tool_default_threshold_is_low(self, mock_manager_class):
        """Test that CodeExecutorTool defaults to LOW threshold."""
        from codemie_tools.data_management.code_executor.code_executor_tool import CodeExecutorTool

        # Ensure env var is not set and force sandbox mode
        with patch.dict(os.environ, {}, clear=True):
            with patch.dict(
                os.environ, {"CODE_EXECUTOR_NAMESPACE": "test", "CODE_EXECUTOR_DOCKER_IMAGE": "test:latest"}
            ):
                tool = CodeExecutorTool(
                    user_id="test_user", file_repository=self.mock_file_repo, execution_mode=ExecutionMode.SANDBOX
                )

                assert tool.config.security_threshold == SecurityIssueSeverity.LOW
                assert tool.security_policy.severity_threshold == SecurityIssueSeverity.LOW


class TestOsModulePolicy(unittest.TestCase):
    """Tests that safe os calls are allowed while dangerous ones are blocked."""

    def _check(self, code: str) -> tuple[bool, list]:
        from codemie_tools.data_management.code_executor.security_policies import check_security_policy

        policy = get_codemie_security_policy(severity_threshold=SecurityIssueSeverity.LOW)
        return check_security_policy(policy, code)

    def test_os_urandom_is_allowed(self):
        """os.urandom is a pure in-process call and must not be blocked."""
        code = "import os\ndata = os.urandom(16)\nprint(data.hex())"
        is_safe, violations = self._check(code)
        assert is_safe, f"os.urandom unexpectedly blocked: {[v.description for v in violations]}"

    def test_os_path_is_allowed(self):
        """os.path.join / os.getcwd() are harmless navigation helpers."""
        code = "import os\nprint(os.path.join('/tmp', 'test'))\nprint(os.getcwd())"
        is_safe, violations = self._check(code)
        assert is_safe, f"os.path unexpectedly blocked: {[v.description for v in violations]}"

    def test_os_makedirs_is_allowed(self):
        """os.makedirs for output dirs is legitimate; the runtime guard enforces workspace boundaries."""
        code = "import os\nos.makedirs('output', exist_ok=True)"
        is_safe, violations = self._check(code)
        assert is_safe, f"os.makedirs unexpectedly blocked: {[v.description for v in violations]}"

    def test_os_mkdir_is_allowed(self):
        """os.mkdir for a workspace-relative dir must not be blocked at pre-execution."""
        code = "import os\nos.mkdir('results')"
        is_safe, violations = self._check(code)
        assert is_safe, f"os.mkdir unexpectedly blocked: {[v.description for v in violations]}"

    def test_os_system_is_blocked(self):
        """os.system() must be blocked — it spawns a shell command."""
        code = "import os\nos.system('id')"
        is_safe, violations = self._check(code)
        assert not is_safe

    def test_os_remove_is_blocked(self):
        """os.remove() must be blocked — filesystem deletions are not allowed."""
        code = "import os\nos.remove('/etc/passwd')"
        is_safe, violations = self._check(code)
        assert not is_safe

    def test_os_rmdir_is_blocked(self):
        """os.rmdir() must be blocked — filesystem deletions are not allowed."""
        code = "import os\nos.rmdir('sensitive_dir')"
        is_safe, violations = self._check(code)
        assert not is_safe

    def test_os_unlink_is_blocked(self):
        """os.unlink() must be blocked — filesystem deletions are not allowed."""
        code = "import os\nos.unlink('secret.key')"
        is_safe, violations = self._check(code)
        assert not is_safe

    def test_os_environ_write_is_blocked(self):
        """os.environ['KEY'] = ... must be blocked — it can leak secrets."""
        code = "import os\nos.environ['SECRET'] = 'leaked'"
        is_safe, violations = self._check(code)
        assert not is_safe


class TestPipModulePolicy(unittest.TestCase):
    """Tests that pip import is blocked at pre-execution."""

    def _check(self, code: str) -> tuple[bool, list]:
        from codemie_tools.data_management.code_executor.security_policies import check_security_policy

        policy = get_codemie_security_policy(severity_threshold=SecurityIssueSeverity.LOW)
        return check_security_policy(policy, code)

    def test_pip_import_is_blocked(self):
        """import pip must be denied — runtime package install is not allowed."""
        code = "import pip\npip.main(['install', 'requests'])"
        is_safe, violations = self._check(code)
        assert not is_safe, "pip import should be blocked but was allowed"

    def test_pip_from_import_is_blocked(self):
        """from pip import ... must also be denied."""
        code = "from pip import main\nmain(['install', 'requests'])"
        is_safe, violations = self._check(code)
        assert not is_safe, "from pip import should be blocked but was allowed"

    def test_pip_module_has_high_severity(self):
        """pip restricted module must be configured as HIGH severity."""
        policy = get_codemie_security_policy(severity_threshold=SecurityIssueSeverity.LOW)
        pip_module = next((m for m in policy.restricted_modules if m.name == "pip"), None)
        assert pip_module is not None, "pip not found in restricted_modules"
        assert pip_module.severity == SecurityIssueSeverity.HIGH


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
