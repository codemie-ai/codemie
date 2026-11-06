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

"""Access control for the provisioning API.

- enforce_provisioning_scope: called from authenticate()'s external branch; keeps every registered
  provisioning client on the routes of the provisioning router. Internal calls signed with the bind
  key take the other branch and are exempt. It reads no feature flag: with the API off no routes are
  set, so a client gets 403 everywhere.
- provisioning_clients_only: guard on the parent provisioning router; only registered clients pass.
- require_permission: the permission check itself; raises 403 unless the client holds the permission.
- RequirePermission: per-route dependency; runs require_permission for the route's permission.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.routing import APIRoute

from codemie.configs.logger import logger
from codemie.configs.provisioning_clients_config import (
    ProvisioningClient,
    ProvisioningPermission,
    provisioning_clients_config,
)
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.user import User

_SCOPE_DENIED_DETAILS = "A provisioning client may only call the provisioning API."
_CLIENT_REQUIRED_DETAILS = "This endpoint is reserved for registered provisioning clients."
_PERMISSION_DENIED_DETAILS = "This provisioning client does not hold the permission {permission}."
_CONTACT_ADMIN_HELP = "If you believe you should have access, please contact your system administrator."

_provisioning_routes: frozenset[tuple[str, str]] = frozenset()


def set_provisioning_routes(router: APIRouter) -> None:
    """Record the (method, path) pairs of the mounted provisioning router as the clients' allowlist."""
    global _provisioning_routes
    _provisioning_routes = frozenset(
        (method, route.path) for route in router.routes if isinstance(route, APIRoute) for method in route.methods
    )


def enforce_provisioning_scope(user: User, request: Request) -> None:
    """Raise 403 when a registered provisioning client calls a route outside the provisioning router."""
    client = provisioning_clients_config.find_client(user.id)
    if client is None:
        return

    route_path = getattr(request.scope.get("route"), "path", None)
    if route_path is not None and (request.method, route_path) in _provisioning_routes:
        return

    logger.warning(
        f"access_denied_provisioning_scope: client={client.name}, actor_user_id={user.id},"
        f" method={request.method}, route={route_path}, domain=provisioning"
    )
    raise _access_denied(_SCOPE_DENIED_DETAILS)


async def provisioning_clients_only(request: Request) -> None:
    """Raise 403 unless the caller is a registered provisioning client; being an admin grants no exemption."""
    user = request.state.user
    if provisioning_clients_config.find_client(user.id) is None:
        logger.warning(f"access_denied_provisioning_client: actor_user_id={user.id}, domain=provisioning")
        raise _access_denied(_CLIENT_REQUIRED_DETAILS)


def require_permission(
    client: ProvisioningClient | None, permission: ProvisioningPermission, actor_user_id: str
) -> ProvisioningClient:
    """Return `client` when it holds `permission`; otherwise log and raise 403 (also for an unregistered caller)."""
    if client is None or permission not in client.permissions:
        logger.warning(
            f"access_denied_provisioning_permission: client={client.name if client else None},"
            f" actor_user_id={actor_user_id}, permission={permission}, domain=provisioning"
        )
        raise _access_denied(_PERMISSION_DENIED_DETAILS.format(permission=permission))
    return client


class RequirePermission:
    """Route dependency: the caller must be a registered client holding `permission`; returns that client."""

    def __init__(self, permission: ProvisioningPermission) -> None:
        self.permission = permission

    async def __call__(self, request: Request) -> ProvisioningClient:
        user = request.state.user
        return require_permission(provisioning_clients_config.find_client(user.id), self.permission, user.id)


def _access_denied(details: str) -> ExtendedHTTPException:
    # Imported here because authentication imports this module.
    from codemie.rest_api.security.authentication import ACCESS_DENIED_MESSAGE

    return ExtendedHTTPException(
        code=status.HTTP_403_FORBIDDEN,
        message=ACCESS_DENIED_MESSAGE,
        details=details,
        help=_CONTACT_ADMIN_HELP,
    )
