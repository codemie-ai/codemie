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
Pod discovery service for Kubernetes-based code execution.

This module provides services for discovering and managing executor pods
in a Kubernetes cluster, separated from session management for clarity.
"""

import logging
import time
from typing import List

logger = logging.getLogger(__name__)


class PodDiscoveryService:
    """
    Service for discovering and validating Kubernetes pods.

    Handles all pod-related queries and validation, keeping pod discovery
    logic separate from session management.
    """

    def __init__(self, client_manager, namespace: str):
        """
        Initialize pod discovery service.

        Args:
            client_manager: KubernetesClientManager for getting/recreating client
            namespace: Kubernetes namespace to search for pods
        """
        self.client_manager = client_manager
        self.namespace = namespace

    @property
    def k8s_client(self):
        """Get Kubernetes client from manager."""
        return self.client_manager.get_client()

    def list_available_pods(self) -> List[str]:
        """
        List all running pods with ready containers in the namespace.

        Only returns pods where:
        1. Pod phase is "Running"
        2. All containers have ready=True status

        Automatically recreates Kubernetes client if connection pool corruption is detected.

        Returns:
            List[str]: Names of available ready pods
        """
        try:
            pods = self.k8s_client.list_namespaced_pod(
                namespace=self.namespace,
                label_selector="app=codemie-executor",
            )
            return self._filter_ready_pods(pods)

        except Exception as e:
            # Check if this is a connection pool corruption error
            if self._is_connection_error(str(e)):
                logger.debug(
                    f"Kubernetes client connection pool corrupted ({type(e).__name__}), " "will retry with fresh client"
                )
                # Recreate client and retry once
                self.client_manager.recreate_client()
                try:
                    pods = self.k8s_client.list_namespaced_pod(
                        namespace=self.namespace,
                        label_selector="app=codemie-executor",
                    )
                    return self._filter_ready_pods(pods)
                except Exception as retry_error:
                    logger.warning(f"Retry after client recreation also failed: {retry_error}")
                    return []

            logger.warning(f"Failed to list pods: {e}")
            return []

    def check_pod_exists_and_ready(self, pod_name: str) -> bool:
        """
        Check if a pod exists, is Running, and all containers are ready.

        Automatically recreates Kubernetes client if connection pool corruption is detected.

        Args:
            pod_name: Name of the pod to check

        Returns:
            bool: True if pod exists, is running, and containers are ready
        """
        try:
            pod = self.k8s_client.read_namespaced_pod(name=pod_name, namespace=self.namespace)

            if pod.status.phase != "Running":
                logger.debug(f"Pod {pod_name} not running (phase: {pod.status.phase})")
                return False

            return self._check_containers_ready(pod, pod_name)

        except Exception as e:
            # Check if this is a connection pool corruption error
            if self._is_connection_error(str(e)):
                logger.debug(
                    f"Kubernetes client connection pool corrupted ({type(e).__name__}), " "will retry with fresh client"
                )
                # Recreate client and retry once
                self.client_manager.recreate_client()
                try:
                    pod = self.k8s_client.read_namespaced_pod(name=pod_name, namespace=self.namespace)

                    if pod.status.phase != "Running":
                        logger.debug(f"Pod {pod_name} not running (phase: {pod.status.phase})")
                        return False

                    return self._check_containers_ready(pod, pod_name)
                except Exception as retry_error:
                    logger.debug(f"Pod {pod_name} check failed after retry: {retry_error}")
                    return False

            logger.debug(f"Pod {pod_name} does not exist or cannot be accessed: {e}")
            return False

    def list_all_executor_pods(self) -> List[tuple]:
        """
        List all executor pods with their status for capacity enforcement and diagnostics.

        Returns:
            List of (pod_name, status) tuples. Status is the pod phase or a more
            specific container waiting reason (e.g. ImagePullBackOff, ErrImagePull).
        """
        try:
            pods = self.k8s_client.list_namespaced_pod(
                namespace=self.namespace,
                label_selector="app=codemie-executor",
            )
            return self._extract_pod_statuses(pods.items)
        except Exception as e:
            if self._is_connection_error(str(e)):
                logger.debug(
                    f"Kubernetes client connection pool corrupted ({type(e).__name__}), will retry with fresh client"
                )
                self.client_manager.recreate_client()
                try:
                    pods = self.k8s_client.list_namespaced_pod(
                        namespace=self.namespace,
                        label_selector="app=codemie-executor",
                    )
                    return self._extract_pod_statuses(pods.items)
                except Exception as retry_error:
                    logger.warning(f"Retry after client recreation also failed: {retry_error}")
                    return []
            logger.warning(f"Failed to list executor pods: {e}")
            return []

    def wait_for_available_pods(self, max_retries: int = 20, retry_delay: float = 0.5) -> List[str]:
        """
        Poll for available Running pods with retry logic.

        Args:
            max_retries: Maximum number of retry attempts
            retry_delay: Delay between retries in seconds

        Returns:
            List[str]: List of available pod names (may be empty)
        """
        for attempt in range(max_retries):
            available_pods = self.list_available_pods()

            if available_pods:
                logger.debug(f"Found {len(available_pods)} available pod(s) on attempt {attempt + 1}")
                return available_pods

            if attempt < max_retries - 1:
                logger.debug(f"No running pods found on attempt {attempt + 1}, " f"waiting {retry_delay}s before retry")
                time.sleep(retry_delay)
            else:
                logger.debug(f"No running pods found after {max_retries} attempts")

        return []

    def _extract_pod_statuses(self, pod_items) -> List[tuple]:
        """
        Extract (pod_name, status) from a list of pod objects.
        Status is the phase, or a container waiting reason if more specific.
        """
        result = []
        for pod in pod_items:
            phase = pod.status.phase if pod.status else "Unknown"
            status = phase
            if phase != "Running" and pod.status and pod.status.container_statuses:
                for cs in pod.status.container_statuses:
                    if cs.state and cs.state.waiting and cs.state.waiting.reason:
                        status = cs.state.waiting.reason
                        break
            result.append((pod.metadata.name, status))
        return result

    def _filter_ready_pods(self, pods) -> List[str]:
        """
        Filter pods to return only ready ones.

        Args:
            pods: Kubernetes pod list object

        Returns:
            List[str]: Names of ready pods
        """
        available = []
        for pod in pods.items:
            if self._is_pod_ready(pod):
                available.append(pod.metadata.name)

        logger.debug(f"Found {len(available)} running pods in namespace {self.namespace}")
        return available

    def _is_pod_ready(self, pod) -> bool:
        """
        Check if a pod is ready (Running phase with ready containers).

        Args:
            pod: Kubernetes pod object

        Returns:
            bool: True if pod is ready, False otherwise
        """
        pod_name = pod.metadata.name

        if pod.status.phase != "Running":
            logger.debug(f"Skipping pod {pod_name}: phase={pod.status.phase}")
            return False

        return self._check_containers_ready(pod, pod_name)

    def _check_containers_ready(self, pod, pod_name: str) -> bool:
        """
        Check if all containers in a pod are ready.

        Args:
            pod: Kubernetes pod object
            pod_name: Name of the pod

        Returns:
            bool: True if all containers are ready
        """
        if not pod.status.container_statuses:
            logger.debug(f"Pod {pod_name} has no container_statuses yet, trusting Running phase")
            return True

        all_ready = all(container.ready for container in pod.status.container_statuses)
        if not all_ready:
            ready_count = sum(1 for c in pod.status.container_statuses if c.ready)
            logger.debug(
                f"Skipping pod {pod_name}: containers not ready "
                f"({ready_count}/{len(pod.status.container_statuses)})"
            )
            return False

        return True

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
