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
Kubernetes client management for code executor.

This module provides lazy initialization and management of Kubernetes API clients,
with support for both in-cluster and kubeconfig-based authentication.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class KubernetesClientManager:
    """
    Manager for Kubernetes API client with lazy initialization.

    Handles client initialization, configuration loading, and client recreation
    when connection issues are detected.
    """

    def __init__(self, kubeconfig_path: Optional[str] = None):
        """
        Initialize the client manager.

        Args:
            kubeconfig_path: Optional path to kubeconfig file.
                           If None, uses in-cluster configuration.
        """
        self.kubeconfig_path = kubeconfig_path
        self._client_instance = None

    def get_client(self):
        """
        Get or create Kubernetes API client.

        Lazily initializes the client on first access.

        Returns:
            kubernetes.client.CoreV1Api: Kubernetes API client
        """
        if self._client_instance is None:
            self._client_instance = self._create_client()
        return self._client_instance

    def recreate_client(self):
        """
        Force recreation of Kubernetes client.

        Useful when connection pool becomes corrupted and needs to be reset.

        Returns:
            kubernetes.client.CoreV1Api: New Kubernetes API client
        """
        logger.debug("Recreating Kubernetes client due to connection issues")
        self._client_instance = None
        return self.get_client()

    def _create_client(self):
        """
        Create a new Kubernetes API client.

        Loads configuration from kubeconfig file or in-cluster config.

        Returns:
            kubernetes.client.CoreV1Api: New Kubernetes API client
        """
        from kubernetes import client, config

        if self.kubeconfig_path:
            logger.debug(f"Loading Kubernetes config from: {self.kubeconfig_path}")
            config.load_kube_config(config_file=self.kubeconfig_path)
        else:
            logger.debug("Loading Kubernetes config for in-cluster environment")
            config.load_incluster_config()

        return client.CoreV1Api()
