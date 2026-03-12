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

import jwt
from datetime import datetime, timedelta, timezone
from fastapi import Request, status

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.idp.base import BaseIdp
from codemie.rest_api.security.user import User, USER_ID_HEADER, AUTHORIZATION_HEADER


class LocalIdp(BaseIdp):
    """Simple header-based authentication"""

    def get_session_cookie(self) -> str:
        return ""

    @staticmethod
    def _generate_mock_token(user_id: str) -> str:
        """
        Generate a mock JWT token for local development.

        Creates a valid JWT with 'iss: codemie-local' and 'type: local-mock'
        claims to distinguish it from real tokens.

        Args:
            user_id: The user identifier to include in the token

        Returns:
            A JWT token string suitable for local development

        Security Note:
            This is a mock token for development only. Never use in production.
        """
        now = datetime.now(timezone.utc)
        payload = {
            "sub": user_id,
            "iss": "codemie-local",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(days=1)).timestamp()),
            "user_id": user_id,
            "username": user_id,
            "type": "local-mock",
        }
        return jwt.encode(payload, key="", algorithm="HS256")

    async def authenticate(self, request: Request) -> User:
        """Authenticate using user-id header

        Args:
            request: FastAPI request object with user-id header

        Returns:
            User object with local development credentials
        """
        user_id = request.headers.get(USER_ID_HEADER)
        if not user_id:
            user_id = request.headers.get(AUTHORIZATION_HEADER)

        if not user_id:
            raise ExtendedHTTPException(
                code=status.HTTP_401_UNAUTHORIZED,
                message="Authentication failed",
                details="Missing user-id header for local authentication",
            )

        # Generate mock JWT token for local authentication
        auth_token = self._generate_mock_token(user_id)

        return User(id=user_id, username=user_id, name=user_id, auth_token=auth_token)
