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

"""SharePoint OAuth2 endpoints: Authorization Code + PKCE (oauth_codemie) and Device Code (oauth_custom)."""

import html
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from codemie.configs import config, logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.sharepoint_pkce_service import SharePointPKCEService

_pkce_service = SharePointPKCEService()

_MS_BASE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0"

_CALLBACK_SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'",
    "X-Frame-Options": "DENY",
}

router = APIRouter(
    tags=["SharePoint OAuth"],
    prefix="/v1/sharepoint/oauth",
)


def _require_pkce_enabled() -> None:
    if not config.SHAREPOINT_PKCE_ENABLED:
        raise ExtendedHTTPException(503, "SharePoint PKCE flow is disabled")


def _html_page(success: bool, message: str) -> str:
    escaped = html.escape(message)
    if success:
        body = "<h2>Authentication Complete</h2>" f"<p>{escaped}</p>" "<p>You can close this window.</p>"
    else:
        body = (
            "<h2>Authentication Failed</h2>"
            f"<p>{escaped}</p>"
            "<p>You can close this window and return to the application.</p>"
        )
    return f"<!DOCTYPE html><html><head><meta charset='UTF-8'></head><body>{body}</body></html>"


class InitiatePKCERequest(BaseModel):
    client_id: Optional[str] = None
    tenant_id: Optional[str] = None


@router.post("/initiate")
async def initiate_pkce(
    request: InitiatePKCERequest,
    user: User = Depends(authenticate),
) -> JSONResponse:
    _require_pkce_enabled()
    result = await _pkce_service.initiate(user.id, request.client_id, request.tenant_id)
    return JSONResponse(content=result)


@router.get("/callback")
async def pkce_callback(
    code: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
) -> HTMLResponse:
    if not config.SHAREPOINT_PKCE_ENABLED:
        return HTMLResponse(
            content=_html_page(False, "SharePoint PKCE flow is disabled."),
            status_code=503,
            headers=_CALLBACK_SECURITY_HEADERS,
        )
    result = await _pkce_service.handle_callback(code, state, error)
    return HTMLResponse(
        content=_html_page(result.success, result.message),
        status_code=result.status_code,
        headers=_CALLBACK_SECURITY_HEADERS,
    )


@router.get("/status/{state}")
async def pkce_status(
    state: str,
    user: User = Depends(authenticate),
) -> JSONResponse:
    _require_pkce_enabled()
    result = await _pkce_service.get_status(state, user.id)
    if result["status"] == "pending":
        return JSONResponse(status_code=202, content=result)
    if result["status"] == "success":
        return JSONResponse(status_code=200, content=result)
    return JSONResponse(status_code=400, content=result)


async def _get_username(access_token: str) -> str:
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get("https://graph.microsoft.com/v1.0/me?$select=userPrincipalName", headers=headers)
        response.raise_for_status()
        return response.json().get("userPrincipalName", "")


# ---------------------------------------------------------------------------
# Device Code Flow (oauth_custom) — no Redis dependency
# ---------------------------------------------------------------------------

_DEVICE_CODE_ERROR_DESCRIPTIONS = {
    "authorization_declined": "Authorization was declined. Please try again.",
    "bad_verification_code": "The verification code is invalid or expired.",
    "expired_token": "The device code has expired. Please restart the authentication flow.",
    "invalid_client": "The application is not configured correctly. Contact your administrator.",
}


def _device_url(tenant_id: Optional[str]) -> str:
    return _MS_BASE.format(tenant=tenant_id or "common") + "/devicecode"


def _token_url(tenant_id: Optional[str]) -> str:
    return _MS_BASE.format(tenant=tenant_id or "common") + "/token"


def _sanitize_device_error(error: str) -> str:
    return _DEVICE_CODE_ERROR_DESCRIPTIONS.get(error, f"Authentication failed: {error}")


class InitiateDeviceCodeRequest(BaseModel):
    client_id: Optional[str] = None
    tenant_id: Optional[str] = None


class PollDeviceCodeRequest(BaseModel):
    device_code: str
    client_id: Optional[str] = None
    tenant_id: Optional[str] = None


@router.post("/device/initiate")
async def initiate_device_code(
    request: InitiateDeviceCodeRequest,
    user: User = Depends(authenticate),
) -> JSONResponse:
    """
    Start Device Code Flow for oauth_custom: request a device code from Microsoft.

    Returns { user_code, verification_uri, device_code, expires_in, interval, message }
    that the frontend uses to prompt the user.
    """
    effective_client_id = request.client_id or config.SHAREPOINT_OAUTH_CLIENT_ID
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                _device_url(request.tenant_id),
                data={
                    "client_id": effective_client_id,
                    "scope": config.SHAREPOINT_OAUTH_SCOPES,
                },
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
        logger.error(f"SharePoint Device Code: device code request failed: {exc}")
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"detail": "Failed to request device code from Microsoft. Please try again."},
        )

    return JSONResponse(
        content={
            "user_code": data.get("user_code"),
            "verification_uri": data.get("verification_uri"),
            "device_code": data.get("device_code"),
            "expires_in": data.get("expires_in", 900),
            "interval": data.get("interval", 5),
            "message": data.get("message"),
        }
    )


@router.post("/device/poll")
async def poll_device_code(
    request: PollDeviceCodeRequest,
    user: User = Depends(authenticate),
) -> JSONResponse:
    """
    Poll Microsoft to check if the user has completed device code authentication.

    Returns:
    - 202 + { status: "pending" }  — user hasn't authenticated yet
    - 200 + { status: "success", access_token, username } — authenticated
    - 400 + { status: "error", message } — expired or denied
    """
    effective_client_id = request.client_id or config.SHAREPOINT_OAUTH_CLIENT_ID
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                _token_url(request.tenant_id),
                data={
                    "client_id": effective_client_id,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": request.device_code,
                },
            )
            data = response.json()
    except httpx.HTTPError as exc:
        logger.error(f"SharePoint Device Code: token poll failed: {exc}")
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={
                "status": "error",
                "message": "Failed to poll Microsoft for authentication status. Please try again.",
            },
        )

    error = data.get("error")

    if error == "authorization_pending":
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"status": "pending"},
        )

    if error == "slow_down":
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"status": "pending", "slow_down": True},
        )

    if error:
        raw_description = data.get("error_description", "")
        logger.warning(f"SharePoint Device Code: error: {error} - {raw_description}")
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"status": "error", "message": _sanitize_device_error(error)},
        )

    access_token = data.get("access_token", "")
    username = ""
    if access_token:
        try:
            username = await _get_username(access_token)
        except httpx.HTTPError as exc:
            logger.warning(f"SharePoint Device Code: could not retrieve username: {exc}")

    return JSONResponse(
        content={
            "status": "success",
            "access_token": access_token,
            "username": username,
        }
    )
