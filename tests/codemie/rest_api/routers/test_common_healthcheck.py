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

"""Healthcheck endpoints keep liveness and model-provider diagnostics separate."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from codemie.rest_api.routers.common import router
from codemie.service.model_provider_readiness import ModelProviderReadiness, ModelProviderStatus


def _app_with_readiness(readiness: ModelProviderReadiness) -> FastAPI:
    app = FastAPI()
    app.state.model_provider_readiness = readiness
    app.include_router(router)
    return app


@pytest.mark.asyncio
async def test_healthcheck_remains_a_simple_liveness_probe():
    app = _app_with_readiness(
        ModelProviderReadiness(status=ModelProviderStatus.CONFIGURED, provider="azure_openai", missing=[])
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/healthcheck")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert "model_provider" not in response.json()


@pytest.mark.asyncio
async def test_model_healthcheck_reports_configured_provider():
    app = _app_with_readiness(
        ModelProviderReadiness(status=ModelProviderStatus.CONFIGURED, provider="azure_openai", missing=[])
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/healthcheck/model")

    assert response.status_code == 200
    assert response.json() == {
        "status": "configured",
        "provider": "azure_openai",
        "missing": [],
    }


@pytest.mark.asyncio
async def test_model_healthcheck_reports_not_configured_with_http_200():
    app = _app_with_readiness(
        ModelProviderReadiness(
            status=ModelProviderStatus.NOT_CONFIGURED,
            provider="azure_openai",
            missing=["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_URL"],
        )
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/healthcheck/model")

    assert response.status_code == 200
    assert response.json() == {
        "status": "not_configured",
        "provider": "azure_openai",
        "missing": ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_URL"],
    }


@pytest.mark.asyncio
async def test_model_healthcheck_reports_provider_not_checked():
    app = _app_with_readiness(
        ModelProviderReadiness(status=ModelProviderStatus.NOT_CHECKED, provider="google_vertexai", missing=[])
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/healthcheck/model")

    assert response.status_code == 200
    assert response.json() == {
        "status": "not_checked",
        "provider": "google_vertexai",
        "missing": [],
    }


@pytest.mark.asyncio
async def test_model_healthcheck_never_500s_when_app_state_readiness_is_unset():
    """CR-002: an app that mounts this router without running main.py's lifespan() must not 500."""
    app = FastAPI()
    app.include_router(router)  # deliberately no app.state.model_provider_readiness set

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/healthcheck/model")

    assert response.status_code == 200
    assert response.json() == {"status": "not_checked", "provider": None, "missing": []}
