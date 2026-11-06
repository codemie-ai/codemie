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

"""The provisioning API: routes for registered provisioning clients only.

Every route of the parent router passes authenticate, then provisioning_clients_only, then the one
RequirePermission it declares. The parent router's routes are also the clients' allowlist in
authenticate() (see provisioning_access), so a route added under it is reachable by clients.

Every route declares exactly one permission. A permission the target state triggers (manage-projects when a
budget call would create the project or names a cost center or admins) is checked with require_permission
in the service's check phase, before anything is written.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, FastAPI

from codemie.configs import config
from codemie.configs.logger import logger
from codemie.configs.provisioning_clients_config import provisioning_clients_config
from codemie.rest_api.routers.provisioning import budgets, projects
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.provisioning_access import provisioning_clients_only, set_provisioning_routes

router = APIRouter(
    prefix="/v1/provisioning",
    tags=["Provisioning"],
    dependencies=[Depends(authenticate), Depends(provisioning_clients_only)],
)

router.include_router(projects.router)
router.include_router(budgets.router)


def mount_provisioning_api(app: FastAPI) -> None:
    """Mount the router when the API and user management are both on, and confine clients to it."""
    if not config.PROVISIONING_API_ENABLED:
        return
    if not config.ENABLE_USER_MANAGEMENT:
        logger.warning(
            "provisioning_api_disabled: requires ENABLE_USER_MANAGEMENT=true, router not registered,"
            " domain=provisioning"
        )
        return
    if provisioning_clients_config.is_empty:
        logger.warning(
            "provisioning_api_no_clients: the provisioning client registry is empty,"
            " every request will be rejected, domain=provisioning"
        )
    app.include_router(router)
    set_provisioning_routes(router)


__all__ = ["mount_provisioning_api", "router"]
