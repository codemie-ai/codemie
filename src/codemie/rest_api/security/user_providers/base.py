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

from typing import Protocol

from fastapi import Request

from codemie.rest_api.security.user import User
from codemie.rest_api.security.idp.base import BaseIdp


class UserProvider(Protocol):
    """Protocol for user authentication providers

    Two implementations:
    - LegacyJwtUserProvider: Ephemeral JWT-based auth (ENABLE_USER_MANAGEMENT=False)
    - PersistentUserProvider: Database-backed auth (ENABLE_USER_MANAGEMENT=True)
    """

    async def authenticate_and_load_user(self, request: Request, idp: BaseIdp) -> User:
        """Authenticate request and return User object

        Args:
            request: FastAPI request object
            idp: IDP provider instance

        Returns:
            security.User object

        Raises:
            ExtendedHTTPException: On authentication failure

        Note:
            Method is async (FastAPI requirement), but implementations
            may call sync SQLModel repository methods internally.
        """
        ...
