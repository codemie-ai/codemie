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

"""Tests for SandboxSessionManager."""

import unittest
from unittest.mock import MagicMock, patch

import pytest

from codemie_tools.data_management.code_executor.models import CodeExecutorConfig
from codemie_tools.data_management.code_executor.session_manager import SandboxSessionManager


class TestSandboxSessionManager(unittest.TestCase):
    """Test suite for SandboxSessionManager."""

    def setUp(self):
        """Set up test fixtures."""
        # Reset singleton instance before each test
        SandboxSessionManager._instance = None

        self.config = CodeExecutorConfig(
            namespace="test-namespace",
            max_pod_pool_size=3,
            pod_name_prefix="test-executor-",
            docker_image="test-image:latest",
        )

    def tearDown(self):
        """Clean up after tests."""
        # Reset singleton
        SandboxSessionManager._instance = None

    def test_singleton_pattern(self):
        """Test that SandboxSessionManager is a singleton."""
        manager1 = SandboxSessionManager(config=self.config)
        manager2 = SandboxSessionManager(config=self.config)

        assert manager1 is manager2, "SandboxSessionManager should be a singleton"

    def test_init_only_once(self):
        """Test that initialization only happens once."""
        manager1 = SandboxSessionManager(config=self.config)
        original_sessions = manager1._sessions

        manager2 = SandboxSessionManager(config=self.config)
        assert manager2._sessions is original_sessions, "Second init should not reset internal state"

    def test_init_k8s_client_with_kubeconfig(self):
        """Test Kubernetes client initialization with custom kubeconfig path."""
        config_with_kubeconfig = CodeExecutorConfig(
            namespace="test-namespace",
            max_pod_pool_size=3,
            pod_name_prefix="test-executor-",
            docker_image="test-image:latest",
            kubeconfig_path="/path/to/kubeconfig",
        )

        with patch('kubernetes.config.load_kube_config') as mock_load_kube:
            with patch('kubernetes.config.load_incluster_config') as mock_load_incluster:
                with patch('kubernetes.client.CoreV1Api'):
                    manager = SandboxSessionManager(config=config_with_kubeconfig)
                    _ = manager._k8s_client

                    mock_load_kube.assert_called_once_with(config_file="/path/to/kubeconfig")
                    mock_load_incluster.assert_not_called()

    def test_init_k8s_client_incluster(self):
        """Test Kubernetes client initialization for in-cluster environment (no kubeconfig)."""
        with patch('kubernetes.config.load_kube_config') as mock_load_kube:
            with patch('kubernetes.config.load_incluster_config') as mock_load_incluster:
                with patch('kubernetes.client.CoreV1Api'):
                    manager = SandboxSessionManager(config=self.config)
                    _ = manager._k8s_client

                    mock_load_incluster.assert_called_once()
                    mock_load_kube.assert_not_called()

    def test_check_pod_exists_running(self):
        """Test checking if pod exists and is running."""
        manager = SandboxSessionManager(config=self.config)
        mock_client = MagicMock()
        manager._k8s_client_instance = mock_client

        # Mock pod with Running status
        mock_pod = MagicMock()
        mock_pod.status.phase = "Running"
        mock_client.read_namespaced_pod.return_value = mock_pod

        result = manager._check_pod_exists("test-pod")

        assert result is True
        mock_client.read_namespaced_pod.assert_called_once_with(name="test-pod", namespace="test-namespace")

    def test_check_pod_exists_not_running(self):
        """Test checking if pod exists but is not running."""
        manager = SandboxSessionManager(config=self.config)
        mock_client = MagicMock()
        manager._k8s_client_instance = mock_client

        # Mock pod with Pending status
        mock_pod = MagicMock()
        mock_pod.status.phase = "Pending"
        mock_client.read_namespaced_pod.return_value = mock_pod

        result = manager._check_pod_exists("test-pod")

        assert result is False

    def test_check_pod_not_found(self):
        """Test checking if pod does not exist."""
        manager = SandboxSessionManager(config=self.config)
        mock_client = MagicMock()
        manager._k8s_client_instance = mock_client

        mock_client.read_namespaced_pod.side_effect = Exception("Pod not found")

        result = manager._check_pod_exists("non-existent-pod")

        assert result is False

    def test_list_available_pods(self):
        """Test listing available running pods."""
        manager = SandboxSessionManager(config=self.config)
        mock_client = MagicMock()
        manager._k8s_client_instance = mock_client

        # Mock pod list
        mock_pod1 = MagicMock()
        mock_pod1.metadata.name = "test-executor-1"
        mock_pod1.status.phase = "Running"

        mock_pod2 = MagicMock()
        mock_pod2.metadata.name = "test-executor-2"
        mock_pod2.status.phase = "Running"

        mock_pod3 = MagicMock()
        mock_pod3.metadata.name = "test-executor-3"
        mock_pod3.status.phase = "Pending"

        mock_pod_list = MagicMock()
        mock_pod_list.items = [mock_pod1, mock_pod2, mock_pod3]
        mock_client.list_namespaced_pod.return_value = mock_pod_list

        result = manager._list_available_pods()

        assert len(result) == 2
        assert "test-executor-1" in result
        assert "test-executor-2" in result
        assert "test-executor-3" not in result

    def test_get_available_pod_name_reuse_existing(self):
        """Test that existing pods are randomly selected for reuse when at capacity."""
        manager = SandboxSessionManager(config=self.config)

        # At max capacity (3 pods), should reuse existing pod
        available_pods = ["existing-pod-1", "existing-pod-2", "existing-pod-3"]
        all_pods = [(p, "Running") for p in available_pods]
        with patch.object(manager._pod_discovery, 'list_available_pods', return_value=available_pods):
            with patch.object(manager._pod_discovery, 'list_all_executor_pods', return_value=all_pods):
                pod_name = manager._get_available_pod_name_or_wait()

                assert pod_name in available_pods, "Should randomly select one of the existing pods when at capacity"

    def test_get_available_pod_name_create_new(self):
        """Test that None is returned when no pods exist and we can create new."""
        manager = SandboxSessionManager(config=self.config)

        with patch.object(manager._pod_discovery, 'list_available_pods', return_value=[]):
            # Use the new method name
            pod_name = manager._get_available_pod_name_or_wait()

            # Should return None to signal new pod creation - llm_sandbox will generate actual name
            assert pod_name is None, "Should return None to signal new pod creation"

    def test_get_available_pod_name_at_capacity(self):
        """Test that existing pod is randomly selected when at max capacity."""
        manager = SandboxSessionManager(config=self.config)

        # All 3 pods running (max capacity)
        existing_pods = ["test-executor-1", "test-executor-2", "test-executor-3"]
        all_pods = [(p, "Running") for p in existing_pods]
        with patch.object(manager._pod_discovery, 'list_available_pods', return_value=existing_pods):
            with patch.object(manager._pod_discovery, 'list_all_executor_pods', return_value=all_pods):
                with patch.object(manager._config, 'max_pod_pool_size', 3):
                    pod_name = manager._get_available_pod_name_or_wait()

                    # Should randomly select one of the existing pods instead of creating new one
                    assert pod_name in existing_pods

    def test_get_available_pod_name_respects_max_limit(self):
        """Test that new pod is not created beyond max limit."""
        manager = SandboxSessionManager(config=self.config)

        # 3 pods already running (at max)
        existing_pods = ["pod-1", "pod-2", "pod-3"]
        all_pods = [(p, "Running") for p in existing_pods]

        with patch.object(manager._pod_discovery, 'list_available_pods', return_value=existing_pods):
            with patch.object(manager._pod_discovery, 'list_all_executor_pods', return_value=all_pods):
                with patch.object(manager._config, 'max_pod_pool_size', 3):
                    pod_name = manager._get_available_pod_name_or_wait()

                    # Should return existing pod, not None (since pods are available)
                    assert pod_name in existing_pods

    def test_get_or_create_lock(self):
        """Test dynamic lock creation."""
        manager = SandboxSessionManager(config=self.config)

        lock1 = manager._get_or_create_lock("pod-1")
        lock2 = manager._get_or_create_lock("pod-1")
        lock3 = manager._get_or_create_lock("pod-2")

        assert lock1 is lock2, "Should return same lock for same pod"
        assert lock1 is not lock3, "Should return different lock for different pod"

    @patch('codemie_tools.data_management.code_executor.session_factory.ArtifactSandboxSession')
    def test_connect_to_existing_pod(self, mock_session_class):
        """Test connecting to an existing pod."""
        from llm_sandbox.security import SecurityPolicy

        manager = SandboxSessionManager(config=self.config)
        mock_client = MagicMock()
        manager._k8s_client_instance = mock_client
        mock_session = MagicMock()
        mock_session._session.container_name = "existing-pod"
        mock_session_class.return_value = mock_session

        # Create a real SecurityPolicy instance
        mock_security_policy = SecurityPolicy()

        # Mock the health check run result
        mock_run_result = MagicMock()
        mock_run_result.exit_code = 0
        mock_run_result.stdout = "health_check_ok"
        mock_run_result.stderr = ""
        mock_session.run.return_value = mock_run_result

        session = manager._connect_to_existing_pod(
            pod_name="existing-pod", workdir="/test/workdir", security_policy=mock_security_policy
        )

        assert session is mock_session
        mock_session.open.assert_called_once()
        mock_session_class.assert_called_once()

    def test_session_healthy(self):
        """Test session health check with healthy session."""
        manager = SandboxSessionManager(config=self.config)
        mock_session = MagicMock()
        manager._sessions["test-pod"] = mock_session

        result = manager._is_session_healthy("test-pod")

        assert result is True
        mock_session.run.assert_called_once_with("print('health_check')")

    def test_session_unhealthy_timeout(self):
        """Test session health check with timeout."""
        from llm_sandbox.exceptions import SandboxTimeoutError

        manager = SandboxSessionManager(config=self.config)
        mock_session = MagicMock()
        mock_session.run.side_effect = SandboxTimeoutError("Timeout")
        manager._sessions["test-pod"] = mock_session

        with patch.object(manager, '_close_session'):
            result = manager._is_session_healthy("test-pod")

            assert result is False

    def test_session_unhealthy_exception(self):
        """Test session health check with exception."""
        manager = SandboxSessionManager(config=self.config)
        mock_session = MagicMock()
        mock_session.run.side_effect = Exception("Connection error")
        manager._sessions["test-pod"] = mock_session

        with patch.object(manager, '_close_session'):
            result = manager._is_session_healthy("test-pod")

            assert result is False

    def test_close_session(self):
        """Test closing a session."""
        manager = SandboxSessionManager(config=self.config)
        mock_session = MagicMock()
        manager._sessions["test-pod"] = mock_session

        manager._close_session("test-pod")

        mock_session.close.assert_called_once()
        assert "test-pod" not in manager._sessions

    def test_close_all_sessions(self):
        """Test closing all sessions."""
        manager = SandboxSessionManager(config=self.config)
        mock_session1 = MagicMock()
        mock_session2 = MagicMock()
        manager._sessions["pod-1"] = mock_session1
        manager._sessions["pod-2"] = mock_session2

        manager.close_all()

        mock_session1.close.assert_called_once()
        mock_session2.close.assert_called_once()
        assert len(manager._sessions) == 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
