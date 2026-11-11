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

from __future__ import annotations

from fastapi import Request

from codemie.configs import logger
from codemie.rest_api.security.idp.base import BaseIdp
from codemie.rest_api.security.user import User
from codemie.rest_api.security.user_providers import UserProvider
from codemie.service.user.authentication_service import authentication_service


class LegacyJwtUserProvider(UserProvider):
    """Legacy ephemeral JWT-based user provider

    Used when ENABLE_USER_MANAGEMENT=False.
    Reconstructs User from JWT claims on every request.
    Zero database queries.

    This provider is thin - delegates project creation to AuthenticationService.
    """

    async def authenticate_and_load_user(self, request: Request, idp: BaseIdp) -> User:
        """Authenticate request using IDP and return ephemeral User

        This is the extracted logic from the current authenticate() function.

        Args:
            request: FastAPI request object
            idp: IDP provider instance

        Returns:
            security.User reconstructed from JWT claims

        Raises:
            ExtendedHTTPException: On authentication failure
        """
        # 1. Delegate to IDP for validation and User construction
        # IDP extracts token internally from request
        user = await idp.authenticate(request)

        # 2. Ensure personal workspace exists (delegate to service)
        if user.username and user.username in user.project_names:
            await authentication_service.ensure_project_exists(user.username)

        logger.debug(f"User authenticated (legacy): user_id={user.id}")
        return user
