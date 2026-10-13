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

from fastapi import APIRouter

from codemie.rest_api.routers.assistant import (
    catalog,
    execution,
    generation,
    health,
    management,
    marketplace,
    toolkits,
    versions,
)

router = APIRouter(
    tags=["Assistant"],
    prefix="/v1",
    dependencies=[],
)

router.include_router(catalog.router)
router.include_router(health.router)
router.include_router(toolkits.router)
router.include_router(management.router)
router.include_router(execution.router)
router.include_router(marketplace.router)
router.include_router(generation.router)
router.include_router(versions.router)

__all__ = ["router"]
