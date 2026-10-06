# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

from fastapi import APIRouter, Depends, Request, status

from codemie.configs import config
from codemie.core.constants import APP_DESCRIPTION
from codemie.core.models import DeploymentVersionItem, DeploymentVersionsResponse, InfoResponse
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.model_provider_readiness import ModelProviderStatus

router = APIRouter(
    tags=["Common"],
    prefix="/v1",
    dependencies=[],
)


@router.get("/info", status_code=status.HTTP_200_OK, response_model=InfoResponse)
def app_info():
    return InfoResponse(
        message="Codemie",
        version=config.APP_VERSION,
        description=APP_DESCRIPTION,
        file_datasource_max_upload_count=config.FILE_DATASOURCE_MAX_UPLOAD_COUNT,
    )


@router.get(
    "/deployment-versions",
    status_code=status.HTTP_200_OK,
    response_model=DeploymentVersionsResponse,
)
def list_deployment_versions(user: User = Depends(authenticate)):
    from codemie.service.deployment.deployment_version_service import deployment_version_service

    records = deployment_version_service.list_deployments()
    return DeploymentVersionsResponse(
        deployments=[DeploymentVersionItem(version=r.version, deployed_at=r.deployed_at) for r in records]
    )


@router.get("/healthcheck", include_in_schema=False)
def healthcheck():
    import os
    import psutil

    process = psutil.Process(os.getpid())
    return {
        "status": "healthy",
        "memory_usage_mb": process.memory_info().rss / 1024 / 1024,
        "cpu_percent": process.cpu_percent(),
        "worker_pid": os.getpid(),
    }


@router.get("/healthcheck/model", include_in_schema=False)
def model_provider_healthcheck(request: Request):
    readiness = getattr(request.app.state, "model_provider_readiness", None)
    if readiness is None:
        return {"status": ModelProviderStatus.NOT_CHECKED.value, "provider": None, "missing": []}
    return {
        "status": readiness.status.value,
        "provider": readiness.provider,
        "missing": readiness.missing,
    }
