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

"""Identity rule for the IdP-sync management API (EPMCDME-16895).

The configured service account (``IDP_SYNC_SERVICE_ACCOUNT_USERNAME``) may call ``/v1/idp-sync/`` and nothing
else. It is matched by user name only: CodeMie and Keycloak are installed together, so the id of the service
user is not known before the deploy, while ``service-account-<client id>`` is. An empty setting closes the
API for everyone.

``enforce_idp_sync_scope`` is called from ``authenticate`` (next to ``enforce_provisioning_scope``), so every
route that authenticates is covered, including those that call ``authenticate`` inside the handler.
"""

from __future__ import annotations

from fastapi import Request, status

from codemie.configs import config
from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.user import User

IDP_SYNC_PREFIX = "/v1/idp-sync"


def is_idp_sync_account(user: User) -> bool:
    """True only for the configured account (user name matches); never when the setting is empty."""
    username = config.IDP_SYNC_SERVICE_ACCOUNT_USERNAME
    return bool(username) and user.username == username


def _is_idp_sync_path(path: str) -> bool:
    return path == IDP_SYNC_PREFIX or path.startswith(IDP_SYNC_PREFIX + "/")


def enforce_idp_sync_scope(user: User, request: Request) -> None:
    """Raise 403 when the sync account calls a route outside ``/v1/idp-sync/``.

    Matches on the route template (``scope["route"].path``), never ``request.url.path``, so a non-empty
    ``root_path`` neither locks the account out of its own API nor lets it through elsewhere. Fails closed
    when the route is unknown.
    """
    if not is_idp_sync_account(user):
        return
    route_path = getattr(request.scope.get("route"), "path", None)
    if route_path is not None and _is_idp_sync_path(route_path):
        return
    logger.warning(
        f"access_denied_idp_sync_scope: actor_user_id={user.id}, method={request.method}, route={route_path},"
        " domain=user_management"
    )
    raise ExtendedHTTPException(
        code=status.HTTP_403_FORBIDDEN,
        message="Access denied",
        details="This account may only call the IdP-sync API.",
        help="If you believe you should have access, please contact your system administrator.",
    )
