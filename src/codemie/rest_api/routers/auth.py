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
from urllib.parse import quote, urlparse

from fastapi import APIRouter, status, Request, Path, Query
from fastapi.responses import RedirectResponse

from codemie.configs import config
from codemie.core.constants import Environment
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.middleware.dynamic_cors import CLI_AUTH_ALLOWED_EXTERNAL_ORIGINS_KEY
from codemie.service.dynamic_config_service import DynamicConfigService

router = APIRouter(
    tags=["Authentication"],
    prefix="/v1/auth",
    dependencies=[],
)


@router.get("/login/{port}")
async def login(request: Request, port: int = Path(..., ge=1, le=65535)):
    token = _build_auth_token(request)
    token_str = base64.b64encode(json.dumps(token).encode("ascii")).decode("ascii")
    redirect_url = f'http://localhost:{port}/auth?token={token_str}'
    return RedirectResponse(redirect_url, status_code=status.HTTP_302_FOUND)


@router.get("/login/redirect")
async def login_with_redirect(
    request: Request,
    callback_url: str = Query(
        ...,
        description="External HTTPS URL that will receive the auth token as a query parameter",
    ),
):
    """Browser-based SSO flow that redirects to an admin-allowed external origin.

    This endpoint preserves the existing localhost callback flow while enabling
    trusted third-party sites to receive the same token payload. The destination
    domain must be added to the CLI_AUTH_ALLOWED_EXTERNAL_ORIGINS dynamic config
    by an administrator.
    """
    await _validate_callback_url(callback_url)
    token = _build_auth_token(request)
    token_str = base64.b64encode(json.dumps(token).encode("ascii")).decode("ascii")
    encoded_token = quote(token_str, safe='')

    separator = '&' if '?' in callback_url else '?'
    redirect_url = f"{callback_url}{separator}token={encoded_token}"
    return RedirectResponse(redirect_url, status_code=status.HTTP_302_FOUND)


def _build_auth_token(request: Request) -> dict:
    token = {"provider": config.IDP_PROVIDER}
    if token["provider"] != 'local':
        token["cookies"] = {
            cookie_name: request.cookies[cookie_name]
            for cookie_name in request.cookies
            if cookie_name.startswith('_oauth2_proxy')
        }
    return token


async def _load_allowed_external_origins() -> set[str]:
    raw = await DynamicConfigService.aget(CLI_AUTH_ALLOWED_EXTERNAL_ORIGINS_KEY, default="[]")
    try:
        origins = json.loads(raw)
    except json.JSONDecodeError:
        origins = []

    if not isinstance(origins, list):
        origins = []

    return {origin.strip().lower() for origin in origins if isinstance(origin, str) and origin.strip()}


async def _validate_callback_url(callback_url: str) -> None:
    try:
        parsed = urlparse(callback_url)
    except ValueError as exc:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Invalid callback URL",
            details=f"Could not parse callback URL: {exc}",
        ) from exc

    if not parsed.scheme or not parsed.hostname:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Invalid callback URL",
            details="Callback URL must include a scheme and a host",
        )

    if parsed.scheme not in {"https"} and not (Environment.LOCAL.value == config.ENV and parsed.scheme == "http"):
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Invalid callback URL",
            details="Callback URL must use HTTPS",
        )

    if parsed.username is not None or parsed.password is not None:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Invalid callback URL",
            details="Callback URL must not contain user credentials",
        )

    if parsed.fragment:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Invalid callback URL",
            details="Callback URL must not contain a fragment",
        )

    allowed = await _load_allowed_external_origins()
    origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    if origin not in allowed:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Callback URL not allowed",
            details="The callback URL domain is not in the administrator allow-list",
        )
