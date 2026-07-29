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

"""Google OAuth2 endpoints: Authorization Code + PKCE flow."""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse

from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.google_oauth.flow_service import GoogleOAuthFlowService
from codemie.utils.oauth_html_utils import html_error_page, html_success_page

router = APIRouter(tags=["Google OAuth"], prefix="/v1/google-oauth")

_oauth_service: Optional[GoogleOAuthFlowService] = None


def _get_oauth_service() -> GoogleOAuthFlowService:
    """Build the flow service on first use.

    Constructing it eagerly at import time creates a Redis client, and `redis` is an
    optional dependency — that made backend startup fail with ModuleNotFoundError in
    deployments that do not install it.
    """
    global _oauth_service
    if _oauth_service is None:
        _oauth_service = GoogleOAuthFlowService()
    return _oauth_service


_CALLBACK_SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'",
    "X-Frame-Options": "DENY",
}


@router.post("/initiate")
async def initiate_oauth(
    user: User = Depends(authenticate),
) -> JSONResponse:
    """Initiate Google OAuth flow with PKCE.

    Returns an authorization URL and opaque state token for the frontend to
    redirect the user to Google's consent screen.
    """
    result = _get_oauth_service().initiate_flow(user.id)
    return JSONResponse(content=result)


@router.get("/callback")
async def oauth_callback(
    code: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
    scope: Optional[str] = Query(default=None),
) -> HTMLResponse:
    """Handle the OAuth callback from Google.

    Google redirects the user here after consent. The endpoint exchanges the
    authorization code for tokens and stores the result in Redis.
    Returns an HTML page the user can close.
    """
    result = _get_oauth_service().handle_callback(code, state, error, scope)
    content = html_success_page(result.message) if result.success else html_error_page(result.message)
    return HTMLResponse(
        content=content,
        status_code=result.status_code,
        headers=_CALLBACK_SECURITY_HEADERS,
    )


@router.get("/status/{state}")
async def oauth_status(
    state: str,
    user: User = Depends(authenticate),
) -> JSONResponse:
    """Poll for the result of a Google OAuth flow.

    Returns:
    - 202 + { status: "pending" }    — user has not yet completed authorization
    - 200 + { status: "success", … } — authorization completed successfully
    - 400 + { status: "error", … }   — authorization failed or state expired
    """
    result = _get_oauth_service().get_status(state, user.id)
    if result["status"] == "pending":
        return JSONResponse(status_code=202, content=result)
    if result["status"] == "success":
        return JSONResponse(status_code=200, content=result)
    return JSONResponse(status_code=400, content=result)
