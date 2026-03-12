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

"""Unit tests for the callbacks router."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def app():
    """Create a FastAPI app with the callbacks router."""
    from codemie.rest_api.routers.callbacks import router

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client(app):
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def mock_index_with_provider_fields():
    """Create a mock IndexInfo with provider_fields and OTP."""
    mock_index = MagicMock()
    mock_index.id = "test-index-123"
    mock_index.repo_name = "test-datasource"
    mock_index.provider_fields = MagicMock()
    mock_index.provider_fields.otp = "test-otp-token"
    mock_index.provider_fields.provider_id = "provider-123"
    return mock_index


@pytest.fixture
def mock_index_without_provider_fields():
    """Create a mock IndexInfo without provider_fields."""
    mock_index = MagicMock()
    mock_index.id = "test-index-456"
    mock_index.repo_name = "test-datasource-no-provider"
    mock_index.provider_fields = None
    return mock_index


class TestDatasourceCallbackSuccess:
    """Tests for successful datasource callback scenarios."""

    @patch('codemie.rest_api.routers.callbacks.IndexInfo.find_by_id')
    def test_callback_completed_status(self, mock_find_by_id, client, mock_index_with_provider_fields):
        """Test callback with 'Completed' status."""
        # Arrange
        mock_find_by_id.return_value = mock_index_with_provider_fields
        index_id = "test-index-123"
        payload = {"status": "Completed"}
        headers = {"X-Callback-OTP": "test-otp-token"}

        # Act
        response = client.post(f"/v1/callbacks/index/{index_id}", json=payload, headers=headers)

        # Assert
        assert response.status_code == 200
        assert response.json() == {"status": "success"}
        mock_index_with_provider_fields.complete_progress.assert_called_once()
        mock_index_with_provider_fields.reset_otp.assert_called_once()
        mock_index_with_provider_fields.set_error.assert_not_called()

    @patch('codemie.rest_api.routers.callbacks.IndexInfo.find_by_id')
    def test_callback_cancelled_status(self, mock_find_by_id, client, mock_index_with_provider_fields):
        """Test callback with 'Cancelled' status."""
        # Arrange
        mock_find_by_id.return_value = mock_index_with_provider_fields
        index_id = "test-index-123"
        payload = {"status": "Cancelled"}
        headers = {"X-Callback-OTP": "test-otp-token"}

        # Act
        response = client.post(f"/v1/callbacks/index/{index_id}", json=payload, headers=headers)

        # Assert
        assert response.status_code == 200
        assert response.json() == {"status": "success"}
        mock_index_with_provider_fields.set_error.assert_called_once_with("Process was canceled by external provider")
        mock_index_with_provider_fields.reset_otp.assert_called_once()
        mock_index_with_provider_fields.complete_progress.assert_not_called()

    @patch('codemie.rest_api.routers.callbacks.IndexInfo.find_by_id')
    def test_callback_error_status_with_error_message(self, mock_find_by_id, client, mock_index_with_provider_fields):
        """Test callback with 'Error' status and custom error message."""
        # Arrange
        mock_find_by_id.return_value = mock_index_with_provider_fields
        index_id = "test-index-123"
        error_message = "Connection timeout error"
        payload = {"status": "Error", "errors": error_message}
        headers = {"X-Callback-OTP": "test-otp-token"}

        # Act
        response = client.post(f"/v1/callbacks/index/{index_id}", json=payload, headers=headers)

        # Assert
        assert response.status_code == 200
        assert response.json() == {"status": "success"}
        mock_index_with_provider_fields.set_error.assert_called_once_with(error_message)
        mock_index_with_provider_fields.reset_otp.assert_called_once()

    @patch('codemie.rest_api.routers.callbacks.IndexInfo.find_by_id')
    def test_callback_error_status_without_error_message(
        self, mock_find_by_id, client, mock_index_with_provider_fields
    ):
        """Test callback with 'Error' status and no custom error message."""
        # Arrange
        mock_find_by_id.return_value = mock_index_with_provider_fields
        index_id = "test-index-123"
        payload = {"status": "Error"}
        headers = {"X-Callback-OTP": "test-otp-token"}

        # Act
        response = client.post(f"/v1/callbacks/index/{index_id}", json=payload, headers=headers)

        # Assert
        assert response.status_code == 200
        assert response.json() == {"status": "success"}
        mock_index_with_provider_fields.set_error.assert_called_once_with("Unknown external provider error")
        mock_index_with_provider_fields.reset_otp.assert_called_once()


class TestDatasourceCallbackValidation:
    """Tests for callback validation and error scenarios."""

    @patch('codemie.rest_api.routers.callbacks.IndexInfo.find_by_id')
    def test_callback_index_not_found(self, mock_find_by_id, client):
        """Test callback with non-existent index ID."""
        # Arrange
        mock_find_by_id.return_value = None
        index_id = "non-existent-index"
        payload = {"status": "Completed"}
        headers = {"X-Callback-OTP": "some-otp"}

        # Act
        response = client.post(f"/v1/callbacks/index/{index_id}", json=payload, headers=headers)

        # Assert
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @patch('codemie.rest_api.routers.callbacks.IndexInfo.find_by_id')
    def test_callback_index_without_provider_fields(self, mock_find_by_id, client, mock_index_without_provider_fields):
        """Test callback with index that has no provider_fields."""
        # Arrange
        mock_find_by_id.return_value = mock_index_without_provider_fields
        index_id = "test-index-456"
        payload = {"status": "Completed"}
        headers = {"X-Callback-OTP": "some-otp"}

        # Act
        response = client.post(f"/v1/callbacks/index/{index_id}", json=payload, headers=headers)

        # Assert
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @patch('codemie.rest_api.routers.callbacks.IndexInfo.find_by_id')
    def test_callback_invalid_otp(self, mock_find_by_id, client, mock_index_with_provider_fields):
        """Test callback with invalid OTP."""
        # Arrange
        mock_find_by_id.return_value = mock_index_with_provider_fields
        index_id = "test-index-123"
        payload = {"status": "Completed"}
        headers = {"X-Callback-OTP": "wrong-otp-token"}

        # Act
        response = client.post(f"/v1/callbacks/index/{index_id}", json=payload, headers=headers)

        # Assert
        assert response.status_code == 403
        assert "Invalid or expired OTP" in response.json()["detail"]
        mock_index_with_provider_fields.complete_progress.assert_not_called()
        mock_index_with_provider_fields.reset_otp.assert_not_called()

    @patch('codemie.rest_api.routers.callbacks.IndexInfo.find_by_id')
    def test_callback_missing_otp_header(self, mock_find_by_id, client, mock_index_with_provider_fields):
        """Test callback without OTP header."""
        # Arrange
        mock_find_by_id.return_value = mock_index_with_provider_fields
        index_id = "test-index-123"
        payload = {"status": "Completed"}

        # Act
        response = client.post(f"/v1/callbacks/index/{index_id}", json=payload)

        # Assert
        assert response.status_code == 403
        assert "Invalid or expired OTP" in response.json()["detail"]

    @patch('codemie.rest_api.routers.callbacks.IndexInfo.find_by_id')
    def test_callback_missing_status(self, mock_find_by_id, client, mock_index_with_provider_fields):
        """Test callback without status field in payload."""
        # Arrange
        mock_find_by_id.return_value = mock_index_with_provider_fields
        index_id = "test-index-123"
        payload = {"some_other_field": "value"}
        headers = {"X-Callback-OTP": "test-otp-token"}

        # Act
        response = client.post(f"/v1/callbacks/index/{index_id}", json=payload, headers=headers)

        # Assert
        assert response.status_code == 400
        assert "Missing status" in response.json()["detail"]
        mock_index_with_provider_fields.reset_otp.assert_not_called()

    @patch('codemie.rest_api.routers.callbacks.IndexInfo.find_by_id')
    def test_callback_invalid_status(self, mock_find_by_id, client, mock_index_with_provider_fields):
        """Test callback with invalid status value."""
        # Arrange
        mock_find_by_id.return_value = mock_index_with_provider_fields
        index_id = "test-index-123"
        payload = {"status": "InvalidStatus"}
        headers = {"X-Callback-OTP": "test-otp-token"}

        # Act
        response = client.post(f"/v1/callbacks/index/{index_id}", json=payload, headers=headers)

        # Assert
        assert response.status_code == 400
        assert "Invalid status" in response.json()["detail"]
        assert "InvalidStatus" in response.json()["detail"]
        mock_index_with_provider_fields.reset_otp.assert_not_called()

    @patch('codemie.rest_api.routers.callbacks.IndexInfo.find_by_id')
    def test_callback_empty_payload(self, mock_find_by_id, client, mock_index_with_provider_fields):
        """Test callback with empty payload."""
        # Arrange
        mock_find_by_id.return_value = mock_index_with_provider_fields
        index_id = "test-index-123"
        payload = {}
        headers = {"X-Callback-OTP": "test-otp-token"}

        # Act
        response = client.post(f"/v1/callbacks/index/{index_id}", json=payload, headers=headers)

        # Assert
        assert response.status_code == 400
        assert "Missing status" in response.json()["detail"]


class TestDatasourceCallbackEdgeCases:
    """Tests for edge cases and special scenarios."""

    @patch('codemie.rest_api.routers.callbacks.IndexInfo.find_by_id')
    def test_callback_with_additional_payload_fields(self, mock_find_by_id, client, mock_index_with_provider_fields):
        """Test callback with additional fields in payload (should be ignored)."""
        # Arrange
        mock_find_by_id.return_value = mock_index_with_provider_fields
        index_id = "test-index-123"
        payload = {
            "status": "Completed",
            "extra_field": "extra_value",
            "another_field": 123,
        }
        headers = {"X-Callback-OTP": "test-otp-token"}

        # Act
        response = client.post(f"/v1/callbacks/index/{index_id}", json=payload, headers=headers)

        # Assert
        assert response.status_code == 200
        assert response.json() == {"status": "success"}
        mock_index_with_provider_fields.complete_progress.assert_called_once()

    @patch('codemie.rest_api.routers.callbacks.IndexInfo.find_by_id')
    def test_callback_status_case_sensitivity(self, mock_find_by_id, client, mock_index_with_provider_fields):
        """Test callback with different case in status value."""
        # Arrange
        mock_find_by_id.return_value = mock_index_with_provider_fields
        index_id = "test-index-123"

        # Test lowercase
        payload_lower = {"status": "completed"}
        headers = {"X-Callback-OTP": "test-otp-token"}

        # Act
        response = client.post(f"/v1/callbacks/index/{index_id}", json=payload_lower, headers=headers)

        # Assert
        # Status check is case-sensitive, so "completed" != "Completed"
        assert response.status_code == 400
        assert "Invalid status" in response.json()["detail"]

    @patch('codemie.rest_api.routers.callbacks.IndexInfo.find_by_id')
    def test_callback_null_status(self, mock_find_by_id, client, mock_index_with_provider_fields):
        """Test callback with null status value."""
        # Arrange
        mock_find_by_id.return_value = mock_index_with_provider_fields
        index_id = "test-index-123"
        payload = {"status": None}
        headers = {"X-Callback-OTP": "test-otp-token"}

        # Act
        response = client.post(f"/v1/callbacks/index/{index_id}", json=payload, headers=headers)

        # Assert
        assert response.status_code == 400
        assert "Missing status" in response.json()["detail"]

    @patch('codemie.rest_api.routers.callbacks.IndexInfo.find_by_id')
    def test_callback_with_empty_errors_field(self, mock_find_by_id, client, mock_index_with_provider_fields):
        """Test callback with Error status and empty errors field."""
        # Arrange
        mock_find_by_id.return_value = mock_index_with_provider_fields
        index_id = "test-index-123"
        payload = {"status": "Error", "errors": ""}
        headers = {"X-Callback-OTP": "test-otp-token"}

        # Act
        response = client.post(f"/v1/callbacks/index/{index_id}", json=payload, headers=headers)

        # Assert
        assert response.status_code == 200
        # payload.get("errors", default) returns empty string if key exists with empty value
        mock_index_with_provider_fields.set_error.assert_called_once_with("")

    @patch('codemie.rest_api.routers.callbacks.IndexInfo.find_by_id')
    @patch('codemie.rest_api.routers.callbacks.logger')
    def test_callback_logs_received_payload(
        self, mock_logger, mock_find_by_id, client, mock_index_with_provider_fields
    ):
        """Test that callback logs the received payload."""
        # Arrange
        mock_find_by_id.return_value = mock_index_with_provider_fields
        index_id = "test-index-123"
        payload = {"status": "Completed"}
        headers = {"X-Callback-OTP": "test-otp-token"}

        # Act
        response = client.post(f"/v1/callbacks/index/{index_id}", json=payload, headers=headers)

        # Assert
        assert response.status_code == 200
        mock_logger.info.assert_called_once()
        log_call_args = mock_logger.info.call_args[0][0]
        assert index_id in log_call_args
        assert "Received callback" in log_call_args


class TestDatasourceCallbackIntegration:
    """Integration-style tests for complete callback workflows."""

    @patch('codemie.rest_api.routers.callbacks.IndexInfo.find_by_id')
    def test_complete_workflow_success(self, mock_find_by_id, client, mock_index_with_provider_fields):
        """Test complete successful callback workflow."""
        # Arrange
        mock_find_by_id.return_value = mock_index_with_provider_fields
        index_id = "test-index-123"
        payload = {"status": "Completed"}
        headers = {"X-Callback-OTP": "test-otp-token"}

        # Act
        response = client.post(f"/v1/callbacks/index/{index_id}", json=payload, headers=headers)

        # Assert - Verify all expected methods were called in order
        assert response.status_code == 200
        assert mock_find_by_id.call_count == 1
        mock_index_with_provider_fields.complete_progress.assert_called_once()
        mock_index_with_provider_fields.reset_otp.assert_called_once()

    @patch('codemie.rest_api.routers.callbacks.IndexInfo.find_by_id')
    def test_complete_workflow_error(self, mock_find_by_id, client, mock_index_with_provider_fields):
        """Test complete error callback workflow."""
        # Arrange
        mock_find_by_id.return_value = mock_index_with_provider_fields
        index_id = "test-index-123"
        error_details = "Database connection failed"
        payload = {"status": "Error", "errors": error_details}
        headers = {"X-Callback-OTP": "test-otp-token"}

        # Act
        response = client.post(f"/v1/callbacks/index/{index_id}", json=payload, headers=headers)

        # Assert - Verify error handling workflow
        assert response.status_code == 200
        mock_index_with_provider_fields.set_error.assert_called_once_with(error_details)
        mock_index_with_provider_fields.reset_otp.assert_called_once()
        mock_index_with_provider_fields.complete_progress.assert_not_called()

    @patch('codemie.rest_api.routers.callbacks.IndexInfo.find_by_id')
    def test_multiple_callbacks_same_index(self, mock_find_by_id, client, mock_index_with_provider_fields):
        """Test multiple callbacks for the same index (second should fail with invalid OTP)."""
        # Arrange
        mock_find_by_id.return_value = mock_index_with_provider_fields
        index_id = "test-index-123"
        payload = {"status": "Completed"}
        headers = {"X-Callback-OTP": "test-otp-token"}

        # Act - First callback
        response1 = client.post(f"/v1/callbacks/index/{index_id}", json=payload, headers=headers)

        # After first callback, OTP should be reset (set to None or different value)
        mock_index_with_provider_fields.provider_fields.otp = None

        # Act - Second callback with same OTP (should fail)
        response2 = client.post(f"/v1/callbacks/index/{index_id}", json=payload, headers=headers)

        # Assert
        assert response1.status_code == 200
        assert response2.status_code == 403
        assert "Invalid or expired OTP" in response2.json()["detail"]
