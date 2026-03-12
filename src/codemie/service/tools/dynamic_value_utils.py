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

import copy
import json
import re
from collections import deque
from typing import Any, Optional
from codemie.configs import logger

from jinja2 import Template


# Custom exceptions for recursive resolution
class CircularDependencyError(Exception):
    """Raised when circular dependencies are detected in dynamic values."""

    pass


class TemplateResolutionError(Exception):
    """Raised when template rendering fails."""

    pass


class DependencyResolutionError(Exception):
    """Raised when dependency resolution fails."""

    pass


# Phase 1: Dependency Analysis Functions


def _extract_template_dependencies(template_string: str) -> set[str]:
    """
    Extract variable dependencies from a Jinja2 template string.

    Args:
        template_string: The template string to analyze

    Returns:
        set of variable names that this template depends on
    """
    if not isinstance(template_string, str):
        return set()

    # Pattern to match Jinja2 variables: {{variable_name}}, {{variable.attribute}}, {{variable|filter}}
    # This handles basic variables but not complex expressions
    pattern = r'\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*[^}]*\}\}'
    matches = re.findall(pattern, template_string)

    return set(matches)


def _build_dependency_graph(dynamic_values: dict[str, Any]) -> dict[str, set[str]]:
    """
    Build a dependency graph of dynamic values.

    Args:
        dynamic_values: dictionary of dynamic values to analyze

    Returns:
        dictionary mapping each key to its set of dependencies
    """
    dependency_graph = {}

    for key, value in dynamic_values.items():
        if isinstance(value, str):
            dependencies = _extract_template_dependencies(value)
            # Only include dependencies that exist in dynamic_values
            valid_dependencies = dependencies.intersection(dynamic_values.keys())
            dependency_graph[key] = valid_dependencies
        else:
            # Non-string values have no dependencies
            dependency_graph[key] = set()

    return dependency_graph


def _detect_circular_dependencies(dependency_graph: dict[str, set[str]]) -> list[list[str]]:
    """
    Detect circular dependencies in the dependency graph using DFS.

    Args:
        dependency_graph: Dependency graph from _build_dependency_graph

    Returns:
        list of circular dependency chains (each chain is a list of keys)
    """
    visited = set()
    recursion_stack = set()
    cycles = []

    def dfs(node: str, path: list[str]) -> bool:
        if node in recursion_stack:
            # Found a cycle
            cycle_start = path.index(node)
            cycle = path[cycle_start:] + [node]
            cycles.append(cycle)
            return True

        if node in visited:
            return False

        visited.add(node)
        recursion_stack.add(node)

        for dependency in dependency_graph.get(node, set()):
            if dependency in dependency_graph and dfs(dependency, path + [node]):
                return True

        recursion_stack.remove(node)
        return False

    for start_node in dependency_graph:
        if start_node not in visited:
            dfs(start_node, [])

    return cycles


# Phase 2: Resolution Order Determination


def _topological_sort(dependency_graph: dict[str, set[str]]) -> list[str]:
    """
    Perform topological sort to determine resolution order using Kahn's algorithm.

    Args:
        dependency_graph: Dependency graph from _build_dependency_graph

    Returns:
        Ordered list of keys for resolution (dependencies first)

    Raises:
        CircularDependencyError: If circular dependencies prevent sorting
    """
    # Calculate in-degree for each node (how many dependencies it has)
    in_degree = {node: len(dependency_graph[node]) for node in dependency_graph}

    # Start with nodes that have no dependencies
    queue = deque([node for node, degree in in_degree.items() if degree == 0])
    sorted_order = []

    while queue:
        current = queue.popleft()
        sorted_order.append(current)

        # For each node that depends on current, reduce its in-degree
        for node in dependency_graph:
            if current in dependency_graph[node]:
                in_degree[node] -= 1
                if in_degree[node] == 0:
                    queue.append(node)

    # Check if all nodes were processed (no cycles)
    if len(sorted_order) != len(dependency_graph):
        remaining_nodes = set(dependency_graph.keys()) - set(sorted_order)
        raise CircularDependencyError(f"Circular dependency detected among nodes: {remaining_nodes}")

    return sorted_order


# Phase 3: Recursive Resolution Engine


class ResolutionContext:
    """Manages the resolution state and prevents infinite recursion."""

    def __init__(self, max_depth: int = 10):
        self.resolving_keys: set[str] = set()
        self.resolved_values: dict[str, Any] = {}
        self.max_depth = max_depth
        self.current_depth = 0

    def is_resolving(self, key: str) -> bool:
        """Check if a key is currently being resolved (prevents cycles)."""
        return key in self.resolving_keys

    def start_resolving(self, key: str) -> None:
        """Mark a key as currently being resolved."""
        self.resolving_keys.add(key)
        self.current_depth += 1

        if self.current_depth > self.max_depth:
            raise DependencyResolutionError(f"Maximum recursion depth {self.max_depth} exceeded")

    def finish_resolving(self, key: str, value: Any) -> None:
        """Mark a key as resolved and store its value."""
        self.resolving_keys.discard(key)
        self.resolved_values[key] = value
        self.current_depth -= 1

    def is_resolved(self, key: str) -> bool:
        """Check if a key has already been resolved."""
        return key in self.resolved_values

    def get_resolved_value(self, key: str) -> Any:
        """Get the resolved value for a key."""
        return self.resolved_values.get(key)


def _resolve_template_with_context(template_str: str, context: dict[str, Any], key: str) -> str:
    """
    Resolve individual template with current resolution context.

    Args:
        template_str: Template string to resolve
        context: Current resolved context
        key: Key being resolved (for error messages)

    Returns:
        Resolved template string

    Raises:
        TemplateResolutionError: If template rendering fails
    """
    try:
        template = Template(template_str)
        rendered_value = template.render(context)
        return rendered_value
    except Exception as e:
        raise TemplateResolutionError(f"Failed to render template for key '{key}': {str(e)}")


def _prepare_resolution_order(dynamic_values: dict[str, Any]) -> list[str]:
    """Prepare the resolution order for dynamic values."""
    if not dynamic_values:
        return []

    dependency_graph = _build_dependency_graph(dynamic_values)

    circular_deps = _detect_circular_dependencies(dependency_graph)
    if circular_deps:
        logger.warning(f"Circular dependencies detected: {circular_deps}")

    try:
        return _topological_sort(dependency_graph)
    except CircularDependencyError:
        logger.warning("Unable to determine resolution order due to circular dependencies, using partial resolution")
        return list(dynamic_values.keys())


def _resolve_value(key: str, dynamic_values: dict[str, Any], context: ResolutionContext) -> Any:
    """Resolve a single value, handling templates."""
    value = dynamic_values[key]
    if isinstance(value, str):
        return _resolve_template_with_context(value, context.resolved_values, key)
    return value


def _resolve_single_key(key: str, dynamic_values: dict[str, Any], context: ResolutionContext):
    """Resolve a single key within the resolution context."""
    if context.is_resolved(key):
        return

    if context.is_resolving(key):
        logger.warning(f"Skipping circular reference for key '{key}'")
        context.resolved_values[key] = dynamic_values[key]  # Use original value
        return

    try:
        context.start_resolving(key)
        resolved_value = _resolve_value(key, dynamic_values, context)
        context.finish_resolving(key, resolved_value)
    except (TemplateResolutionError, DependencyResolutionError) as e:
        logger.warning(f"Failed to resolve key '{key}': {e}")
        context.finish_resolving(key, dynamic_values[key])
    except Exception as e:
        logger.error(f"Unexpected error resolving key '{key}': {e}")
        context.finish_resolving(key, dynamic_values[key])


def _resolve_dynamic_values_recursively(dynamic_values: dict[str, Any]) -> dict[str, Any]:
    """
    Resolve dynamic values recursively in dependency order.

    Args:
        dynamic_values: dictionary of dynamic values to resolve

    Returns:
        dictionary with all expressions resolved

    Raises:
        CircularDependencyError: If circular dependencies are detected
        DependencyResolutionError: If resolution fails
    """
    if not dynamic_values:
        return {}

    resolution_order = _prepare_resolution_order(dynamic_values)
    context = ResolutionContext(max_depth=MAX_RECURSION_DEPTH)

    for key in resolution_order:
        _resolve_single_key(key, dynamic_values, context)

    return context.resolved_values


def process_string(
    source: str,
    context: Optional[list[Any] | dict[str, str]],
    initial_dynamic_vals: dict | None = None,
    enable_recursive_resolution: bool | None = None,
) -> str:
    """
    Process a string with dynamic context resolution.

    This is a convenience wrapper around process_values for single string processing.

    Args:
        source: The string to process (may contain templates)
        context: Optional context - can be:
                 - list[Any]: Legacy format, will be consolidated and resolved
                 - dict[str, str]: Pre-resolved context (optimized path)
        initial_dynamic_vals: Optional initial dynamic values to use as base context
        enable_recursive_resolution: Override for recursive resolution feature flag

    Returns:
        Processed string with resolved templates
    """
    return (
        process_values(
            {"string_value": source},
            context,
            initial_dynamic_vals=initial_dynamic_vals,
            enable_recursive_resolution=enable_recursive_resolution,
        ).get("string_value", source)
        if context or initial_dynamic_vals
        else source
    )


# Configuration for recursive resolution
ENABLE_RECURSIVE_RESOLUTION = True
MAX_RECURSION_DEPTH = 10


def _consolidate_context(context: list[Any] | dict[str, Any] | None, initial_dynamic_vals: dict | None = None) -> dict:
    """
    Consolidate a list of context dictionaries into a single dictionary.

    Args:
        context: Optional list of context dictionaries to consolidate
        initial_dynamic_vals: Optional initial dynamic values to use as base context

    Returns:
        Consolidated dictionary with all context values
    """
    dynamic_values = {}
    if initial_dynamic_vals:
        dynamic_values.update(initial_dynamic_vals)
    if isinstance(context, dict):
        dynamic_values.update(context)
    elif isinstance(context, list):
        for context_item in context:
            if isinstance(context_item, dict):
                dynamic_values.update(context_item)
    return dynamic_values


def _serialize_complex_objects(dynamic_values: dict) -> None:
    """Serialize dictionary values in dynamic_values to JSON strings in-place."""
    for msg_key, msg_value in dynamic_values.items():
        if isinstance(msg_value, dict):
            dynamic_values[msg_key] = json.dumps(msg_value, ensure_ascii=False)


def _recursively_resolve_values(dynamic_values: dict, use_recursive: bool) -> dict:
    """Recursively resolve dynamic values if enabled."""
    if not use_recursive:
        return dynamic_values

    try:
        return _resolve_dynamic_values_recursively(dynamic_values)
    except (CircularDependencyError, DependencyResolutionError) as e:
        logger.warning(f"Dynamic value resolution failed: {e}")
    except Exception as e:
        logger.error(f"Unexpected error during recursive resolution: {e}")

    return dynamic_values  # Return unresolved values on failure


def _resolve_source_values(source_values: dict, resolved_context: dict) -> dict:
    """Resolve source values using the resolved context."""
    processed_values = {}
    for key, value in source_values.items():
        try:
            processed_value = _resolve_dynamic_values(key, value, resolved_context)
            processed_values[key] = processed_value
        except Exception as e:
            logger.error(f"Error resolving tool arg value for key '{key}': {str(e)}")
            processed_values[key] = value  # Keep original value on error
    return processed_values


def process_values(
    source_values: dict,
    context: Optional[list[Any] | dict[str, str]],
    initial_dynamic_vals: dict | None = None,
    enable_recursive_resolution: bool | None = None,
) -> dict:
    """
    Process source values with dynamic context resolution.

    Enhanced version that supports both legacy list context and new pre-resolved
    dict context for performance optimization.

    Args:
        source_values: dictionary of values to process (may contain templates)
        context: Optional context - can be:
                 - list[Any]: Legacy format, will be consolidated and resolved
                 - dict[str, str]: Pre-resolved context (optimized path)
        initial_dynamic_vals: Optional initial dynamic values to use as base context
        enable_recursive_resolution: Override for recursive resolution feature flag

    Returns:
        dictionary with resolved values
    """
    if not context and not initial_dynamic_vals:
        return copy.deepcopy(source_values)

    dynamic_values = _consolidate_context(context, initial_dynamic_vals)
    # _serialize_complex_objects(dynamic_values)

    use_recursive = (
        enable_recursive_resolution if enable_recursive_resolution is not None else ENABLE_RECURSIVE_RESOLUTION
    )
    resolved_dynamic_values = _recursively_resolve_values(dynamic_values, use_recursive)

    return _resolve_source_values(source_values, resolved_dynamic_values)


def _resolve_dynamic_values(key: str, value: Any, dynamic_values: dict) -> Any:
    # If value is empty/None and we have input message, try to get value from input
    if dynamic_values:
        parsed_value = dynamic_values.get(key)
        if parsed_value is not None:
            return parsed_value

    # If value is not a string, return as-is
    if not isinstance(value, str):
        return value

    # Try to render as Jinja2 template
    try:
        template = Template(value)
        rendered_value = template.render(dynamic_values)

        return rendered_value
    except Exception as e:
        logger.warning(f"Failed to render template for key '{key}': {str(e)}")
        return value
