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

"""Unit tests for CustomNodeInfoService.

Tests the service layer logic for discovering custom nodes from the registry
and extracting their execute() method parameter schemas.
"""

from __future__ import annotations

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.workflow_models import CustomNodeSchemaResponse
from codemie.workflows.custom_node_info import CustomNodeInfoService


class TestCustomNodeInfoService:
    """Test suite for CustomNodeInfoService."""

    def test_get_node_ids_returns_list(self):
        """Test that get_node_ids returns a non-empty list."""
        node_ids = CustomNodeInfoService.get_node_ids()

        assert isinstance(node_ids, list)
        assert len(node_ids) > 0
        # Verify it's sorted
        assert node_ids == sorted(node_ids)

    def test_get_node_ids_only_includes_registered_nodes(self):
        """Test that only explicitly registered nodes are included."""
        node_ids = CustomNodeInfoService.get_node_ids()

        # Should not include base classes or system files
        assert "base_node" not in node_ids
        assert "__init__" not in node_ids
        assert "__pycache__" not in node_ids

        # Only nodes in NODE_REGISTRY should be present
        from codemie.workflows.custom_node_info.custom_node_info_service import NODE_REGISTRY

        assert set(node_ids) == set(NODE_REGISTRY.keys())

    def test_get_node_ids_includes_known_nodes(self):
        """Test that known custom node types are discovered."""
        node_ids = CustomNodeInfoService.get_node_ids()

        # These are known custom nodes that should exist
        expected_nodes = [
            "generate_documents_tree",
            "result_finalizer",
            "state_processor",
            "summarize_conversation",
            "supervisor",
        ]

        for expected_node in expected_nodes:
            assert expected_node in node_ids, f"Expected custom node '{expected_node}' not found in {node_ids}"

    def test_get_node_schema_state_processor(self):
        """Test schema extraction for state_processor node."""
        response = CustomNodeInfoService.get_node_schema("state_processor")

        assert isinstance(response, CustomNodeSchemaResponse)
        assert response.custom_node_type == "state_processor"

        # Should have config parameters
        assert "output_template" in response.config_schema
        assert "workflow_execution_id" in response.config_schema
        assert "states_status_filter" in response.config_schema
        assert "state_id" in response.config_schema

        # Verify output_template is required
        assert response.config_schema["output_template"].required is True
        assert response.config_schema["output_template"].type == "text"

        # Verify states_status_filter has values for list
        assert response.config_schema["states_status_filter"].type == "list"
        assert response.config_schema["states_status_filter"].values is not None
        assert len(response.config_schema["states_status_filter"].values) > 0

    def test_get_node_schema_generate_documents_tree(self):
        """Test schema extraction for generate_documents_tree."""
        response = CustomNodeInfoService.get_node_schema("generate_documents_tree")

        assert isinstance(response, CustomNodeSchemaResponse)
        assert response.custom_node_type == "generate_documents_tree"

        # Should have config parameters
        assert "datasource_id" in response.config_schema
        assert "output_key" in response.config_schema
        assert "include_content" in response.config_schema

        # Verify datasource_id is required
        assert response.config_schema["datasource_id"].required is True
        assert response.config_schema["datasource_id"].type == "str"

    def test_get_node_schema_supervisor(self):
        """Test schema extraction for supervisor node."""
        response = CustomNodeInfoService.get_node_schema("supervisor")

        assert isinstance(response, CustomNodeSchemaResponse)
        assert response.custom_node_type == "supervisor"

        # Supervisor has no config parameters
        assert len(response.config_schema) == 0

    def test_get_node_schema_invalid_node_raises_not_found(self):
        """Test that requesting schema for non-existent node raises ExtendedHTTPException with 404."""
        with pytest.raises(ExtendedHTTPException) as exc_info:
            CustomNodeInfoService.get_node_schema("nonexistent_node")

        assert exc_info.value.code == 404
        assert "not found" in exc_info.value.message.lower()
        assert "nonexistent_node" in exc_info.value.message

    def test_get_node_schema_empty_string_raises_not_found(self):
        """Test that empty string node ID raises ExtendedHTTPException with 404."""
        with pytest.raises(ExtendedHTTPException) as exc_info:
            CustomNodeInfoService.get_node_schema("")

        assert exc_info.value.code == 404

    def test_schema_extraction_returns_config_parameters(self):
        """Test that schema extraction returns actual config parameters."""
        response = CustomNodeInfoService.get_node_schema("state_processor")

        # Should have actual config parameters
        assert "output_template" in response.config_schema
        assert "workflow_execution_id" in response.config_schema

    def test_schema_structure_consistency_across_nodes(self):
        """Test that schema structure is consistent across different custom nodes."""
        node_ids = ["state_processor", "generate_documents_tree", "supervisor"]

        for node_id in node_ids:
            response = CustomNodeInfoService.get_node_schema(node_id)

            assert isinstance(response, CustomNodeSchemaResponse)
            assert response.custom_node_type == node_id
            assert isinstance(response.config_schema, dict)

            # Verify each parameter has CustomNodeConfigField structure
            for param_info in response.config_schema.values():
                assert hasattr(param_info, "type")
                assert hasattr(param_info, "required")
                assert hasattr(param_info, "description")
                assert hasattr(param_info, "values")  # New field (optional)
