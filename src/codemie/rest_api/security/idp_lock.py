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

"""FastAPI dependency that locks user and project mutations when the identity provider owns them."""

from __future__ import annotations

from fastapi import status

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException

IDP_LOCK_MESSAGE = "User management is controlled by the identity provider."


def idp_lock_error() -> ExtendedHTTPException:
    """The 403 answered for every change the identity provider owns."""
    return ExtendedHTTPException(code=status.HTTP_403_FORBIDDEN, message=IDP_LOCK_MESSAGE)


def require_no_idp_lock() -> None:
    """Block the request with HTTP 403 when IDP_MANAGED_ENABLED=True.

    Applied to mutation handlers (POST/PUT/DELETE) of user, project and membership management;
    never to read-only GET endpoints.
    """
    if config.IDP_MANAGED_ENABLED:
        raise idp_lock_error()
