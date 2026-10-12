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
        """Default security threshold is HIGH (strict) after the semantic flip."""
        config = CodeExecutorConfig()
        assert config.security_threshold == SecurityIssueSeverity.HIGH

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

    def test_security_threshold_from_env_none(self):
        """Test loading NONE threshold from environment (primary name)."""
        with patch.dict(os.environ, {"CODE_EXECUTOR_SECURITY_THRESHOLD": "NONE"}):
            config = CodeExecutorConfig.from_env()
            assert config.security_threshold == SecurityIssueSeverity.SAFE

    def test_security_threshold_from_env_safe_alias(self):
        """SAFE is accepted as a deprecated alias for NONE via env var."""
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
        assert "NONE, LOW, MEDIUM, HIGH" in str(exc_info.value)

    def test_security_threshold_validator_accepts_empty(self):
        """Test that empty threshold value is rejected."""
        with pytest.raises(ValueError):
            CodeExecutorConfig(security_threshold="")


class TestSecurityThresholdTranslation(unittest.TestCase):
    """Boundary translation from user-facing strictness to internal enforcement cutoff."""

    def test_translation_mapping(self):
        from codemie_tools.data_management.code_executor.security_policies import (
            translate_security_threshold,
        )

        assert translate_security_threshold(SecurityIssueSeverity.SAFE) == SecurityIssueSeverity.SAFE
        assert translate_security_threshold(SecurityIssueSeverity.LOW) == SecurityIssueSeverity.HIGH
        assert translate_security_threshold(SecurityIssueSeverity.MEDIUM) == SecurityIssueSeverity.MEDIUM
        assert translate_security_threshold(SecurityIssueSeverity.HIGH) == SecurityIssueSeverity.LOW


class TestSecurityPolicyThreshold(unittest.TestCase):
    """Test suite for security policy threshold mapping."""

    def test_policy_with_safe_threshold(self):
        """Test policy creation with SAFE threshold."""
        policy = get_codemie_security_policy(severity_threshold=SecurityIssueSeverity.SAFE)
        assert policy.severity_threshold == SecurityIssueSeverity.SAFE

    def test_policy_with_low_threshold(self):
        """LOW is user-facing permissive; internally it becomes the HIGH cutoff."""
        policy = get_codemie_security_policy(severity_threshold=SecurityIssueSeverity.LOW)
        assert policy.severity_threshold == SecurityIssueSeverity.HIGH

    def test_policy_with_medium_threshold(self):
        """Test policy creation with MEDIUM threshold."""
        policy = get_codemie_security_policy(severity_threshold=SecurityIssueSeverity.MEDIUM)
        assert policy.severity_threshold == SecurityIssueSeverity.MEDIUM

    def test_policy_with_high_threshold(self):
        """HIGH is user-facing strict; internally it becomes the LOW cutoff."""
        policy = get_codemie_security_policy(severity_threshold=SecurityIssueSeverity.HIGH)
        assert policy.severity_threshold == SecurityIssueSeverity.LOW

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


class TestRequestsThresholdOrdering(unittest.TestCase):
    """requests is still MEDIUM severity; substitutes for the retired pathlib scenario
    (pathlib was removed from restricted_modules by a prior, unrelated commit)."""

    def _check(self, threshold):
        from codemie_tools.data_management.code_executor.security_policies import check_security_policy

        policy = get_codemie_security_policy(severity_threshold=threshold)
        return check_security_policy(policy, "import requests\nrequests.get('https://example.com')")

    def test_safe_does_not_block(self):
        assert self._check(SecurityIssueSeverity.SAFE)[0]

    def test_low_does_not_block_medium_severity_requests(self):
        assert self._check(SecurityIssueSeverity.LOW)[0]

    def test_medium_blocks_medium_severity_requests(self):
        assert not self._check(SecurityIssueSeverity.MEDIUM)[0]

    def test_high_blocks_medium_severity_requests(self):
        assert not self._check(SecurityIssueSeverity.HIGH)[0]


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
    def test_tool_default_threshold_is_high(self, mock_manager_class):
        """CodeExecutorTool defaults to the HIGH (strict) threshold."""
        from codemie_tools.data_management.code_executor.code_executor_tool import CodeExecutorTool

        # Ensure env var is not set and force sandbox mode
        with patch.dict(os.environ, {}, clear=True):
            with patch.dict(
                os.environ, {"CODE_EXECUTOR_NAMESPACE": "test", "CODE_EXECUTOR_DOCKER_IMAGE": "test:latest"}
            ):
                tool = CodeExecutorTool(
                    user_id="test_user", file_repository=self.mock_file_repo, execution_mode=ExecutionMode.SANDBOX
                )

                assert tool.config.security_threshold == SecurityIssueSeverity.HIGH
                # HIGH (strict) translates to a LOW severity cutoff for the upstream policy,
                # so LOW + MEDIUM + HIGH severity patterns are all blocked.
                assert tool.security_policy.severity_threshold == SecurityIssueSeverity.LOW


class TestOsModulePolicy(unittest.TestCase):
    """Tests that safe os calls are allowed while dangerous ones are blocked."""

    def _check(self, code: str, threshold: SecurityIssueSeverity = SecurityIssueSeverity.LOW) -> tuple[bool, list]:
        from codemie_tools.data_management.code_executor.security_policies import check_security_policy

        policy = get_codemie_security_policy(severity_threshold=threshold)
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

    # os.system is a HIGH-severity pattern; re-verify blocking across every
    # threshold (spec Section 6), not just the previously-hardcoded LOW input.
    def test_os_system_not_blocked_at_safe_threshold(self):
        """SAFE disables enforcement entirely — os.system is allowed."""
        code = "import os\nos.system('id')"
        is_safe, violations = self._check(code, SecurityIssueSeverity.SAFE)
        assert is_safe, f"os.system unexpectedly blocked at NONE: {[v.description for v in violations]}"

    def test_os_system_blocked_at_low_threshold(self):
        """LOW (permissive) still blocks HIGH-severity patterns like os.system."""
        code = "import os\nos.system('id')"
        is_safe, _ = self._check(code, SecurityIssueSeverity.LOW)
        assert not is_safe, "os.system should be blocked at LOW"

    def test_os_system_blocked_at_medium_threshold(self):
        code = "import os\nos.system('id')"
        is_safe, _ = self._check(code, SecurityIssueSeverity.MEDIUM)
        assert not is_safe, "os.system should be blocked at MEDIUM"

    def test_os_system_blocked_at_high_threshold(self):
        code = "import os\nos.system('id')"
        is_safe, _ = self._check(code, SecurityIssueSeverity.HIGH)
        assert not is_safe, "os.system should be blocked at HIGH"


class TestPipModulePolicy(unittest.TestCase):
    """Tests that pip import is blocked at pre-execution."""

    def _check(self, code: str, threshold: SecurityIssueSeverity = SecurityIssueSeverity.LOW) -> tuple[bool, list]:
        from codemie_tools.data_management.code_executor.security_policies import check_security_policy

        policy = get_codemie_security_policy(severity_threshold=threshold)
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

    # pip is a HIGH-severity restricted module; re-verify blocking across every
    # threshold (spec Section 6), not just the previously-hardcoded LOW input.
    def test_pip_import_not_blocked_at_safe_threshold(self):
        """SAFE disables enforcement entirely — pip import is allowed."""
        code = "import pip\npip.main(['install', 'requests'])"
        is_safe, violations = self._check(code, SecurityIssueSeverity.SAFE)
        assert is_safe, f"pip import unexpectedly blocked at NONE: {[v.description for v in violations]}"

    def test_pip_import_blocked_at_low_threshold(self):
        """LOW (permissive) still blocks HIGH-severity restricted modules like pip."""
        code = "import pip\npip.main(['install', 'requests'])"
        is_safe, _ = self._check(code, SecurityIssueSeverity.LOW)
        assert not is_safe, "pip import should be blocked at LOW"

    def test_pip_import_blocked_at_medium_threshold(self):
        code = "import pip\npip.main(['install', 'requests'])"
        is_safe, _ = self._check(code, SecurityIssueSeverity.MEDIUM)
        assert not is_safe, "pip import should be blocked at MEDIUM"

    def test_pip_import_blocked_at_high_threshold(self):
        code = "import pip\npip.main(['install', 'requests'])"
        is_safe, _ = self._check(code, SecurityIssueSeverity.HIGH)
        assert not is_safe, "pip import should be blocked at HIGH"


class TestGetRestrictedModuleNames(unittest.TestCase):
    """Direct coverage for get_restricted_module_names, which feeds the agent-facing
    blocked-modules string shown in tool descriptions (CR-004)."""

    def test_none_threshold_returns_empty_list(self):
        from codemie_tools.data_management.code_executor.security_policies import get_restricted_module_names

        assert get_restricted_module_names(None) == []

    def test_low_threshold_returns_only_high_severity_module_names(self):
        """LOW is permissive; internally it becomes the HIGH cutoff, so only
        HIGH-severity restricted modules (e.g. pip, subprocess) are returned —
        MEDIUM-severity modules (e.g. requests, glob) must not appear."""
        from codemie_tools.data_management.code_executor.security_policies import get_restricted_module_names

        names = get_restricted_module_names(SecurityIssueSeverity.LOW)
        assert "pip" in names
        assert "subprocess" in names
        assert "requests" not in names
        assert "glob" not in names
        assert names == sorted(names)

    def test_high_threshold_returns_low_medium_and_high_severity_module_names(self):
        """HIGH is strict; internally it becomes the LOW cutoff, so it returns a
        superset of the LOW-threshold list, including MEDIUM-severity modules."""
        from codemie_tools.data_management.code_executor.security_policies import get_restricted_module_names

        low_names = get_restricted_module_names(SecurityIssueSeverity.LOW)
        high_names = get_restricted_module_names(SecurityIssueSeverity.HIGH)
        assert set(low_names).issubset(set(high_names))
        assert "requests" in high_names
        assert "glob" in high_names
        assert high_names == sorted(high_names)


class TestSecurityPolicyLoaderSeverityMap(unittest.TestCase):
    """SEVERITY_MAP in _SecurityPolicyLoader must accept NONE as a valid severity name."""

    def test_parse_severity_none_maps_to_safe(self):
        """NONE is the primary user-facing name for the most permissive level."""
        from codemie_tools.data_management.code_executor.security_policies import _SecurityPolicyLoader

        result = _SecurityPolicyLoader._parse_severity("NONE")
        assert result == SecurityIssueSeverity.SAFE

    def test_parse_severity_none_case_insensitive(self):
        """NONE parsing is case-insensitive like the rest of the map."""
        from codemie_tools.data_management.code_executor.security_policies import _SecurityPolicyLoader

        result = _SecurityPolicyLoader._parse_severity("none")
        assert result == SecurityIssueSeverity.SAFE

    def test_parse_severity_safe_alias_still_works(self):
        """SAFE is a deprecated alias and must still parse correctly."""
        from codemie_tools.data_management.code_executor.security_policies import _SecurityPolicyLoader

        result = _SecurityPolicyLoader._parse_severity("SAFE")
        assert result == SecurityIssueSeverity.SAFE

    def test_parse_severity_invalid_int_error_says_none(self):
        """Invalid int severity error message must say 0 (NONE), not 0 (SAFE)."""
        from codemie_tools.data_management.code_executor.security_policies import _SecurityPolicyLoader

        with pytest.raises(ValueError) as exc_info:
            _SecurityPolicyLoader._parse_severity(99)
        assert "0 (NONE)" in str(exc_info.value)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
