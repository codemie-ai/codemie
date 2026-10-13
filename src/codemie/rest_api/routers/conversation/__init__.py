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

from fastapi import APIRouter, Depends

from codemie.rest_api.routers.conversation import crud, deletion, folders, history, messages, retrieval, streaming
from codemie.rest_api.security.authentication import authenticate


router = APIRouter(
    tags=["Conversation"],
    prefix="/v1",
    dependencies=[Depends(authenticate)],
)

# Include order is source order and load-bearing: 5 of the 7 route-precedence pairs span modules.
router.include_router(retrieval.router)
router.include_router(streaming.router)
router.include_router(deletion.router)
router.include_router(history.router)
router.include_router(crud.router)
router.include_router(folders.router)
router.include_router(messages.router)

__all__ = ["router"]
