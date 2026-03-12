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
Sandbox session manager for maintaining persistent code execution sessions.

This module provides the SandboxSessionManager singleton that manages
a pool of reusable sandbox sessions with automatic health checking and
intelligent pod discovery.
"""

import logging
import random
import threading
import time
from typing import Dict, Optional

from langchain_core.tools import ToolException
from llm_sandbox import SandboxSession
from llm_sandbox.exceptions import SandboxTimeoutError
from llm_sandbox.security import SecurityPolicy

from codemie_tools.data_management.code_executor.k8s_client_manager import (
    KubernetesClientManager,
)
from codemie_tools.data_management.code_executor.models import CodeExecutorConfig
from codemie_tools.data_management.code_executor.pod_discovery import PodDiscoveryService
from codemie_tools.data_management.code_executor.session_factory import SessionFactory

logger = logging.getLogger(__name__)


class SandboxSessionManager:
    """
    Singleton manager for maintaining persistent sandbox sessions.

    Manages a pool of reusable sessions mapped to pod names, providing
    thread-safe access and automatic session lifecycle management with
    protection against race conditions.

    Sessions are automatically refreshed after 5 minutes (300 seconds) to prevent
    resource accumulation and ensure fresh execution environments.

    Attributes:
        _sessions: Dictionary mapping pod names to active sandbox sessions
        _session_locks: Per-pod locks for thread-safe session access
        _session_timestamps: Dictionary tracking session creation times for TTL enforcement
        _config: Configuration for sandbox execution
        _initialized: Flag indicating whether the singleton has been initialized
        _pods_being_created: Set tracking pods currently being created (prevents over-provisioning)
        _pod_management_lock: Global lock for all pod selection/creation decisions
        _session_ttl_seconds: Session time-to-live in seconds (default: 300 = 5 minutes)
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, config: CodeExecutorConfig):
        """Implement thread-safe singleton pattern with double-checked locking."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, config: CodeExecutorConfig):
        """
        Initialize session storage and per-pod locks.

        Args:
            config: Configuration for sandbox execution
        """
        if self._initialized:
            return

        self._config = config
        self._sessions: Dict[str, SandboxSession] = {}
        self._session_locks: Dict[str, threading.Lock] = {}
        self._session_timestamps: Dict[str, float] = {}
        self._pods_being_created: set = set()
        self._pod_management_lock = threading.Lock()
        self._session_ttl_seconds = 300  # 5 minutes

        # Service components (lazy initialization)
        self._client_manager_instance = None
        self._pod_discovery_instance = None
        self._session_factory_instance = None

        self._initialized = True

    @property
    def _client_manager(self) -> KubernetesClientManager:
        """Get or create client manager (lazy initialization)."""
        if self._client_manager_instance is None:
            self._client_manager_instance = KubernetesClientManager(self._config.kubeconfig_path)
        return self._client_manager_instance

    @property
    def _pod_discovery(self) -> PodDiscoveryService:
        """Get or create pod discovery service (lazy initialization)."""
        if self._pod_discovery_instance is None:
            self._pod_discovery_instance = PodDiscoveryService(self._client_manager, self._config.namespace)
        return self._pod_discovery_instance

    @property
    def _session_factory(self) -> SessionFactory:
        """Get or create session factory (lazy initialization)."""
        if self._session_factory_instance is None:
            self._session_factory_instance = SessionFactory(self._config, self._client_manager)
        return self._session_factory_instance

    @property
    def _k8s_client(self):
        """
        Get Kubernetes client (backward compatibility property).

        Returns:
            kubernetes.client.CoreV1Api: Kubernetes API client
        """
        return self._client_manager.get_client()

    @property
    def _k8s_client_instance(self):
        """Get client instance (backward compatibility)."""
        return self._client_manager._client_instance

    @_k8s_client_instance.setter
    def _k8s_client_instance(self, value):
        """Set client instance (backward compatibility for tests)."""
        self._client_manager._client_instance = value

    def _check_pod_exists(self, pod_name: str) -> bool:
        """
        Check if pod exists (backward compatibility method).

        Args:
            pod_name: Name of the pod to check

        Returns:
            bool: True if pod exists, is running, and containers are ready
        """
        return self._pod_discovery.check_pod_exists_and_ready(pod_name)

    def _list_available_pods(self):
        """
        List available pods (backward compatibility method).

        Returns:
            List[str]: Names of available pods
        """
        return self._pod_discovery.list_available_pods()

    def _get_available_pod_name_or_wait(self) -> Optional[str]:
        """
        Get available pod (backward compatibility method).

        Returns:
            Optional[str]: Pod name or None
        """
        return self._select_or_wait_for_pod()

    def _connect_to_existing_pod(self, pod_name: str, workdir: str, security_policy: SecurityPolicy) -> SandboxSession:
        """
        Connect to existing pod (backward compatibility method).

        Args:
            pod_name: Name of the existing pod
            workdir: Working directory
            security_policy: Security policy

        Returns:
            SandboxSession: Connected session
        """
        return self._session_factory.connect_to_existing_pod(pod_name, workdir, security_policy)

    def _get_or_create_lock(self, pod_name: str) -> threading.Lock:
        """
        Get or create a lock for the specified pod in a thread-safe manner.

        Args:
            pod_name: Name of the pod

        Returns:
            threading.Lock: Lock for the pod
        """
        return self._session_locks.setdefault(pod_name, threading.Lock())

    def get_session(
        self,
        pod_name: Optional[str],
        workdir: str,
        pod_manifest: dict,
        security_policy: SecurityPolicy,
    ) -> SandboxSession:
        """
        Get or create a persistent session for the specified pod.

        This method provides thread-safe session acquisition with global pod management
        locking to prevent race conditions and over-provisioning.

        Args:
            pod_name: Name of the pod to connect to, or None to let system decide
            workdir: Working directory for the session
            pod_manifest: Pod manifest for creating new pods
            security_policy: Security policy for code validation

        Returns:
            SandboxSession: Active session for the pod

        Raises:
            ToolException: If session creation fails
        """
        with self._pod_management_lock:
            # Try to reuse existing session
            session = self._try_reuse_session(pod_name)
            if session:
                return session

            # Determine which pod to use
            selected_pod_name = pod_name if pod_name else self._select_or_wait_for_pod()

            # Try to reuse session for selected pod
            session = self._try_reuse_session(selected_pod_name)
            if session:
                return session

            # Track new pod creation
            create_new_pod = selected_pod_name is None
            if create_new_pod:
                temp_pod_id = f"__creating_pod_{id(threading.current_thread())}__"
                self._pods_being_created.add(temp_pod_id)
            else:
                pod_lock = self._get_or_create_lock(selected_pod_name)
                temp_pod_id = None

        # Create or connect to session outside global lock
        try:
            if create_new_pod:
                return self._create_new_pod_session(workdir, pod_manifest, security_policy)
            else:
                return self._connect_to_pod_session(selected_pod_name, workdir, pod_manifest, security_policy, pod_lock)
        finally:
            if create_new_pod and temp_pod_id:
                with self._pod_management_lock:
                    self._pods_being_created.discard(temp_pod_id)

    def _try_reuse_session(self, pod_name: Optional[str]) -> Optional[SandboxSession]:
        """
        Try to reuse an existing healthy session for the given pod.

        Args:
            pod_name: Name of the pod to check for existing session

        Returns:
            SandboxSession if a healthy session exists, None otherwise
        """
        if not pod_name or pod_name not in self._sessions:
            return None

        if not self._is_session_healthy(pod_name):
            return None

        logger.debug(f"Reusing existing session for pod: {pod_name}")
        session = self._sessions[pod_name]

        # Ensure pod name attribute is set
        if not hasattr(session, "_codemie_pod_name"):
            session._codemie_pod_name = pod_name

        return session

    def _select_or_wait_for_pod(self) -> Optional[str]:
        """
        Select an available pod or wait if at capacity.

        Strategy:
        1. Count total pods (running + being created)
        2. If under capacity, return None to signal new pod creation
        3. If at capacity with running pods, randomly select one for reuse
        4. If at capacity without running pods, wait for pods to become available

        Returns:
            str: Available pod name for reuse
            None: Needs new pod creation (only when under capacity)
        """
        running_pods = self._pod_discovery.list_available_pods()
        total_pods = len(running_pods) + len(self._pods_being_created)

        # Under capacity: create new pod
        if total_pods < self._config.max_pod_pool_size:
            logger.debug(
                f"Scaling up: Will create new pod "
                f"(running: {len(running_pods)}, creating: {len(self._pods_being_created)}, "
                f"total: {total_pods}, max: {self._config.max_pod_pool_size})"
            )
            return None

        # At capacity with running pods: randomly select one
        if running_pods:
            pod_name = random.choice(running_pods)  # NOSONAR
            logger.debug(f"At capacity: Selected pod {pod_name} from {len(running_pods)} available")
            return pod_name

        # At capacity without running pods: wait
        logger.debug(f"At capacity: Waiting for {len(self._pods_being_created)} pod(s) to start")
        available_pods = self._pod_discovery.wait_for_available_pods()

        if not available_pods:
            logger.warning(f"No pods available after waiting (max: {self._config.max_pod_pool_size})")
            return None

        pod_name = random.choice(available_pods)  # NOSONAR
        logger.debug(f"Using pod: {pod_name} (from {len(available_pods)} available after waiting)")
        return pod_name

    def _create_new_pod_session(
        self, workdir: str, pod_manifest: dict, security_policy: SecurityPolicy
    ) -> SandboxSession:
        """
        Create a new pod with session.

        Args:
            workdir: Working directory
            pod_manifest: Pod manifest
            security_policy: Security policy

        Returns:
            SandboxSession: Newly created session
        """
        logger.debug("Creating new pod")

        session, actual_pod_name = self._session_factory.create_new_pod_session(workdir, pod_manifest, security_policy)

        self._store_session(session, actual_pod_name)
        logger.info(f"New pod created: {actual_pod_name}")

        return session

    def _connect_to_pod_session(
        self,
        pod_name: str,
        workdir: str,
        pod_manifest: dict,
        security_policy: SecurityPolicy,
        pod_lock: threading.Lock,
    ) -> SandboxSession:
        """
        Connect to existing pod with error handling.

        Args:
            pod_name: Name of the pod
            workdir: Working directory
            pod_manifest: Pod manifest
            security_policy: Security policy
            pod_lock: Lock for this pod

        Returns:
            SandboxSession: Connected session

        Raises:
            ToolException: If connection fails
        """
        with pod_lock:
            try:
                # Check if pod exists and is ready
                if not self._pod_discovery.check_pod_exists_and_ready(pod_name):
                    raise ToolException(f"Pod {pod_name} does not exist or is not ready")

                session = self._session_factory.connect_to_existing_pod(pod_name, workdir, security_policy)

                self._store_session(session, pod_name)
                logger.debug(f"Connected to existing pod: {pod_name}")

                return session

            except ToolException as conn_error:
                logger.warning(
                    f"Failed to use pod {pod_name}: {conn_error}. " "Will retry with different pod or create new one."
                )
                # Recursive call with fresh selection
                return self.get_session(
                    pod_name=None,
                    workdir=workdir,
                    pod_manifest=pod_manifest,
                    security_policy=security_policy,
                )

    def _store_session(self, session: SandboxSession, pod_name: str) -> None:
        """
        Store session in the pool with timestamp tracking.

        Args:
            session: Session to store
            pod_name: Name of the pod
        """
        self._sessions[pod_name] = session
        self._session_timestamps[pod_name] = time.time()
        session._codemie_pod_name = pod_name

    def _is_session_healthy(self, pod_name: str) -> bool:
        """
        Check if an existing session is still healthy and responsive.

        Validates both session TTL (5 minutes) and responsiveness via health check.

        Args:
            pod_name: Name of the pod to check

        Returns:
            bool: True if session is healthy, False otherwise
        """
        # Check TTL
        if pod_name in self._session_timestamps:
            session_age = time.time() - self._session_timestamps[pod_name]
            if session_age >= self._session_ttl_seconds:
                logger.info(
                    f"Session for {pod_name} exceeded TTL "
                    f"({session_age:.0f}s >= {self._session_ttl_seconds}s), will recreate"
                )
                self._close_session(pod_name)
                return False

        # Health check
        session = self._sessions[pod_name]
        try:
            session.run("print('health_check')")
            return True
        except SandboxTimeoutError:
            logger.warning(f"Session for {pod_name} expired, will recreate")
            self._close_session(pod_name)
            return False
        except Exception as e:
            logger.warning(f"Existing session for {pod_name} is dead, will recreate: {e}")
            self._close_session(pod_name)
            return False

    def _close_session(self, pod_name: str) -> None:
        """
        Close and remove a session from the pool.

        Args:
            pod_name: Name of the pod
        """
        if pod_name in self._sessions:
            try:
                self._sessions[pod_name].close()
            except Exception as e:
                logger.warning(f"Error closing session for {pod_name}: {e}")
            finally:
                del self._sessions[pod_name]
                if pod_name in self._session_timestamps:
                    del self._session_timestamps[pod_name]

    def close_all(self) -> None:
        """Close all managed sessions. Useful for cleanup."""
        for pod_name in list(self._sessions.keys()):
            self._close_session(pod_name)
        logger.info("All sessions closed")
