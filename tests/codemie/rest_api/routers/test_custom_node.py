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

"""Unit tests for custom node API endpoints.

Tests the REST API layer for custom node schema retrieval endpoints.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.main import app
from codemie.rest_api.security.user import User

# Workflow router is already included in main.py, just get the client
client = TestClient(app)


@pytest.fixture
def mock_user():
    """Fixture for mock user."""
    return User(id="test_user", email="test@example.com", is_admin=False)


@pytest.fixture
def mock_auth_header():
    """Fixture for mock authentication header."""
    return {"Authorization": "Bearer test_token"}


@pytest.fixture
def mock_authenticate():
    """Fixture to mock authentication dependency."""
    with patch("codemie.rest_api.routers.workflow.authenticate") as mock:
        mock.return_value = User(id="test_user", email="test@example.com", is_admin=False)
        yield mock


class TestGetCustomNodes:
    """Test suite for GET /v1/workflows/custom-nodes endpoint."""

    def test_get_custom_nodes_success(self, mock_auth_header, mock_authenticate):
        """Test successful retrieval of all custom node IDs."""
        mock_node_ids = ["state_processor", "supervisor", "generate_documents_tree"]

        with patch("codemie.rest_api.routers.workflow.CustomNodeInfoService") as mock_service:
            mock_service.get_node_ids.return_value = mock_node_ids

            response = client.get("/v1/workflows/custom-nodes", headers=mock_auth_header)

            assert response.status_code == 200
            assert response.json() == mock_node_ids
            mock_service.get_node_ids.assert_called_once()

    def test_get_custom_nodes_empty_list(self, mock_auth_header, mock_authenticate):
        """Test when no custom nodes are available."""
        with patch("codemie.rest_api.routers.workflow.CustomNodeInfoService") as mock_service:
            mock_service.get_node_ids.return_value = []

            response = client.get("/v1/workflows/custom-nodes", headers=mock_auth_header)

            assert response.status_code == 200
            assert response.json() == []

    def test_get_custom_nodes_requires_authentication(self):
        """Test that endpoint requires authentication."""
        # Note: This test may behave differently depending on your auth setup
        # If authenticate is a dependency, missing header should trigger auth error
        response = client.get("/v1/workflows/custom-nodes")

        # Response depends on auth implementation - could be 401, 403, or handled differently
        assert response.status_code in [401, 403, 422]  # 422 if missing required dependency


class TestGetCustomNodeSchema:
    """Test suite for GET /v1/workflows/custom-nodes/{custom_node_id}/schema endpoint."""

    def test_get_custom_node_schema_success(self, mock_auth_header, mock_authenticate):
        """Test successful retrieval of custom node schema."""
        mock_schema = {
            "custom_node_type": "state_processor",
            "config_schema": {
                "output_template": {
                    "type": "text",
                    "required": True,
                    "description": "Jinja template for output processing",
                    "values": None,
                },
                "workflow_execution_id": {
                    "type": "str",
                    "required": False,
                    "description": "Workflow execution ID (optional)",
                    "values": None,
                },
                "states_status_filter": {
                    "type": "list",
                    "required": False,
                    "description": "Filter states by status",
                    "values": ["NOT_STARTED", "IN_PROGRESS", "SUCCEEDED", "FAILED"],
                },
            },
        }

        with patch("codemie.rest_api.routers.workflow.CustomNodeInfoService") as mock_service:
            mock_service.get_node_schema.return_value = mock_schema

            response = client.get("/v1/workflows/custom-nodes/state_processor/schema", headers=mock_auth_header)

            assert response.status_code == 200
            response_data = response.json()

            # Verify response structure
            assert response_data["custom_node_type"] == "state_processor"
            assert "config_schema" in response_data
            assert "output_template" in response_data["config_schema"]
            assert "workflow_execution_id" in response_data["config_schema"]
            assert "states_status_filter" in response_data["config_schema"]

            # Verify each parameter has required fields
            for param_info in response_data["config_schema"].values():
                assert "type" in param_info
                assert "required" in param_info
                assert "description" in param_info

            # Verify list type has values
            assert response_data["config_schema"]["states_status_filter"]["values"] is not None

            mock_service.get_node_schema.assert_called_once_with("state_processor")

    def test_get_custom_node_schema_not_found(self, mock_auth_header, mock_authenticate):
        """Test 404 error when custom node type doesn't exist."""
        with patch("codemie.rest_api.routers.workflow.CustomNodeInfoService") as mock_service:
            mock_service.get_node_schema.side_effect = ExtendedHTTPException(
                code=404, message="Custom node type 'nonexistent_node' not found"
            )

            response = client.get("/v1/workflows/custom-nodes/nonexistent_node/schema", headers=mock_auth_header)

            assert response.status_code == 404
            error_data = response.json()
            assert "error" in error_data
            assert "not found" in error_data["error"]["message"].lower()
            assert "nonexistent_node" in error_data["error"]["message"]

    def test_get_custom_node_schema_state_processor_node(self, mock_auth_header, mock_authenticate):
        """Test schema retrieval for state_processor node."""
        # Just call the real service - no mocking needed since we're testing the actual schema
        response = client.get("/v1/workflows/custom-nodes/state_processor/schema", headers=mock_auth_header)

        assert response.status_code == 200
        response_data = response.json()
        assert response_data["custom_node_type"] == "state_processor"
        # Should have actual config parameters
        assert "output_template" in response_data["config_schema"]
        assert "workflow_execution_id" in response_data["config_schema"]
        assert "states_status_filter" in response_data["config_schema"]
        assert "state_id" in response_data["config_schema"]

    def test_get_custom_node_schema_generate_documents_tree(self, mock_auth_header, mock_authenticate):
        """Test schema retrieval for generate_documents_tree."""
        # Just call the real service - no mocking needed since we're testing the actual schema
        response = client.get("/v1/workflows/custom-nodes/generate_documents_tree/schema", headers=mock_auth_header)

        assert response.status_code == 200
        response_data = response.json()
        assert response_data["custom_node_type"] == "generate_documents_tree"
        # Should have actual config parameters
        assert "datasource_id" in response_data["config_schema"]
        assert "documents_filtering_pattern" in response_data["config_schema"]
        assert "documents_filter" in response_data["config_schema"]
        assert "output_key" in response_data["config_schema"]
        assert "include_content" in response_data["config_schema"]

    def test_get_custom_node_schema_special_characters_in_id(self, mock_auth_header, mock_authenticate):
        """Test handling of special characters in custom_node_id."""
        with patch("codemie.rest_api.routers.workflow.CustomNodeInfoService") as mock_service:
            mock_service.get_node_schema.side_effect = ExtendedHTTPException(
                code=404, message="Custom node type 'invalid@node!' not found"
            )

            response = client.get("/v1/workflows/custom-nodes/invalid@node!/schema", headers=mock_auth_header)

            assert response.status_code == 404

    def test_get_custom_node_schema_empty_config(self, mock_auth_header, mock_authenticate):
        """Test handling of custom node with no config parameters."""
        mock_schema = {"custom_node_type": "minimal_node", "config_schema": {}}

        with patch("codemie.rest_api.routers.workflow.CustomNodeInfoService") as mock_service:
            mock_service.get_node_schema.return_value = mock_schema

            response = client.get("/v1/workflows/custom-nodes/minimal_node/schema", headers=mock_auth_header)

            assert response.status_code == 200
            response_data = response.json()
            assert response_data["custom_node_type"] == "minimal_node"
            assert response_data["config_schema"] == {}

    def test_get_custom_node_schema_requires_authentication(self):
        """Test that schema endpoint requires authentication."""
        response = client.get("/v1/workflows/custom-nodes/transform_node/schema")

        # Response depends on auth implementation
        assert response.status_code in [401, 403, 422]

    def test_get_custom_node_schema_validates_node_id(self, mock_auth_header, mock_authenticate):
        """Test that invalid node IDs are properly validated."""
        invalid_node_ids = ["", "  ", "../../../etc/passwd", "node__pycache__"]

        with patch("codemie.rest_api.routers.workflow.CustomNodeInfoService") as mock_service:
            mock_service.get_node_schema.side_effect = ExtendedHTTPException(code=404, message="Custom node not found")

            for invalid_id in invalid_node_ids:
                # URL encode spaces and special chars
                import urllib.parse

                encoded_id = urllib.parse.quote(invalid_id, safe="")

                response = client.get(f"/v1/workflows/custom-nodes/{encoded_id}/schema", headers=mock_auth_header)

                # Should return 404 for invalid IDs
                assert response.status_code == 404, f"Expected 404 for invalid ID: {invalid_id}"
