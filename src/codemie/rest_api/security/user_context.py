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

"""
User context management using ContextVar.

This module provides thread-safe and async-friendly storage for the current
authenticated user during request processing. It uses Python's contextvars
to maintain user context that can be accessed from different parts of the
application without explicit parameter passing.

The user context is set during authentication and can be retrieved in
downstream components (like MCP toolkit service) for placeholder resolution
in headers and other configuration.
"""

from contextvars import ContextVar

from codemie.rest_api.security.user import User

# Thread-safe and async-friendly context storage for the current user
# Each request gets its own isolated context
_current_user: ContextVar[User | None] = ContextVar('current_user', default=None)
_current_auth_token: ContextVar[str | None] = ContextVar('current_auth_token', default=None)


def set_current_user(user: User) -> None:
    """
    Store the current authenticated user in the request context.

    This function should be called during authentication after the user
    has been successfully validated. The user will be available for the
    duration of the request in the current async context.

    Args:
        user: The authenticated User object to store in context
    """
    _current_user.set(user)


def get_current_user() -> User | None:
    """
    Retrieve the current authenticated user from the request context.

    This function can be called from anywhere in the request handling
    chain to access the authenticated user without explicit parameter
    passing.

    Returns:
        The authenticated User object if available, None otherwise
    """
    return _current_user.get()


def clear_current_user() -> None:
    """
    Clear the current user from the request context.

    This function can be used for cleanup purposes, though ContextVar
    automatically handles context isolation between requests.
    """
    _current_user.set(None)


def set_current_auth_token(token: str) -> None:
    """
    Store the current authentication token in the request context.

    Security Note:
        This token is stored in a request-scoped context and never logged.
        It should only be used for outbound API authentication.

    Args:
        token: The JWT authentication token to store in context
    """
    _current_auth_token.set(token)


def get_current_auth_token() -> str | None:
    """
    Retrieve the current authentication token from the request context.

    Usage:
        Prefer this over user.auth_token for cleaner API access:
        # OLD: token = get_current_user().auth_token
        # NEW: token = get_current_auth_token()

    Returns:
        The JWT authentication token if available, None otherwise

    Security Note:
        Never log or expose this token in error messages or responses.
    """
    return _current_auth_token.get()


def clear_current_auth_token() -> None:
    """
    Clear the current authentication token from the request context.

    This function can be used for cleanup purposes, though ContextVar
    automatically handles context isolation between requests.
    """
    _current_auth_token.set(None)
