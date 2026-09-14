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
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.utils import get_api_root_path
from codemie.rest_api.security import jwt_local
from codemie.rest_api.security.user import AUTHORIZATION_HEADER

router = APIRouter(
    tags=["Authentication"],
    prefix="/v1/auth",
    dependencies=[],
)


def _get_local_login_cookies(request: Request) -> dict[str, str]:
    cookie_token = request.cookies.get(config.AUTH_COOKIE_NAME)
    auth_header = request.headers.get(AUTHORIZATION_HEADER)
    header_token = auth_header[7:] if auth_header and auth_header.startswith("Bearer ") else None
    token = cookie_token or header_token
    if not token:
        return {}

    try:
        jwt_local.validate_local_jwt(token)
    except ExtendedHTTPException:
        return {}

    return {config.AUTH_COOKIE_NAME: token}


@router.get("/login/{port}")
async def login(request: Request, port: int = Path(..., ge=1, le=65535)):
    token = {"provider": config.IDP_PROVIDER}
    if token["provider"] == "local" and config.ENABLE_USER_MANAGEMENT:
        token["cookies"] = _get_local_login_cookies(request)
        if not token["cookies"]:
            login_url = f"{config.FRONTEND_URL}/auth/sign-in?next={get_api_root_path()}/v1/auth/login/{port}"
            return RedirectResponse(login_url, status_code=status.HTTP_302_FOUND)
    elif token["provider"] != "local":
        token["cookies"] = {
            cookie_name: request.cookies[cookie_name]
            for cookie_name in request.cookies
            if cookie_name.startswith("_oauth2_proxy")
        }
    token_str = base64.b64encode(json.dumps(token).encode("ascii")).decode("ascii")
    redirect_url = f"http://localhost:{port}/auth?token={token_str}"
    return RedirectResponse(redirect_url, status_code=status.HTTP_302_FOUND)
