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

"""The production app with the marketplace-management router mounted.

``MARKETPLACE_MANAGEMENT_ENABLED`` ships off, so ``codemie.rest_api.main`` only mounts
the ``/v1/marketplace-management`` router when an operator's ``.env`` turns it on. A
pipeline has no ``.env``. Tests that drive the real ``main.app`` therefore switch the
feature on and mount the router themselves, then put the shared singleton back.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi import FastAPI

_ENTITIES_PREFIX = "/v1/marketplace-management/entities/"


def mounted_marketplace_app() -> Iterator[FastAPI]:
    """Yield ``main.app`` with the router mounted; restore the app on exit.

    Use from a module-scoped fixture: ``yield from mounted_marketplace_app()``.
    """
    from codemie.configs import config
    from codemie.rest_api import main

    routes = main.app.router.routes
    routes_before = len(routes)
    patcher = pytest.MonkeyPatch()
    patcher.setattr(config, "MARKETPLACE_MANAGEMENT_ENABLED", True)
    try:
        if not any(getattr(route, "path", "").startswith(_ENTITIES_PREFIX) for route in routes):
            main.register_marketplace_management_router(main.app)
            main.app.openapi_schema = None
        assert any(
            getattr(route, "path", "").startswith(_ENTITIES_PREFIX) for route in routes
        ), "marketplace-management router is not mounted on the production app"
        yield main.app
    finally:
        if len(routes) > routes_before:
            del routes[routes_before:]
            main.app.openapi_schema = None
        patcher.undo()
