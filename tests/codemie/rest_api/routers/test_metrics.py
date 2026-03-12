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

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from codemie.rest_api.routers.metrics import router
from codemie.rest_api.security.user import User
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService

# Create a FastAPI app and include the router
app = FastAPI()
app.include_router(router)


@pytest.fixture
def anyio_backend():
    return 'asyncio'


# Sample metrics request data
sample_metrics_request = {
    "name": "frontend_test_metric",
    "attributes": {"test_attribute": "test_value"},
}


@pytest.mark.anyio
@patch.object(BaseMonitoringService, 'send_count_metric')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_send_metric_success(mock_authenticate, mock_send_count_metric):
    # Arrange
    mock_authenticate.return_value = User(id="user123", name="testuser", username="testuser@example.com")
    mock_send_count_metric.return_value = None  # Method doesn't return anything

    # Act
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post("/v1/metrics", headers={"user-id": "user123"}, json=sample_metrics_request)

    # Assert
    assert response.status_code == 200
    response_data = response.json()
    assert response_data["success"] is True

    # Verify the BaseMonitoringService was called with the correct parameters
    expected_attributes = {
        "test_attribute": "test_value",
        "user_id": "user123",
        "user_name": "testuser",
        "user_email": "testuser@example.com",
    }
    mock_send_count_metric.assert_called_once_with(
        name=sample_metrics_request["name"],
        attributes=expected_attributes,
    )


@pytest.mark.anyio
@patch.object(BaseMonitoringService, 'send_count_metric')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_send_metric_minimal(mock_authenticate, mock_send_count_metric):
    # Arrange
    mock_authenticate.return_value = User(id="user123", name="testuser", username="testuser@example.com")
    mock_send_count_metric.return_value = None

    # Minimal request with only required fields
    minimal_request = {"name": "minimal_metric"}

    # Act
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post("/v1/metrics", headers={"user-id": "user123"}, json=minimal_request)

    # Assert
    assert response.status_code == 200
    response_data = response.json()
    assert response_data["success"] is True

    # Verify the BaseMonitoringService was called with default values
    expected_attributes = {"user_id": "user123", "user_name": "testuser", "user_email": "testuser@example.com"}
    mock_send_count_metric.assert_called_once_with(name="frontend_minimal_metric", attributes=expected_attributes)
