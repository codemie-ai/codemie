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
Session factory for creating and validating sandbox sessions.

This module provides services for creating new sandbox sessions with
proper configuration, health checking, and validation.
"""

import logging
from typing import Tuple

from langchain_core.tools import ToolException
from llm_sandbox import SandboxSession, SandboxBackend, ArtifactSandboxSession
from llm_sandbox.security import SecurityPolicy
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
    retry_if_exception_type,
)

from codemie_tools.data_management.code_executor.models import CodeExecutorConfig

logger = logging.getLogger(__name__)


class SessionFactory:
    """
    Factory for creating and validating sandbox sessions.

    Handles session creation, connection retries, and health validation.
    """

    def __init__(self, config: CodeExecutorConfig, client_manager):
        """
        Initialize session factory.

        Args:
            config: Code executor configuration
            client_manager: KubernetesClientManager for getting/recreating client
        """
        self.config = config
        self.client_manager = client_manager

    @property
    def k8s_client(self):
        """Get Kubernetes client from manager."""
        return self.client_manager.get_client()

    def create_new_pod_session(
        self, workdir: str, pod_manifest: dict, security_policy: SecurityPolicy
    ) -> Tuple[SandboxSession, str]:
        """
        Create a new pod with session.

        Args:
            workdir: Working directory
            pod_manifest: Pod manifest configuration
            security_policy: Security policy for code validation

        Returns:
            Tuple[SandboxSession, str]: Session and actual pod name from cluster

        Raises:
            ToolException: If pod creation fails
        """
        session_config = self._build_session_config(
            workdir=workdir,
            security_policy=security_policy,
            pod_manifest=pod_manifest,
            keep_template=self.config.keep_template,
            default_timeout=self.config.default_timeout,
        )

        session = ArtifactSandboxSession(**session_config)
        session.open()

        actual_pod_name = getattr(session._session, "container_name", None)
        if not actual_pod_name:
            raise ToolException("Failed to retrieve pod name from cluster after creation")

        logger.debug(f"New pod created successfully: {actual_pod_name}")
        return session, actual_pod_name

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(Exception),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def connect_to_existing_pod(self, pod_name: str, workdir: str, security_policy: SecurityPolicy) -> SandboxSession:
        """
        Connect to an existing pod with retry logic.

        Uses exponential backoff: 1s, 2s, 4s, 8s, 8s between attempts.
        Automatically recreates Kubernetes client if connection pool corruption is detected.

        Args:
            pod_name: Name of the existing pod
            workdir: Working directory
            security_policy: Security policy for code validation

        Returns:
            SandboxSession: Connected session

        Raises:
            Exception: If all connection attempts fail
        """
        try:
            logger.debug(f"Connecting to pod: {pod_name}")
            session = self._create_pod_connection(pod_name, workdir, security_policy)
            self._validate_session(session, pod_name)
            logger.debug(f"Successfully connected to pod: {pod_name}")
            return session

        except Exception as e:
            # Check if this is a connection pool corruption error
            if self._is_connection_error(str(e)):
                logger.debug(
                    f"Kubernetes client connection pool corrupted ({type(e).__name__}), "
                    "recreating client for next retry"
                )
                self.client_manager.recreate_client()

            logger.warning(f"Connection attempt failed for pod {pod_name}: {type(e).__name__}: {e}")
            raise

    def _create_pod_connection(self, pod_name: str, workdir: str, security_policy: SecurityPolicy) -> SandboxSession:
        """
        Create connection to existing pod.

        Args:
            pod_name: Name of the existing pod
            workdir: Working directory
            security_policy: Security policy

        Returns:
            SandboxSession: Connected session

        Raises:
            ToolException: If connection fails
        """
        try:
            session_config = self._build_session_config(
                workdir=workdir,
                security_policy=security_policy,
                container_id=pod_name,
            )

            session = ArtifactSandboxSession(**session_config)
            session.open()

            return session

        except ToolException:
            raise
        except Exception as e:
            error_type = type(e).__name__
            logger.error(
                f"Failed to connect to existing pod {pod_name}: {error_type}: {e}",
                exc_info=True,
            )
            raise ToolException(f"Connection to pod {pod_name} failed: {error_type}: {e}") from e

    def _validate_session(self, session: SandboxSession, pod_name: str) -> None:
        """
        Validate that session is properly initialized and responsive.

        Args:
            session: The sandbox session to validate
            pod_name: Name of the pod

        Raises:
            ToolException: If validation fails
        """
        # Validate container name was set
        container_name = getattr(session._session, "container_name", None)
        if not container_name:
            raise ToolException(f"Container name not set after opening session for pod {pod_name}")

        logger.debug(f"Validated container name: {container_name} for pod: {pod_name}")

        # Health check: verify pod is responsive
        self._health_check_session(session, pod_name)

    def _health_check_session(self, session: SandboxSession, pod_name: str) -> None:
        """
        Perform health check on session.

        Args:
            session: The sandbox session to check
            pod_name: Name of the pod

        Raises:
            ToolException: If health check fails
        """
        try:
            result = session.run("print('health_check_ok')")

            if result.exit_code != 0:
                raise ToolException(
                    f"Health check failed with exit code {result.exit_code}. " f"Stderr: {result.stderr}"
                )

            if "health_check_ok" not in result.stdout:
                raise ToolException(f"Health check returned unexpected output: {result.stdout}")

            logger.debug(f"Health check passed for pod: {pod_name}")

        except ToolException:
            raise
        except Exception as e:
            error_type = type(e).__name__
            logger.error(
                f"Health check failed for pod {pod_name}: {error_type}: {e}",
                exc_info=True,
            )
            raise ToolException(f"Pod {pod_name} is not responsive: {error_type}: {e}") from e

    def _build_session_config(self, workdir: str, security_policy: SecurityPolicy, **kwargs) -> dict:
        """
        Build session configuration with common parameters.

        Args:
            workdir: Working directory
            security_policy: Security policy for code validation
            **kwargs: Additional configuration parameters

        Returns:
            dict: Session configuration
        """
        config = {
            "backend": SandboxBackend.KUBERNETES,
            "lang": "python",
            "kube_namespace": self.config.namespace,
            "verbose": self.config.verbose,
            "workdir": workdir,
            "execution_timeout": self.config.execution_timeout,
            "session_timeout": self.config.session_timeout,
            "security_policy": security_policy,
            "skip_environment_setup": self.config.skip_environment_setup,
            "client": self.k8s_client,
        }
        config.update(kwargs)
        return config

    @staticmethod
    def _is_connection_error(error_msg: str) -> bool:
        """
        Check if error message indicates a connection pool corruption.

        Common patterns:
        - WebSocketBadStatusException with "Handshake status 200 OK"
        - ApiException with "websocket" in message
        - ApiException(0) with no specific reason

        Args:
            error_msg: The error message string

        Returns:
            bool: True if this is a connection error
        """
        error_lower = error_msg.lower()
        return "handshake" in error_lower or "websocket" in error_lower or "apiexception: (0)" in error_lower
