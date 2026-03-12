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

from typing import List, Optional
from fastapi import status

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.user import User
from codemie.rest_api.models.provider import Provider, ProviderBase, CreateProviderRequest, UpdateProviderRequest
from codemie.service.provider.datasource.provider_datasource_schema_service import ProviderDatasourceSchemaService

DEFAULT_PER_PAGE = 10_000
SORT = {"update_date": "DESC"}


class ProviderService:
    """CRUD operations for providers"""

    @classmethod
    def index(
        cls,
        user: User,
        page: int = 0,
        per_page: int = DEFAULT_PER_PAGE,
    ) -> List[ProviderBase]:
        """Get all providers"""
        return Provider.get_all(page_number=page + 1, items_per_page=per_page)

    @classmethod
    def get(cls, user: User, provider_id: str) -> ProviderBase:
        """Get a provider by id"""
        return cls._get_by_id(provider_id)

    @classmethod
    def create(cls, user: User, request: CreateProviderRequest) -> ProviderBase:
        """Create a provider"""
        cls._validate_name_is_unique(request.name)
        provider = Provider(**request.dict(by_alias=True))
        provider.save()

        return provider

    @classmethod
    def update(cls, user: User, provider_id: str, request: UpdateProviderRequest) -> ProviderBase:
        """Update a provider"""
        provider = cls._get_by_id(provider_id)
        cls._validate_name_is_unique(request.name, provider_id)

        for field, value in request.dict().items():
            if value:
                setattr(provider, field, value)

        provider.update()

        return provider

    @classmethod
    def delete(cls, user: User, provider_id: str) -> bool:
        """Delete a provider"""
        provider = cls._get_by_id(provider_id)
        provider.delete()

        return True

    @classmethod
    def index_schemas(cls, user: User) -> List:
        """Returns all datasource schemas"""
        return ProviderDatasourceSchemaService.get_all(user)

    @staticmethod
    def _get_by_id(provider_id: str) -> ProviderBase:
        """Find a provider by id"""
        provider = Provider.find_by_id(provider_id)

        if not provider:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message="Not found",
                details=f"The provider with ID [{provider_id}] could not be found in the system.",
                help="Please verify the provider ID and try again. If you believe this is an error, contact support.",
            )

        return provider

    @staticmethod
    def _validate_name_is_unique(name: Optional[str], provider_id: Optional[str] = None) -> None:
        """Validate that the provider name is unique"""
        if not name:
            return

        if not Provider.check_name_is_unique(name, provider_id):
            raise ExtendedHTTPException(
                code=status.HTTP_409_CONFLICT,
                message="Conflict",
                details=f"A provider with the name [{name}] already exists.",
                help="Please choose a different name and try again.",
            )
