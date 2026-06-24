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

import base64
import contextlib
import hashlib
import json
import secrets
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

import httpx

from codemie.clients.redis import create_redis_client
from codemie.configs import config, logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.encryption.base_encryption_service import BaseEncryptionService
from codemie.service.encryption.encryption_factory import EncryptionFactory

_GRAPH_ME_URL = "https://graph.microsoft.com/v1.0/me?$select=userPrincipalName"
_MS_BASE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0"
_STATE_KEY_PREFIX = "codemie:sp_pkce:state:"
_RESULT_KEY_PREFIX = "codemie:sp_pkce:result:"
_STATE_TTL = 600
_RESULT_TTL = 300

_ERROR_DESCRIPTIONS = {
    "access_denied": "Authorization was declined. Please try again.",
    "invalid_grant": "The authorization code is invalid or expired.",
    "invalid_client": "The application is not configured correctly. Contact your administrator.",
}


@dataclass
class CallbackResult:
    success: bool
    message: str
    status_code: int


class SharePointPKCEService:
    def __init__(self, redis_client=None, encryption_service: Optional[BaseEncryptionService] = None):
        self._redis = redis_client or create_redis_client()
        self._enc = encryption_service or EncryptionFactory().get_current_encryption_service()

    def _generate_code_verifier(self) -> str:
        return secrets.token_urlsafe(96)

    def _generate_code_challenge(self, verifier: str) -> str:
        digest = hashlib.sha256(verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    def _sanitize_error(self, error: str) -> str:
        return _ERROR_DESCRIPTIONS.get(error, f"Authentication failed: {error}")

    def _redis_set_result(self, key: str, payload: dict, ttl: int) -> bool:
        try:
            self._redis.set(key, self._enc.encrypt(json.dumps(payload)), ex=ttl)
            return True
        except Exception as exc:
            logger.error(f"SharePoint PKCE: Redis unavailable while storing result: {exc}")
            return False

    def _handle_provider_error(self, state_key: str, result_key: str, error: str, user_id: str = "") -> CallbackResult:
        # state_key already consumed by getdel in handle_callback
        message = self._sanitize_error(error)
        self._redis_set_result(result_key, {"status": "error", "message": message, "user_id": user_id}, _RESULT_TTL)
        return CallbackResult(False, message, 200)

    async def _get_username(self, access_token: str) -> str:
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(_GRAPH_ME_URL, headers=headers)
            response.raise_for_status()
            return response.json().get("userPrincipalName", "")

    async def initiate(self, user_id: str, client_id: Optional[str], tenant_id: Optional[str]) -> dict:
        effective_client_id = client_id or config.SHAREPOINT_OAUTH_CLIENT_ID
        effective_tenant_id = tenant_id or "common"
        code_verifier = self._generate_code_verifier()
        code_challenge = self._generate_code_challenge(code_verifier)
        state = secrets.token_urlsafe(32)

        state_data = json.dumps(
            {
                "code_verifier": code_verifier,
                "client_id": effective_client_id,
                "tenant_id": effective_tenant_id,
                "user_id": user_id,
            }
        )

        try:
            self._redis.set(f"{_STATE_KEY_PREFIX}{state}", self._enc.encrypt(state_data), ex=_STATE_TTL)
        except Exception as exc:
            logger.error(f"SharePoint PKCE: failed to store state in Redis: {exc}")
            raise ExtendedHTTPException(502, "Failed to initiate authentication")

        authorize_base = _MS_BASE.format(tenant=effective_tenant_id) + "/authorize"
        redirect_uri = f"{config.CALLBACK_API_BASE_URL}/v1/sharepoint/oauth/callback"
        params = {
            "client_id": effective_client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": config.SHAREPOINT_OAUTH_SCOPES,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return {"auth_url": authorize_base + "?" + urlencode(params), "state": state}

    async def handle_callback(self, code: Optional[str], state: Optional[str], error: Optional[str]) -> CallbackResult:
        if not state:
            return CallbackResult(False, "Missing state parameter.", 400)

        state_key = f"{_STATE_KEY_PREFIX}{state}"
        result_key = f"{_RESULT_KEY_PREFIX}{state}"

        try:
            raw = self._redis.getdel(state_key)
        except Exception as exc:
            logger.error(f"SharePoint PKCE: Redis unavailable reading state in callback: {exc}")
            return CallbackResult(False, "Authentication service temporarily unavailable. Please try again.", 503)

        # Extract user_id before branching — needed in both error and success result payloads
        user_id = ""
        if raw:
            with contextlib.suppress(Exception):
                user_id = json.loads(self._enc.decrypt(raw.decode())).get("user_id", "")

        if error:
            return self._handle_provider_error(state_key, result_key, error, user_id)

        if raw is None:
            return CallbackResult(False, "Invalid or expired authentication state.", 400)

        # State consumed atomically by getdel above — no separate delete needed

        state_data = json.loads(self._enc.decrypt(raw.decode()))
        tenant_id = state_data.get("tenant_id") or None
        client_id = state_data.get("client_id") or config.SHAREPOINT_OAUTH_CLIENT_ID
        code_verifier = state_data["code_verifier"]
        redirect_uri = f"{config.CALLBACK_API_BASE_URL}/v1/sharepoint/oauth/callback"

        try:
            async with httpx.AsyncClient(timeout=15) as http:
                token_response = await http.post(
                    _MS_BASE.format(tenant=tenant_id or "common") + "/token",
                    data={
                        "client_id": client_id,
                        "grant_type": "authorization_code",
                        "code": code,
                        "code_verifier": code_verifier,
                        "redirect_uri": redirect_uri,
                        "scope": config.SHAREPOINT_OAUTH_SCOPES,
                    },
                )
                token_data = token_response.json()
        except httpx.HTTPError as exc:
            logger.error(f"SharePoint PKCE: token exchange failed: {exc}")
            self._redis_set_result(
                result_key, {"status": "error", "message": "Token exchange failed.", "user_id": user_id}, _RESULT_TTL
            )
            return CallbackResult(False, "Token exchange failed.", 200)

        if "error" in token_data:
            message = self._sanitize_error(token_data["error"])
            logger.warning(
                f"SharePoint PKCE: token error: {token_data['error']} — {token_data.get('error_description', '')}"
            )
            self._redis_set_result(result_key, {"status": "error", "message": message, "user_id": user_id}, _RESULT_TTL)
            return CallbackResult(False, message, 200)

        access_token = token_data.get("access_token", "")
        username = ""
        if access_token:
            try:
                username = await self._get_username(access_token)
            except httpx.HTTPError as exc:
                logger.warning(f"SharePoint PKCE: could not retrieve username: {exc}")

        stored = self._redis_set_result(
            result_key,
            {"status": "success", "access_token": access_token, "username": username, "user_id": user_id},
            _RESULT_TTL,
        )
        if not stored:
            return CallbackResult(False, "Authentication service temporarily unavailable. Please try again.", 503)

        return CallbackResult(True, "Authentication successful.", 200)

    async def get_status(self, state: str, user_id: str) -> dict:
        result_key = f"{_RESULT_KEY_PREFIX}{state}"
        try:
            raw = self._redis.get(result_key)
        except Exception as exc:
            logger.error(f"SharePoint PKCE: failed to read status from Redis: {exc}")
            raise ExtendedHTTPException(502, "Failed to read authentication status")

        if raw is None:
            return {"status": "pending"}

        result = json.loads(self._enc.decrypt(raw.decode()))

        if result.get("user_id") != user_id:
            raise ExtendedHTTPException(403, "Forbidden")

        try:
            self._redis.delete(result_key)
        except Exception as exc:
            logger.warning(f"SharePoint PKCE: failed to delete result key from Redis: {exc}")

        if result.get("status") == "success":
            return {
                "status": "success",
                "access_token": result.get("access_token", ""),
                "username": result.get("username", ""),
            }
        return {"status": "error", "message": result.get("message", "Authentication failed.")}
