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

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from codemie.rest_api.security.authentication import authenticate, admin_access_only, User
from codemie.rest_api.models.provider import CreateProviderRequest, UpdateProviderRequest
from codemie.service.provider import ProviderService


router = APIRouter(tags=["Provider"], prefix="/v1", dependencies=[Depends(authenticate)])


@router.get("/providers")
def list_providers(user: User = Depends(authenticate)):
    return ProviderService.index(user)


@router.get("/providers/datasource_schemas")
def list_provider_schemas(user: User = Depends(authenticate)):
    return ProviderService.index_schemas(user)


@router.get("/providers/{provider_id}")
def get_provider(
    provider_id: str,
    user: User = Depends(authenticate),
):
    return ProviderService.get(user, provider_id)


@router.post("/providers")
def create_provider(
    request: CreateProviderRequest, user: User = Depends(authenticate), _is_admin: bool = Depends(admin_access_only)
):
    return ProviderService.create(user, request)


@router.put("/providers/{provider_id}")
def update_provider(
    provider_id: str,
    request: UpdateProviderRequest,
    user: User = Depends(authenticate),
    _is_admin: bool = Depends(admin_access_only),
):
    return ProviderService.update(user, provider_id, request)


@router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_provider(
    provider_id: str, user: User = Depends(authenticate), _is_admin: bool = Depends(admin_access_only)
) -> JSONResponse:
    ProviderService.delete(user, provider_id)
