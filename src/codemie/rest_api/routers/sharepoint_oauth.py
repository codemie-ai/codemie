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

"""SharePoint OAuth2 Device Code Flow endpoints."""

from typing import Optional

import httpx
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from codemie.configs import config, logger
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User

router = APIRouter(
    tags=["SharePoint OAuth"],
    prefix="/v1/sharepoint/oauth",
)

_GRAPH_ME_URL = "https://graph.microsoft.com/v1.0/me?$select=userPrincipalName"

_MS_BASE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0"

_ERROR_DESCRIPTIONS = {
    "authorization_declined": "Authorization was declined. Please try again.",
    "bad_verification_code": "The verification code is invalid or expired.",
    "expired_token": "The device code has expired. Please restart the authentication flow.",
    "invalid_client": "The application is not configured correctly. Contact your administrator.",
}


def _device_url(tenant_id: Optional[str]) -> str:
    return _MS_BASE.format(tenant=tenant_id or "common") + "/devicecode"


def _token_url(tenant_id: Optional[str]) -> str:
    return _MS_BASE.format(tenant=tenant_id or "common") + "/token"


def _sanitize_error_description(error: str) -> str:
    """Return a user-friendly message for known OAuth errors; fall back to the error code."""
    return _ERROR_DESCRIPTIONS.get(error, f"Authentication failed: {error}")


class InitiateDeviceCodeRequest(BaseModel):
    client_id: Optional[str] = None
    tenant_id: Optional[str] = None


class PollDeviceCodeRequest(BaseModel):
    device_code: str
    client_id: Optional[str] = None
    tenant_id: Optional[str] = None


@router.post("/initiate")
async def initiate_device_code(
    request: InitiateDeviceCodeRequest,
    user: User = Depends(authenticate),
) -> JSONResponse:
    """
    Start Device Code Flow: request a device code from Microsoft.

    Args:
        request.client_id: Optional custom Azure app client ID. If omitted, uses the CodeMie shared app.
        request.tenant_id: Optional Azure AD tenant ID. Required for single-tenant custom apps.
                   If omitted, uses the /common/ endpoint (works for multi-tenant apps).

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
        logger.error(f"SharePoint OAuth: device code request failed: {exc}")
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


@router.post("/poll")
async def poll_device_code(
    request: PollDeviceCodeRequest,
    user: User = Depends(authenticate),
) -> JSONResponse:
    """
    Poll Microsoft to check if the user has completed device code authentication.

    Returns:
    - 202 + { status: "pending" }  — user hasn't authenticated yet
    - 200 + { status: "success", access_token, username } — authenticated, token returned directly
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
        logger.error(f"SharePoint OAuth: token poll failed: {exc}")
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
        logger.warning(f"SharePoint OAuth: device code error: {error} — {raw_description}")
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"status": "error", "message": _sanitize_error_description(error)},
        )

    access_token = data.get("access_token", "")

    username = ""
    if access_token:
        try:
            username = await _get_username(access_token)
        except httpx.HTTPError as exc:
            logger.warning(f"SharePoint OAuth: could not retrieve username from Graph API: {exc}")

    return JSONResponse(
        content={
            "status": "success",
            "access_token": access_token,
            "username": username,
        }
    )


async def _get_username(access_token: str) -> str:
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(_GRAPH_ME_URL, headers=headers)
        response.raise_for_status()
        return response.json().get("userPrincipalName", "")
