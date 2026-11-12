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

from codemie.configs import config, logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.idp.base import BaseIdp
from codemie.rest_api.security.user import User, USER_ID_HEADER, AUTHORIZATION_HEADER, stand_in_user

_LOCAL_MOCK_JWT_SECRET = "codemie-local-dev-only-not-for-production"


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
        return jwt.encode(payload, key=_LOCAL_MOCK_JWT_SECRET, algorithm="HS256")

    @staticmethod
    def _user_not_active() -> ExtendedHTTPException:
        return ExtendedHTTPException(
            code=status.HTTP_401_UNAUTHORIZED,
            message="Authentication failed",
            details="User is not active",
        )

    def _user_without_active_row(
        self, user_id: str, auth_token: str, *, has_row: bool, allow_stand_in: bool, signed_loopback: bool
    ) -> User:
        """The user for an id with no active users row: refused when deactivated, else a stand-in."""
        # A deactivated user has no access, also not through signed trigger callbacks
        if not allow_stand_in or has_row:
            raise self._user_not_active()
        return stand_in_user(user_id, component=_stand_in_component(signed_loopback), auth_token=auth_token)

    async def authenticate(
        self, request: Request, *, allow_stand_in: bool = True, signed_loopback: bool = False
    ) -> User:
        """Authenticate using user-id header

        Args:
            request: FastAPI request object with user-id header
            allow_stand_in: False for signed internal callbacks with user management on, which
                get a stand-in user only on ENV local and otherwise need an active user row
            signed_loopback: True only for HMAC-signed internal callbacks. Only those may read an
                owner's IDP-claims snapshot (EPMCDME-16625 AR-5); a bare header never does.

        Returns:
            User object populated from DB when available, stub otherwise

        Raises:
            ExtendedHTTPException: 401 when the header is missing, the user is deactivated, or
                allow_stand_in is False and no active user could be loaded
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

        auth_token = self._generate_mock_token(user_id)

        if config.ENV == "local":
            return User(id=user_id, username=user_id, name=user_id, auth_token=auth_token)

        from codemie.clients.postgres import get_async_session
        from codemie.service.user.user_by_id_loader import aload_user_by_id

        try:
            async with get_async_session() as session:
                lookup = await aload_user_by_id(session, user_id, with_snapshot=signed_loopback)
        except Exception as e:
            if not allow_stand_in:
                logger.warning(f"LocalIdp: DB lookup failed for internal call, user_id='{user_id}': {e}")
                raise self._user_not_active() from e
            logger.warning(f"LocalIdp: DB lookup failed for user_id='{user_id}', falling back to stub user: {e}")
            return stand_in_user(user_id, component=_stand_in_component(signed_loopback), auth_token=auth_token)

        if lookup.user is None:
            return self._user_without_active_row(
                user_id,
                auth_token,
                has_row=lookup.has_row,
                allow_stand_in=allow_stand_in,
                signed_loopback=signed_loopback,
            )
        lookup.user.auth_token = auth_token
        return lookup.user


def _stand_in_component(signed_loopback: bool) -> str:
    return "local_idp_loopback" if signed_loopback else "local_idp"
