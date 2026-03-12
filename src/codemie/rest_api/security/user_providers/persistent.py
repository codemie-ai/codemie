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

import asyncio
from typing import Optional

from fastapi import Request

from codemie.configs import config
from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.user import User, USER_ID_HEADER, AUTHORIZATION_HEADER
from codemie.rest_api.security.user_providers import UserProvider
from codemie.service.user.authentication_service import authentication_service

from codemie.rest_api.security.idp.base import BaseIdp

# Request coalescing state — tracks in-flight authentication per user_id.
# Only holds entries during active auth requests; self-cleaning via finally block.
_auth_in_flight: dict[str, asyncio.Future[User]] = {}
_user_auth_coalesce_registry_lock: asyncio.Lock | None = None


def _get_registry_lock() -> asyncio.Lock:
    """Lazy-initialize the registry lock to avoid creating it at module import
    time when no event loop is running (Python 3.10+ deprecation / 3.12+ error).

    No TOCTOU guard needed: this function is synchronous (no await), so asyncio's
    single-threaded event loop guarantees no preemption between the check and assignment.
    """
    global _user_auth_coalesce_registry_lock
    if _user_auth_coalesce_registry_lock is None:
        _user_auth_coalesce_registry_lock = asyncio.Lock()
    return _user_auth_coalesce_registry_lock


def _extract_local_auth_token(request: Request) -> str:
    """Extract JWT token from Authorization header or cookie (mutually exclusive).

    Raises:
        ExtendedHTTPException: 401 if both sources present, or neither present.
    """
    auth_header = request.headers.get("Authorization")
    header_token = auth_header[7:] if auth_header and auth_header.startswith("Bearer ") else None

    cookie_token = request.cookies.get(config.AUTH_COOKIE_NAME)

    if header_token and cookie_token:
        raise ExtendedHTTPException(
            code=401,
            message="Ambiguous authentication",
            details="Token found in both Authorization header and cookie. Use only one.",
        )

    if header_token:
        return header_token
    if cookie_token:
        return cookie_token

    raise ExtendedHTTPException(code=401, message="Authentication required")


class PersistentUserProvider(UserProvider):
    """Database-backed user provider with organic IDP migration

    Used when ENABLE_USER_MANAGEMENT=True.
    Loads/creates users from database.
    Supports organic migration from IDP on first login.

    This provider is thin - all database operations delegated to AuthenticationService.
    Concurrent requests for the same user are coalesced: only the first request
    performs DB operations; subsequent requests await the result (leader/follower pattern).
    """

    async def authenticate_and_load_user(self, request: Request, idp: BaseIdp) -> User:
        """Authenticate request and load/create user from database

        Args:
            request: FastAPI request object
            idp: IDP provider instance

        Returns:
            security.User loaded from database

        Raises:
            ExtendedHTTPException: On authentication failure
        """
        # 1. Check for dev header (ENV='local' only)
        if config.ENV == "local":
            dev_user_id = request.headers.get(USER_ID_HEADER)
            if not dev_user_id:
                dev_user_id = request.headers.get(AUTHORIZATION_HEADER)
            if dev_user_id:
                return await authentication_service.authenticate_dev_header(dev_user_id)

        # 2. Determine auth mode and authenticate
        user_id: str
        idp_user: Optional[User] = None
        auth_token: str

        if config.IDP_PROVIDER == "local":
            from codemie.rest_api.security.jwt_local import validate_local_jwt

            auth_token = _extract_local_auth_token(request)
            claims = validate_local_jwt(auth_token)
            user_id = claims["sub"]
        else:
            idp_user = await idp.authenticate(request)
            user_id = idp_user.id
            auth_token = idp_user.auth_token or ""

        # 3. Coalesced authentication — first request processes, rest wait
        return await _coalesced_authenticate(user_id, idp_user, auth_token)


async def _coalesced_authenticate(user_id: str, idp_user: Optional[User], auth_token: str) -> User:
    """Coalesce concurrent authentication requests for the same user.

    Uses leader/follower pattern with asyncio.Future:
    - First request for a user_id becomes the "leader" and performs actual DB operations
      via authentication_service.authenticate_persistent_user.
    - Concurrent requests for the same user_id become "followers" that await the
      leader's Future and receive a deep copy of the result with their own auth_token.

    This avoids redundant user create/update DB operations when the UI sends
    multiple requests simultaneously on page load.

    Trade-off: followers receive the leader's IDP profile data. Since concurrent
    requests arrive within milliseconds, the IDP data is effectively identical.

    Args:
        user_id: User UUID from token/IDP
        idp_user: IDP user object (None for local JWT)
        auth_token: Authentication token for this specific request

    Returns:
        security.User loaded from database
    """
    is_leader = False
    lock = _get_registry_lock()

    async with lock:
        if user_id in _auth_in_flight:
            future = _auth_in_flight[user_id]
        else:
            future = asyncio.get_running_loop().create_future()
            _auth_in_flight[user_id] = future
            is_leader = True

    if is_leader:
        logger.debug(f"Auth leader started: user_id={user_id}")
        try:
            result = await authentication_service.authenticate_persistent_user(
                user_id=user_id, idp_user=idp_user, auth_token=auth_token
            )
            if not future.done():
                future.set_result(result)
            return result
        except BaseException as exc:
            if not future.done():
                future.set_exception(exc)
            raise
        finally:
            async with lock:
                _auth_in_flight.pop(user_id, None)
    else:
        logger.debug(f"Auth coalesced for user_id={user_id}, waiting for leader request")
        result = await future
        # Deep copy: User contains mutable lists (project_names, knowledge_bases, etc.)
        # Shallow copy would share list references between leader and all followers.
        follower_result = result.model_copy(deep=True)
        follower_result.auth_token = auth_token
        return follower_result
