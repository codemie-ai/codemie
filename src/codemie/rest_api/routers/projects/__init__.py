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

from __future__ import annotations

from fastapi import APIRouter

from codemie.rest_api.routers.projects import crud, management, membership, spending


router = APIRouter(
    tags=["Projects"],
    prefix="/v1",
    dependencies=[],
)

router.include_router(crud.router)
router.include_router(spending.router)
router.include_router(management.router)
router.include_router(membership.router)

__all__ = ["router"]
