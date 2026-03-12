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
Security policy for code execution in shared sandbox environments.

This module provides YAML-based security policy loading for multi-tenant
code execution environments.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, Any

import yaml
from llm_sandbox.security import SecurityPolicy, SecurityPattern, RestrictedModule, SecurityIssueSeverity

logger = logging.getLogger(__name__)


class _SecurityPolicyLoader:
    """Internal YAML-based security policy loader."""

    SEVERITY_MAP = {
        "SAFE": SecurityIssueSeverity.SAFE,
        "LOW": SecurityIssueSeverity.LOW,
        "MEDIUM": SecurityIssueSeverity.MEDIUM,
        "HIGH": SecurityIssueSeverity.HIGH,
    }

    @classmethod
    def load_from_yaml(cls, yaml_path: Path) -> SecurityPolicy:
        """Load security policy from YAML file."""
        if not yaml_path.exists():
            raise FileNotFoundError(f"Security policy YAML file not found: {yaml_path}")

        try:
            with open(yaml_path, 'r') as f:
                config = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ValueError(f"Failed to parse YAML file: {e}") from e

        return cls._build_policy_from_config(config)

    @classmethod
    def _build_policy_from_config(cls, config: Dict[str, Any]) -> SecurityPolicy:
        """Build SecurityPolicy from parsed YAML configuration."""
        # Validate version
        version = config.get("version")
        if version and version != "1.0":
            logger.warning(f"Unsupported policy version: {version}, expected 1.0")

        # Severity threshold should NOT be in YAML - it's set separately via config
        # Use SAFE as placeholder since it will be overridden anyway
        if "severity_threshold" in config:
            logger.warning(
                "severity_threshold in YAML is ignored. "
                "Use CODE_EXECUTOR_SECURITY_THRESHOLD environment variable instead."
            )

        # Create base policy with SAFE (will be overridden by caller)
        policy = SecurityPolicy(severity_threshold=SecurityIssueSeverity.SAFE, restricted_modules=[])

        # Load restricted modules
        for module_config in config.get("restricted_modules", []):
            policy.restricted_modules.append(cls._parse_restricted_module(module_config))

        # Load security patterns
        for pattern_config in config.get("patterns", []):
            policy.add_pattern(cls._parse_security_pattern(pattern_config))

        logger.debug(
            f"Loaded policy from YAML: " f"modules={len(policy.restricted_modules)}, patterns={len(policy.patterns)}"
        )

        return policy

    @classmethod
    def _parse_restricted_module(cls, module_config: Dict[str, Any]) -> RestrictedModule:
        """Parse restricted module from configuration."""
        if not isinstance(module_config, dict):
            raise ValueError(f"Invalid module config format: {module_config}")

        name = module_config.get("name")
        if not name:
            raise ValueError("Module configuration missing 'name' field")

        return RestrictedModule(
            name=name,
            description=module_config.get("description", ""),
            severity=cls._parse_severity(module_config.get("severity", "SAFE")),
        )

    @classmethod
    def _parse_security_pattern(cls, pattern_config: Dict[str, Any]) -> SecurityPattern:
        """Parse security pattern from configuration."""
        if not isinstance(pattern_config, dict):
            raise ValueError(f"Invalid pattern config format: {pattern_config}")

        pattern = pattern_config.get("pattern")
        if not pattern:
            raise ValueError("Pattern configuration missing 'pattern' field")

        return SecurityPattern(
            pattern=pattern,
            description=pattern_config.get("description", ""),
            severity=cls._parse_severity(pattern_config.get("severity", "SAFE")),
        )

    @classmethod
    def _parse_severity(cls, severity: Any) -> SecurityIssueSeverity:
        """Parse severity level from string, int, or enum."""
        if isinstance(severity, SecurityIssueSeverity):
            return severity

        if isinstance(severity, str):
            severity_upper = severity.upper()
            if severity_upper not in cls.SEVERITY_MAP:
                raise ValueError(
                    f"Invalid severity level: {severity}. " f"Must be one of: {', '.join(cls.SEVERITY_MAP.keys())}"
                )
            return cls.SEVERITY_MAP[severity_upper]

        if isinstance(severity, int):
            try:
                return SecurityIssueSeverity(severity)
            except ValueError:
                raise ValueError(
                    f"Invalid severity level: {severity}. " f"Must be 0 (SAFE), 1 (LOW), 2 (MEDIUM), or 3 (HIGH)"
                )

        raise ValueError(f"Invalid severity type: {type(severity)}")


def get_codemie_security_policy(
    severity_threshold: Optional[SecurityIssueSeverity] = None, yaml_config_path: Optional[Path] = None
) -> SecurityPolicy:
    """
    Get the CodeMie security policy from YAML configuration.

    Simple logic:
    - If severity_threshold is None → Return empty policy (no restrictions)
    - If severity_threshold is set AND yaml_config_path is None → Load default_security_policies.yaml
    - If severity_threshold is set AND yaml_config_path provided → Load from yaml_config_path (error if not found)

    The YAML file defines:
    - System operations (os, subprocess, sys manipulation)
    - File system operations (shutil, pathlib, glob, tempfile)
    - Network operations (socket, urllib, httpx)
    - HTTP operations with configurable severity levels
    - Process/thread manipulation (threading, multiprocessing)
    - Code evaluation/compilation (eval, exec, compile)
    - Inspection/introspection modules (inspect, importlib)
    - Dangerous code patterns (regex-based detection)

    Args:
        severity_threshold: Security severity threshold.
                          If None, no restrictions are applied (empty policy).
                          SAFE (0): blocks nothing (most permissive).
                          LOW (1): allows read operations like requests.get().
                          MEDIUM (2): more restrictive.
                          HIGH (3): only blocks critical operations.
        yaml_config_path: Optional path to YAML configuration file.
                         If not provided, loads default_security_policies.yaml.
                         If provided, must exist or FileNotFoundError is raised.

    Returns:
        SecurityPolicy: Configured security policy loaded from YAML

    Raises:
        FileNotFoundError: If yaml_config_path provided but file doesn't exist
        ValueError: If YAML file format is invalid

    Examples:
        # No restrictions (severity_threshold is None)
        policy = get_codemie_security_policy(severity_threshold=None)

        # Use default policy with LOW severity
        policy = get_codemie_security_policy(severity_threshold=SecurityIssueSeverity.LOW)

        # Use custom policy file
        policy = get_codemie_security_policy(
            severity_threshold=SecurityIssueSeverity.MEDIUM,
            yaml_config_path=Path("my_policy.yaml")
        )
    """
    # If severity_threshold is None, return empty policy (no restrictions)
    if severity_threshold is None:
        logger.debug("Security policy: UNRESTRICTED (no threshold specified)")
        return None

    # Load from YAML
    if not yaml_config_path:
        # Load default policy
        default_policy_path = Path(__file__).parent / "default_security_policies.yaml"
        if not default_policy_path.exists():
            raise FileNotFoundError(f"Default security policy not found: {default_policy_path}")
        logger.debug(f"Loading default security policy: {default_policy_path.name}")
        policy = _SecurityPolicyLoader.load_from_yaml(default_policy_path)
    else:
        # Load from provided path (must exist)
        if not yaml_config_path.exists():
            raise FileNotFoundError(f"Security policy file not found: {yaml_config_path}")
        logger.debug(f"Loading custom security policy: {yaml_config_path}")
        policy = _SecurityPolicyLoader.load_from_yaml(yaml_config_path)

    # Set severity threshold from config (YAML doesn't contain threshold)
    policy.severity_threshold = severity_threshold
    logger.debug(f"Applied severity threshold: {severity_threshold.name}")

    return policy


def get_restricted_module_names(
    severity_threshold: Optional[SecurityIssueSeverity] = None, yaml_config_path: Optional[Path] = None
) -> list[str]:
    """
    Get names of restricted modules that are at or above the specified severity threshold.

    This function is useful for dynamically generating lists of blocked modules
    for tool descriptions and documentation based on the configured security level.

    Args:
        severity_threshold: Minimum severity level to include.
                          If None, returns empty list (no restrictions).
                          SAFE (0): returns all modules
                          LOW (1): returns modules with LOW, MEDIUM, and HIGH severity
                          MEDIUM (2): returns modules with MEDIUM and HIGH severity
                          HIGH (3): returns only HIGH severity modules
        yaml_config_path: Optional path to YAML configuration file.
                         If not provided, loads default_security_policies.yaml.

    Returns:
        List of module names that are restricted at the given severity level,
        sorted alphabetically for consistent output.

    Example:
        >>> get_restricted_module_names(SecurityIssueSeverity.LOW)
        ['compile', 'eval', 'exec', 'glob', 'httpx', 'importlib', 'inspect', ...]

        >>> get_restricted_module_names(SecurityIssueSeverity.HIGH)
        ['compile', 'eval', 'exec', 'httpx', 'multiprocessing', 'os', ...]

        >>> get_restricted_module_names(None)
        []
    """
    # If no threshold, return empty list
    if severity_threshold is None:
        return []

    # Get the full security policy from YAML
    policy = get_codemie_security_policy(severity_threshold=severity_threshold, yaml_config_path=yaml_config_path)

    # Extract module names from restricted modules that meet the severity threshold
    restricted_names = [module.name for module in policy.restricted_modules if module.severity >= severity_threshold]

    # Return sorted list for consistent output
    return sorted(restricted_names)
