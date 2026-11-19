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

"""Router-level guard of the IdP-sync management API (EPMCDME-16895).

Only the configured service account may call ``/v1/idp-sync/``; the rule that keeps it out of every other
route lives in ``idp_sync_access`` and runs inside ``authenticate``.
"""

from __future__ import annotations

from fastapi import Depends, status

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.idp_sync_access import IDP_SYNC_PREFIX, is_idp_sync_account
from codemie.rest_api.security.user import User

__all__ = ["IDP_SYNC_PREFIX", "is_idp_sync_account", "require_idp_sync_account"]


async def require_idp_sync_account(
    user: User = Depends(authenticate),  # noqa: B008 - FastAPI dependency injection
) -> User:
    """Router-level dependency of the IdP-sync API: 403 unless the caller is the configured account."""
    if not is_idp_sync_account(user):
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message="Access denied",
            details="Only the configured IdP-sync service account may call this API.",
            help="If you believe you should have access, please contact your system administrator.",
        )
    return user
