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

"""Isolated helpers that confine the ServiceNow service account to its integration routes.

Mirrors the UserProvider separation-of-concerns pattern: this module is the
entire surface area, since there is exactly one caller (authenticate()) plus
one route guard (servicenow_integration_access_only) and no state to hold.
Split into a stateless predicate (is_servicenow_service_account) and the
scope check (enforce_servicenow_scope) so the guard can reuse the predicate
without paying for the route lookup.
"""

from __future__ import annotations

from functools import cache

from fastapi import Request, status

from codemie.configs import config
from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.user import User
from codemie.rest_api.security.user_type_validator import SERVICE_ACCOUNT_USER_TYPE

_SCOPE_DENIED_DETAILS = "This service account may only call the ServiceNow integration endpoints."
_SCOPE_DENIED_HELP = "If you believe you should have access, please contact your system administrator."


def is_servicenow_service_account(user: User) -> bool:
    return bool(
        config.SERVICENOW_INTEGRATION_ENABLED
        and config.SERVICENOW_SERVICE_ACCOUNT_ID
        and user.id == config.SERVICENOW_SERVICE_ACCOUNT_ID
        and user.user_type == SERVICE_ACCOUNT_USER_TYPE
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
