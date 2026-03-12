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
Configuration models for Code Executor tool.

This module defines the configuration schema for executing Python code
in a secure, isolated Kubernetes environment.
"""

import os
from enum import Enum
from typing import Optional

from llm_sandbox.security import SecurityIssueSeverity
from pydantic import Field, field_validator

from codemie_tools.base.models import CodeMieToolConfig


class ExecutionMode(str, Enum):
    """Execution mode for code execution."""

    SANDBOX = "sandbox"
    LOCAL = "local"


class CodeExecutorConfig(CodeMieToolConfig):
    """Configuration for Code Executor tool."""

    # Working directory configuration
    workdir_base: str = Field(
        default="/home/codemie",
        description="Base working directory for code execution",
    )

    # Kubernetes configuration
    namespace: str = Field(
        default="codemie-runtime",
        description="Kubernetes namespace for executor pods",
    )

    docker_image: str = Field(
        default="epamairun/codemie-python:2.2.13-1",
        description="Docker image for Python execution environment",
    )

    # Timeout configuration (in seconds)
    execution_timeout: float = Field(
        default=30.0,
        description="Code execution timeout in seconds (protects against infinite loops)",
        gt=0,
    )

    session_timeout: float = Field(
        default=300.0,
        description="Session lifetime in seconds",
        gt=0,
    )

    default_timeout: float = Field(
        default=30.0,
        description="Default operation timeout in seconds",
        gt=0,
    )

    # Resource limits
    memory_limit: str = Field(
        default="256Mi",
        description="Memory limit for executor pods",
    )

    memory_request: str = Field(
        default="256Mi",
        description="Memory request for executor pods",
    )

    cpu_limit: str = Field(
        default="1",
        description="CPU limit for executor pods",
    )

    cpu_request: str = Field(
        default="500m",
        description="CPU request for executor pods",
    )

    # Dynamic pod pool configuration
    max_pod_pool_size: int = Field(
        default=5,
        description="Maximum number of pods to create dynamically in the pool",
        gt=0,
    )

    pod_name_prefix: str = Field(
        default="codemie-executor-",
        description="Prefix for dynamically created pod names",
    )

    # Security configuration
    run_as_user: int = Field(
        default=1001,
        description="User ID for pod execution",
        gt=0,
    )

    run_as_group: int = Field(
        default=1001,
        description="Group ID for pod execution",
        gt=0,
    )

    fs_group: int = Field(
        default=1001,
        description="Filesystem group ID for pod execution",
        gt=0,
    )

    security_threshold: Optional[SecurityIssueSeverity] = Field(
        default=SecurityIssueSeverity.LOW,
        description="Security policy severity threshold. "
        "If None, no restrictions are applied (empty policy). "
        "Defaults to LOW for balanced security. "
        "SAFE (0): blocks nothing (most permissive). "
        "LOW (1): allows read operations like requests.get(). "
        "MEDIUM (2): more restrictive. "
        "HIGH (3): only blocks critical operations.",
    )

    yaml_policy_path: str = Field(
        default="",
        description="Path to custom YAML security policy file. "
        "Leave empty to use default_security_policies.yaml. "
        "If provided, file must exist or error will be raised.",
    )

    execution_mode: ExecutionMode = Field(
        default=ExecutionMode.LOCAL,
        description="Execution mode: 'sandbox' for isolated Kubernetes pod execution, "
        "'local' for embedded kernel execution. "
        "Note: 'local' mode has limited security and resource controls.",
    )

    verbose: bool = Field(
        default=False,
        description="Enable verbose logging",
    )

    keep_template: bool = Field(
        default=True,
        description="Persist template after code execution",
    )

    skip_environment_setup: bool = Field(
        default=False,
        description="Skip environment setup in sandbox",
    )

    kubeconfig_path: str = Field(
        default="",
        description="Path to kubeconfig file for Kubernetes authentication (optional)",
    )

    @field_validator("execution_mode", mode="before")
    @classmethod
    def validate_execution_mode(cls, v) -> ExecutionMode:
        """Validate execution mode value."""
        if not v:
            return ExecutionMode.SANDBOX

        # If already an enum, return it
        if isinstance(v, ExecutionMode):
            return v

        # If string, convert to enum
        if isinstance(v, str):
            v_lower = v.lower()
            if v_lower == "sandbox":
                return ExecutionMode.SANDBOX
            elif v_lower == "local":
                return ExecutionMode.LOCAL
            else:
                raise ValueError(f"Invalid execution_mode: {v}. Must be 'sandbox' or 'local'")

        raise ValueError(f"Invalid execution_mode type: {type(v)}")

    @field_validator("security_threshold", mode="before")
    @classmethod
    def validate_security_threshold(cls, v) -> Optional[SecurityIssueSeverity]:
        """Validate and convert security threshold value."""
        # Allow None (no restrictions)
        if v is None or (isinstance(v, str) and v == ""):
            return None

        # If already an enum, return it
        if isinstance(v, SecurityIssueSeverity):
            return v

        # If string, convert to enum
        if isinstance(v, str):
            threshold_map = {
                "SAFE": SecurityIssueSeverity.SAFE,
                "LOW": SecurityIssueSeverity.LOW,
                "MEDIUM": SecurityIssueSeverity.MEDIUM,
                "HIGH": SecurityIssueSeverity.HIGH,
            }
            v_upper = v.upper()
            if v_upper not in threshold_map:
                raise ValueError(
                    f"Invalid security_threshold: {v}. Must be one of: SAFE, LOW, MEDIUM, HIGH or empty for no restrictions"
                )
            return threshold_map[v_upper]

        # If integer, convert to enum
        if isinstance(v, int):
            try:
                return SecurityIssueSeverity(v)
            except ValueError:
                raise ValueError(f"Invalid security_threshold: {v}. Must be 0 (SAFE), 1 (LOW), 2 (MEDIUM), or 3 (HIGH)")

        raise ValueError(f"Invalid security_threshold type: {type(v)}")

    @classmethod
    def from_env(cls) -> "CodeExecutorConfig":
        """
        Create configuration from environment variables with fallback to defaults.

        Environment Variables:
            CODE_EXECUTOR_EXECUTION_MODE: Execution mode (sandbox/local, default: sandbox)
            CODE_EXECUTOR_KUBECONFIG_PATH: Path to kubeconfig file (optional, takes priority over ENV)
            CODE_EXECUTOR_WORKDIR_BASE: Base working directory
            CODE_EXECUTOR_NAMESPACE: Kubernetes namespace
            CODE_EXECUTOR_DOCKER_IMAGE: Docker image
            CODE_EXECUTOR_EXECUTION_TIMEOUT: Execution timeout in seconds
            CODE_EXECUTOR_SESSION_TIMEOUT: Session timeout in seconds
            CODE_EXECUTOR_DEFAULT_TIMEOUT: Default timeout in seconds
            CODE_EXECUTOR_MEMORY_LIMIT: Memory limit
            CODE_EXECUTOR_MEMORY_REQUEST: Memory request
            CODE_EXECUTOR_CPU_LIMIT: CPU limit
            CODE_EXECUTOR_CPU_REQUEST: CPU request
            CODE_EXECUTOR_MAX_POD_POOL_SIZE: Maximum number of pods to create dynamically
            CODE_EXECUTOR_POD_NAME_PREFIX: Prefix for dynamically created pod names
            CODE_EXECUTOR_RUN_AS_USER: User ID
            CODE_EXECUTOR_RUN_AS_GROUP: Group ID
            CODE_EXECUTOR_FS_GROUP: Filesystem group ID
            CODE_EXECUTOR_SECURITY_THRESHOLD: Security policy threshold (SAFE/LOW/MEDIUM/HIGH or 0/1/2/3)
            CODE_EXECUTOR_YAML_POLICY_PATH: Path to custom YAML security policy file (optional)
            CODE_EXECUTOR_VERBOSE: Enable verbose logging (true/false)
            CODE_EXECUTOR_SKIP_ENVIRONMENT_SETUP: Skip environment setup (true/false)
            CODE_EXECUTOR_KUBECONFIG_PATH: Path to kubeconfig file (optional, takes priority over in-cluster config)

        Returns:
            CodeExecutorConfig: Configuration instance with values from environment or defaults
        """

        # Helper to convert string to bool
        def str_to_bool(value: str) -> bool:
            return value.lower() in ("true", "1", "yes")

        return cls(
            execution_mode=os.getenv("CODE_EXECUTOR_EXECUTION_MODE", "local"),
            workdir_base=os.getenv("CODE_EXECUTOR_WORKDIR_BASE", "/home/codemie"),
            namespace=os.getenv("CODE_EXECUTOR_NAMESPACE", "codemie-runtime"),
            docker_image=os.getenv("CODE_EXECUTOR_DOCKER_IMAGE", "epamairun/codemie-python:2.2.9-1"),
            execution_timeout=float(os.getenv("CODE_EXECUTOR_EXECUTION_TIMEOUT", "30.0")),
            session_timeout=float(os.getenv("CODE_EXECUTOR_SESSION_TIMEOUT", "300.0")),
            default_timeout=float(os.getenv("CODE_EXECUTOR_DEFAULT_TIMEOUT", "30.0")),
            memory_limit=os.getenv("CODE_EXECUTOR_MEMORY_LIMIT", "256Mi"),
            memory_request=os.getenv("CODE_EXECUTOR_MEMORY_REQUEST", "256Mi"),
            cpu_limit=os.getenv("CODE_EXECUTOR_CPU_LIMIT", "1"),
            cpu_request=os.getenv("CODE_EXECUTOR_CPU_REQUEST", "100m"),
            max_pod_pool_size=int(os.getenv("CODE_EXECUTOR_MAX_POD_POOL_SIZE", "5")),
            pod_name_prefix=os.getenv("CODE_EXECUTOR_POD_NAME_PREFIX", "codemie-executor-"),
            run_as_user=int(os.getenv("CODE_EXECUTOR_RUN_AS_USER", "1001")),
            run_as_group=int(os.getenv("CODE_EXECUTOR_RUN_AS_GROUP", "1001")),
            fs_group=int(os.getenv("CODE_EXECUTOR_FS_GROUP", "1001")),
            security_threshold=os.getenv("CODE_EXECUTOR_SECURITY_THRESHOLD", "LOW"),
            yaml_policy_path=os.getenv("CODE_EXECUTOR_YAML_POLICY_PATH", ""),
            verbose=str_to_bool(os.getenv("CODE_EXECUTOR_VERBOSE", "false")),
            keep_template=str_to_bool(os.getenv("CODE_EXECUTOR_KEEP_TEMPLATE", "true")),
            skip_environment_setup=str_to_bool(os.getenv("CODE_EXECUTOR_SKIP_ENVIRONMENT_SETUP", "false")),
            kubeconfig_path=os.getenv("CODE_EXECUTOR_KUBECONFIG_PATH", ""),
        )
