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

from codemie.rest_api.routers.index import (
    code_repositories,
    kb_atlassian,
    kb_azure_devops,
    kb_files,
    kb_git_faq,
    kb_google,
    kb_sharepoint,
    kb_xwiki,
    providers,
    queries,
)
from codemie.rest_api.security.authentication import authenticate

router = APIRouter(tags=["Indexing"], prefix="/v1", dependencies=[Depends(authenticate)])
router.include_router(queries.router)
router.include_router(code_repositories.router)
router.include_router(kb_atlassian.router)
router.include_router(kb_azure_devops.router)
router.include_router(kb_google.router)
router.include_router(kb_sharepoint.router)
router.include_router(kb_xwiki.router)
router.include_router(kb_git_faq.router)
router.include_router(kb_files.router)
router.include_router(providers.router)

__all__ = ["router"]
