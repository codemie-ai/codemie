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

from unittest.mock import patch, AsyncMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from codemie.rest_api.routers.webhook import router
from codemie.triggers.bindings.webhook import WebhookService

app = FastAPI()
app.include_router(router)

# Mock data
mock_request = {
    "type": "http",
    "method": "POST",
    "path": "/v1/webhooks/test-webhook-id",
    "headers": {},
    "query_string": b"",
    "client": ("testclient", 50000),
    "scope": {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/webhooks/test-webhook-id",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 8000),
        "root_path": "",
        "app": app,
        "router": router,
    },
}


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.mark.anyio
@patch.object(WebhookService, 'invoke_webhook_logic', new_callable=AsyncMock)
async def test_invoke_webhook_success(mock_invoke_webhook_logic):
    # Arrange
    mock_invoke_webhook_logic.return_value = {"status": "success"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        # Act
        response = await ac.post("/v1/webhooks/test-webhook-id", json={})

    # Assert
    assert response.status_code == 200
    assert response.json() == {"status": "success"}
    mock_invoke_webhook_logic.assert_called_once()
