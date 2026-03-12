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

"""Service for extracting and providing custom node configuration schema information.

This module provides a simplified registry-based approach to discovering custom node types
and their hardcoded configuration parameter schemas.
"""

from typing import Any

from fastapi import status

from codemie.configs import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.workflow_models import CustomNodeConfigField, CustomNodeSchemaResponse
from codemie.workflows.nodes.generate_documents_tree import GenerateDocumentsTree
from codemie.workflows.nodes.result_finalizer_node import ResultFinalizerNode
from codemie.workflows.nodes.state_processor_node import StateProcessorNode
from codemie.workflows.nodes.summarize_conversation_node import SummarizeConversationNode
from codemie.workflows.nodes.supervisor_node import SupervisorNode


NODE_REGISTRY: dict[str, type] = {
    "generate_documents_tree": GenerateDocumentsTree,
    "result_finalizer": ResultFinalizerNode,
    "state_processor": StateProcessorNode,
    "summarize_conversation": SummarizeConversationNode,
    "supervisor": SupervisorNode,
}


class CustomNodeInfoService:
    """Service for extracting schema information from custom workflow nodes using a registry."""

    @classmethod
    def get_node_ids(cls) -> list[str]:
        """Get list of all available custom node IDs from the registry.

        Returns:
            List of custom node IDs (e.g., ['transform_node', 'state_processor_node'])
        """
        node_ids = sorted(NODE_REGISTRY.keys())
        logger.debug(f"Retrieved {len(node_ids)} custom node types from registry: {node_ids}")
        return node_ids

    @classmethod
    def get_node_schema(cls, custom_node_id: str) -> CustomNodeSchemaResponse:
        """Extract configuration schema for a specific custom node type.

        Args:
            custom_node_id: The custom node type identifier (e.g., 'transform_node')

        Returns:
            CustomNodeSchemaResponse with formatted config schema

        Raises:
            ExtendedHTTPException: If the custom node type doesn't exist (404)
        """
        if custom_node_id not in NODE_REGISTRY:
            available_nodes = cls.get_node_ids()
            logger.warning(f"Custom node not found: {custom_node_id}. Available: {available_nodes}")
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message=f"Custom node type '{custom_node_id}' not found",
                details=f"Available custom node types: {', '.join(available_nodes)}",
            )

        logger.info(f"Extracting schema for custom node: {custom_node_id}")

        node_class = NODE_REGISTRY[custom_node_id]
        raw_schema = cls._extract_config_schema_from_node(node_class, custom_node_id)

        formatted_schema = {}
        for field_name, field_info in raw_schema.items():
            formatted_schema[field_name] = CustomNodeConfigField(
                type=field_info["type"],
                required=field_info["required"],
                description=field_info["description"],
                values=field_info.get("values"),  # Optional values for list types
            )

        return CustomNodeSchemaResponse(custom_node_type=custom_node_id, config_schema=formatted_schema)

    @classmethod
    def _extract_config_schema_from_node(cls, node_class: type, node_id: str) -> dict[str, dict[str, Any]]:
        """Extract configuration schema for a node from its config_schema class attribute.

        Args:
            node_class: The node class with config_schema attribute
            node_id: The node identifier for logging

        Returns:
            Dictionary mapping config parameter names to their schema info
        """
        if not hasattr(node_class, "config_schema"):
            logger.warning(f"Node class {node_class.__name__} has no config_schema attribute")
            return {}

        config_schema_class = node_class.config_schema
        schema = {}

        # Extract fields from Pydantic model
        for field_name, field_info in config_schema_class.model_fields.items():
            json_schema_extra = field_info.json_schema_extra or {}
            schema[field_name] = {
                "type": json_schema_extra.get("type", "str"),
                "required": json_schema_extra.get("required", False),
                "description": json_schema_extra.get("description", f"Parameter '{field_name}'"),
                "values": json_schema_extra.get("values"),
            }

        logger.debug(f"Retrieved {len(schema)} config parameters for {node_id}")
        return schema
