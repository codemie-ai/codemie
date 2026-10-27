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

"""Isolated helpers that identify the ServiceNow service account and confine it to its integration routes.

The account is identified by its id (the token subject) alone, because it is
provisioned like any other client-credentials API client and its stored
user_type is usually "regular".

- is_servicenow_service_account: the identity predicate. Used by the route
  guard (servicenow_integration_access_only), by enforce_servicenow_scope, and
  by user_type_validator.is_service_account, which excludes the account
  wherever service accounts are excluded.
- enforce_servicenow_scope: called from authenticate(); keeps the account on
  the ServiceNow router.
"""

from __future__ import annotations

from functools import cache

from fastapi import Request, status

from codemie.configs import config
from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.user_management import UserDB
from codemie.rest_api.security.user import User

_SCOPE_DENIED_DETAILS = "This service account may only call the ServiceNow integration endpoints."
_SCOPE_DENIED_HELP = "If you believe you should have access, please contact your system administrator."


def is_servicenow_service_account(user: User | UserDB) -> bool:
    return bool(
        config.SERVICENOW_INTEGRATION_ENABLED
        and config.SERVICENOW_SERVICE_ACCOUNT_ID
        and user.id == config.SERVICENOW_SERVICE_ACCOUNT_ID
    )


@cache
def _allowed_routes() -> frozenset[tuple[str, str]]:
    from codemie.rest_api.routers.servicenow_integration import router

    return frozenset((method, route.path) for route in router.routes for method in route.methods)


def enforce_servicenow_scope(user: User, request: Request) -> None:
    """Raise 403 when the ServiceNow service account calls anything outside its router."""
    if not is_servicenow_service_account(user):
        return

    from codemie.rest_api.security.authentication import ACCESS_DENIED_MESSAGE

    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    if route_path is not None and (request.method, route_path) in _allowed_routes():
        return

    logger.warning(
        f"access_denied_servicenow_scope: actor_user_id={user.id}, method={request.method},"
        f" route={route_path}, domain=servicenow_integration"
    )
    raise ExtendedHTTPException(
        code=status.HTTP_403_FORBIDDEN,
        message=ACCESS_DENIED_MESSAGE,
        details=_SCOPE_DENIED_DETAILS,
        help=_SCOPE_DENIED_HELP,
    )
