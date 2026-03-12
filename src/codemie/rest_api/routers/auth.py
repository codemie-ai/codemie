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

import base64
import json

from fastapi import APIRouter, status, Request, Path
from fastapi.responses import RedirectResponse

from codemie.configs import config

router = APIRouter(
    tags=["Authentication"],
    prefix="/v1/auth",
    dependencies=[],
)


@router.get("/login/{port}")
async def login(request: Request, port: int = Path(..., ge=1, le=65535)):
    token = {"provider": config.IDP_PROVIDER}
    if token["provider"] != 'local':
        token["cookies"] = {}
        for cookie_name in request.cookies:
            if not cookie_name.startswith('_oauth2_proxy'):
                continue
            token["cookies"][cookie_name] = request.cookies[cookie_name]

    token_str = base64.b64encode(json.dumps(token).encode("ascii")).decode("ascii")
    redirect_url = f'http://localhost:{port}/auth?token={token_str}'
    return RedirectResponse(redirect_url, status_code=status.HTTP_302_FOUND)
