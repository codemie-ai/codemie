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

from elasticsearch.exceptions import NotFoundError
from fastapi import APIRouter, BackgroundTasks, status, Depends
from starlette.requests import Request

from codemie.configs import logger
from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import (
    BaseResponse,
    BaseResponseWithData,
)
from codemie.service.provider.datasource import (
    ProviderDatasourceCreationService,
    ProviderDatasourceUpdateService,
    ProviderDatasourceReindexService,
)
from codemie.rest_api.models.index import (
    IndexInfo,
)
from codemie.rest_api.models.provider import Provider
from codemie.rest_api.security.authentication import authenticate, admin_access_only
from codemie.rest_api.security.user import User
from codemie.service.provider.datasource.provider_datasource_import_service import (
    DatasourceImportSummary,
    ProviderDatasourceImportService,
)
from codemie.rest_api.routers.utils import (
    raise_forbidden,
)

from codemie.rest_api.routers.index._common import (
    INDEX_NOT_FOUND_MESSAGE,
    _index_unique_check,
)


router = APIRouter()


@router.post("/index/provider", status_code=status.HTTP_200_OK)
def create_provider_datasource_index(
    background_tasks: BackgroundTasks,
    raw_request: Request,
    toolkit_id: str,
    provider_name: str,
    request: dict,
):
    _index_unique_check(request['project_name'], request['name'])
    provider = Provider.get_by_fields({"name.keyword": provider_name})

    if not provider:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Provider not found",
            details=f"The provider with name '{provider_name}' could not be found in the system.",
            help="Please verify the provider name and ensure it exists. "
            "If you believe this is an error, contact support.",
        )

    service = ProviderDatasourceCreationService(
        provider=provider,
        toolkit_id=toolkit_id,
        values=request,
        user=raw_request.state.user,
    )

    background_tasks.add_task(service.run)
    return BaseResponse(message=service.started_message)


@router.put("/index/provider/{index_info_id}/reindex", status_code=status.HTTP_200_OK)
def reindex_provider_datasource_index(
    background_tasks: BackgroundTasks,
    raw_request: Request,
    index_info_id: str,
    request: dict,
    user: User = Depends(authenticate),
):
    index_info = IndexInfo.find_by_id(index_info_id)

    if not index_info:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=INDEX_NOT_FOUND_MESSAGE,
            details=f"The index with ID '{index_info_id}' could not be found in the system.",
            help="Please verify the index ID and ensure it exists. If you believe this is an error, contact support.",
        )

    service = ProviderDatasourceReindexService(
        datasource=index_info,
        values=request,
        user=user,
    )
    background_tasks.add_task(service.run)
    return BaseResponse(message=service.started_message)


@router.put("/index/provider/{index_info_id}", status_code=status.HTTP_200_OK)
def update_provider_datasource_index(
    background_tasks: BackgroundTasks,
    raw_request: Request,
    index_info_id: str,
    request: dict,
    user: User = Depends(authenticate),
):
    index_info = IndexInfo.find_by_id(index_info_id)

    if not index_info:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=INDEX_NOT_FOUND_MESSAGE,
            details=f"The index with ID '{index_info_id}' could not be found in the system.",
            help="Please verify the index ID and ensure it exists. If you believe this is an error, contact support.",
        )

    if not Ability(user).can(Action.WRITE, index_info):
        raise_forbidden("update")

    try:
        provider = Provider.get_by_id(index_info.provider_fields.provider_id)
    except NotFoundError as e:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Provider not found",
            details=f"The provider with name '{index_info.provider_id}' could not be found in the system.",
            help="Please verify the provider name and ensure it exists."
            "If you believe this is an error, contact support.",
        ) from e

    service = ProviderDatasourceUpdateService(datasource=index_info, values=request, provider=provider, user=user)
    service.run()

    return BaseResponse(message=service.updated_message)


@router.get(
    "/index/provider/datasources/import/availability",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponseWithData,
    dependencies=[Depends(admin_access_only)],
)
def datasource_import_availability(admin: User = Depends(authenticate)) -> BaseResponseWithData:
    """
    Report which providers can be imported from (drives UI availability of the import action).

    A provider is importable if it advertises a datasource-listing (CATALOG) capability or has a
    built-in import source. Returns an empty list when no importable provider is installed (e.g.
    AICE not deployed), so the UI can hide the feature. Admin-only.
    """
    providers = ProviderDatasourceImportService.importable_provider_names(user=admin)
    return BaseResponseWithData(
        message="Datasource import availability",
        data={"available": bool(providers), "providers": providers},
    )


@router.post(
    "/index/provider/datasources/import",
    status_code=status.HTTP_200_OK,
    response_model=DatasourceImportSummary,
    dependencies=[Depends(admin_access_only)],
)
def import_provider_datasources(admin: User = Depends(authenticate)) -> DatasourceImportSummary:
    """
    Import datasources that already exist on configured providers into CodeMie.

    Provider-agnostic: for every importable provider, enumerates its datasources and creates an
    ``index_info`` row for each one not yet registered (deduped by external datasource id, so
    re-running only imports new ones). Does not trigger any provider creation/indexing — the
    datasources are already built. Providers that aren't installed/importable are skipped. Admin-only.

    Returns:
        DatasourceImportSummary with per-provider imported/skipped counts and any per-item errors.
    """
    logger.info(
        "Provider datasource import triggered by admin",
        extra={"admin_id": admin.id, "admin_name": admin.name},
    )

    try:
        return ProviderDatasourceImportService(user=admin).run()
    except ValueError as e:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Datasource import failed",
            details=str(e),
            help="Verify that the target provider is configured correctly and try again.",
        )
    except Exception as e:
        logger.error(f"Provider datasource import failed: {e}", exc_info=True, extra={"admin_id": admin.id})
        raise ExtendedHTTPException(
            code=status.HTTP_502_BAD_GATEWAY,
            message="Failed to import datasources",
            details="Could not reach a provider to enumerate its datasources.",
            help="Check that the provider is reachable and try again.",
        )
