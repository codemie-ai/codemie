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
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional
from urllib.parse import urlencode

import httpx
import requests

from codemie.clients.redis import create_redis_client
from codemie.configs import config, logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.utils import get_api_root_path
from codemie.service.encryption.base_encryption_service import BaseEncryptionService
from codemie.service.encryption.encryption_factory import EncryptionFactory

if TYPE_CHECKING:
    from codemie.rest_api.models.settings import CredentialValues

_GRAPH_ME_URL = "https://graph.microsoft.com/v1.0/me?$select=userPrincipalName"
_MS_BASE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0"
# Values written by the delegated sign-in flows (integration, PKCE and device code).
_DELEGATED_AUTH_TYPES = ("oauth", "oauth_codemie", "oauth_custom")
# Refresh before the token actually dies, so a run cannot start on one that expires
# mid-flight. Same buffer as the Google OAuth token manager.
_REFRESH_BUFFER = 5 * 60
_REFRESH_TIMEOUT = 30
_STATE_KEY_PREFIX = "codemie:sp_pkce:state:"
_RESULT_KEY_PREFIX = "codemie:sp_pkce:result:"
_REFRESH_LOCK_KEY_PREFIX = "codemie:sp_pkce:refresh_lock:"
_STATE_TTL = 600
_AUTH_UNAVAILABLE_MSG = "Authentication service temporarily unavailable. Please try again."
_RESULT_TTL = 300
# Outlive the slowest possible refresh (_REFRESH_TIMEOUT plus the write) so the lock is
# never released under its holder, but still expire if a worker dies mid-refresh.
_REFRESH_LOCK_TTL = 45
_REFRESH_LOCK_WAIT = 35
_REFRESH_LOCK_POLL = 0.2
# Release only our own lock: after a TTL expiry the key may belong to another worker.
_RELEASE_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""

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
        # `redis` is an optional dependency, so only reach for a client when the PKCE
        # flow is actually enabled. Callers are gated on the same flag, which keeps
        # `_redis` from being used while it is None.
        if redis_client is None and config.SHAREPOINT_PKCE_ENABLED:
            redis_client = create_redis_client()
        self._redis = redis_client
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
        effective_tenant_id = tenant_id or config.SHAREPOINT_OAUTH_TENANT_ID or "common"
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
            if config.STANDALONE:
                raise ExtendedHTTPException(503, _AUTH_UNAVAILABLE_MSG)
            raise ExtendedHTTPException(502, "Failed to initiate authentication")

        authorize_base = _MS_BASE.format(tenant=effective_tenant_id) + "/authorize"
        redirect_uri = f"{config.CALLBACK_API_BASE_URL}{get_api_root_path()}/v1/sharepoint/oauth/callback"
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
            return CallbackResult(False, _AUTH_UNAVAILABLE_MSG, 503)

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
        redirect_uri = f"{config.CALLBACK_API_BASE_URL}{get_api_root_path()}/v1/sharepoint/oauth/callback"

        try:
            async with httpx.AsyncClient(timeout=15) as http:
                token_response = await http.post(
                    _MS_BASE.format(tenant=tenant_id or config.SHAREPOINT_OAUTH_TENANT_ID or "common") + "/token",
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
        # Kept so an integration can stay signed in past the ~1h access token lifetime.
        # Microsoft returns it because `offline_access` is in SHAREPOINT_OAUTH_SCOPES.
        refresh_token = token_data.get("refresh_token", "")
        expires_at = int(time.time()) + int(token_data.get("expires_in", 3600))
        username = ""
        if access_token:
            try:
                username = await self._get_username(access_token)
            except httpx.HTTPError as exc:
                logger.warning(f"SharePoint PKCE: could not retrieve username: {exc}")

        stored = self._redis_set_result(
            result_key,
            {
                "status": "success",
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_at": expires_at,
                "username": username,
                "user_id": user_id,
            },
            _RESULT_TTL,
        )
        if not stored:
            return CallbackResult(False, _AUTH_UNAVAILABLE_MSG, 503)

        return CallbackResult(True, "Authentication successful.", 200)

    def _read_result(self, state: str, user_id: str) -> Optional[dict]:
        """Read a completed flow result, verifying it belongs to the requesting user.

        The result is intentionally left in Redis (it expires with _RESULT_TTL) so that
        saving an integration can read the tokens after the frontend has already polled
        for status. Mirrors the Google OAuth flow service.
        """
        try:
            raw = self._redis.get(f"{_RESULT_KEY_PREFIX}{state}")
        except Exception as exc:
            logger.error(f"SharePoint PKCE: failed to read status from Redis: {exc}")
            raise ExtendedHTTPException(502, "Failed to read authentication status")

        if raw is None:
            return None

        result = json.loads(self._enc.decrypt(raw.decode()))

        if result.get("user_id") != user_id:
            raise ExtendedHTTPException(403, "Forbidden")

        return result

    async def get_status(self, state: str, user_id: str) -> dict:
        result = self._read_result(state, user_id)

        if result is None:
            return {"status": "pending"}

        if result.get("status") == "success":
            # The refresh token is deliberately not returned - it is a long-lived
            # credential and is read server-side when the integration is saved.
            return {
                "status": "success",
                "access_token": result.get("access_token", ""),
                "username": result.get("username", ""),
            }
        return {"status": "error", "message": result.get("message", "Authentication failed.")}

    def populate_credentials_from_flow(self, oauth_state: str, user_id: str) -> List["CredentialValues"]:
        """Turn a completed sign-in into SharePoint integration credential values.

        Called when a SharePoint setting is saved with an `oauth_state`, so the tokens
        travel Redis -> backend -> Settings without passing through the browser.

        Consumes the flow result atomically: two concurrent saves with the same
        `oauth_state` (double-click on Save, client retry) must not both produce a
        Settings row sharing one refresh token.
        """
        from codemie.rest_api.models.settings import CredentialValues

        try:
            raw = self._redis.getdel(f"{_RESULT_KEY_PREFIX}{oauth_state}")
        except Exception as exc:
            logger.error(f"SharePoint PKCE: failed to consume result from Redis: {exc}")
            raise ExtendedHTTPException(502, "Failed to read authentication status")

        if raw is None:
            raise ExtendedHTTPException(
                400,
                "SharePoint sign-in not completed",
                "Click 'Sign in with Microsoft' and complete authentication before saving.",
            )

        result = json.loads(self._enc.decrypt(raw.decode()))

        # State is bound to the initiating user at creation; a mismatch here means
        # this save is not from the user who signed in. Do not restore the payload —
        # a mismatched consumer is either a bug or an attack.
        if result.get("user_id") != user_id:
            raise ExtendedHTTPException(403, "Forbidden")

        if result.get("status") != "success":
            raise ExtendedHTTPException(
                400,
                "SharePoint sign-in not completed",
                "Click 'Sign in with Microsoft' and complete authentication before saving.",
            )

        access_token = result.get("access_token", "")
        if not access_token:
            raise ExtendedHTTPException(
                502, "SharePoint sign-in incomplete", "Authentication did not return an access token."
            )

        return [
            CredentialValues(key="auth_type", value="oauth"),
            CredentialValues(key="access_token", value=access_token),
            CredentialValues(key="refresh_token", value=result.get("refresh_token", "")),
            CredentialValues(key="expires_at", value=str(result.get("expires_at", 0))),
            CredentialValues(key="username", value=result.get("username", "")),
        ]


def get_valid_access_token(setting) -> Optional[str]:
    """Return a usable delegated access token for a SharePoint setting.

    Refreshes and stores it first when it is missing or close to expiry, so the caller
    hands the tool a token it can actually use. Returns None for app-auth settings,
    which mint their own token per call.

    The tool cannot do this itself: `codemie_tools` must not import `codemie`, so it can
    reach neither the app registration the user signed in with nor the Settings record.
    Mirrors `google_oauth.token_manager`, which also resolves a valid token before use.

    Never raises. A failed refresh returns the stored token unchanged, and the tool
    reports that the integration needs reconnecting when Graph rejects it.
    """
    values = setting.normalize_values()
    if values.get("auth_type") not in _DELEGATED_AUTH_TYPES:
        return None

    access_token = values.get("access_token") or ""
    refresh_token = values.get("refresh_token") or ""

    if not refresh_token or not _needs_refresh(access_token, values.get("expires_at")):
        return access_token

    with _refresh_lock(setting.id) as locked:
        # Re-read under the lock: whoever held it before us has already refreshed and
        # stored a new pair, and spending our copy of the refresh token would trade one
        # Entra has just invalidated.
        current = _reload_credentials(setting.id) or values
        access_token = current.get("access_token") or access_token
        refresh_token = current.get("refresh_token") or refresh_token

        if not _needs_refresh(access_token, current.get("expires_at")):
            return access_token

        if not locked:
            # Redis is down or the holder outlived the wait. Refreshing unserialized
            # risks losing a rotated token, but blocking the run is worse.
            logger.warning(f"SharePoint: refreshing setting {setting.id} without the refresh lock")

        refreshed = _exchange_refresh_token(refresh_token)
        if not refreshed:
            return access_token

        _store_refreshed_token(setting.id, refreshed)
        return refreshed["access_token"]


@contextlib.contextmanager
def _refresh_lock(setting_id):
    """Serialize token refresh for one setting across workers.

    Entra rotates refresh tokens, so two concurrent refreshes race: both spend the same
    stored token, and the slower write buries the newer one — signing the user out an
    hour later with nothing in the logs to explain it. The sign-in flow that produces
    these settings already requires Redis, so the lock lives there and holds across
    processes, which an in-process lock would not.

    Yields True while the lock is held and False when it could not be taken; a Redis
    outage degrades to the previous unserialized behaviour rather than failing the run.
    """
    key = f"{_REFRESH_LOCK_KEY_PREFIX}{setting_id}"
    token = secrets.token_urlsafe(16).encode()

    try:
        client = create_redis_client()
        deadline = time.monotonic() + _REFRESH_LOCK_WAIT
        while True:
            acquired = bool(client.set(key, token, nx=True, ex=_REFRESH_LOCK_TTL))
            if acquired or time.monotonic() >= deadline:
                break
            time.sleep(_REFRESH_LOCK_POLL)
    except Exception as exc:
        logger.warning(f"SharePoint: refresh lock unavailable for setting {setting_id}: {exc}")
        yield False
        return

    try:
        yield acquired
    finally:
        if acquired:
            with contextlib.suppress(Exception):
                client.eval(_RELEASE_LOCK_SCRIPT, 1, key, token)


def _reload_credentials(setting_id) -> Optional[dict]:
    """Re-read the stored credentials, decrypted, as `_build_config` would see them."""
    from codemie.rest_api.models.settings import Settings
    from codemie.service.settings.settings import SettingsService

    try:
        setting = Settings.get_by_id(id_=str(setting_id))
        if not setting:
            return None
        SettingsService._decrypt_credentials(setting)
        return setting.normalize_values()
    except Exception as exc:
        # Fall back to the caller's copy: a stale read only costs an extra refresh.
        logger.warning(f"SharePoint: could not re-read setting {setting_id} before refresh: {exc}")
        return None


def _needs_refresh(access_token: str, expires_at) -> bool:
    if not access_token:
        return True
    try:
        expiry = int(expires_at or 0)
    except (TypeError, ValueError):
        expiry = 0
    # An unknown expiry is left alone: the token is used until Graph rejects it.
    return bool(expiry) and time.time() + _REFRESH_BUFFER >= expiry


def _exchange_refresh_token(refresh_token: str) -> Optional[dict]:
    """Trade the refresh token for a new one at Entra. Returns None when that fails."""
    # The delegated sign-in always runs against the platform app registration, so the
    # client id is not stored per integration. It is a public client: no secret is sent.
    tenant = config.SHAREPOINT_OAUTH_TENANT_ID or "common"
    try:
        response = requests.post(
            _MS_BASE.format(tenant=tenant) + "/token",
            data={
                "client_id": config.SHAREPOINT_OAUTH_CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            timeout=_REFRESH_TIMEOUT,
        )
    except Exception as exc:
        logger.warning(f"SharePoint: token refresh request failed: {exc}")
        return None

    if response.status_code >= 400:
        # invalid_grant means the user must sign in again; anything else is transient.
        # Either way the stored token is kept and the tool reports the rejection.
        logger.warning(f"SharePoint: token refresh rejected with HTTP {response.status_code}")
        return None

    try:
        token_data = response.json()
    except ValueError:
        logger.warning("SharePoint: token refresh returned a non-JSON body")
        return None

    if not token_data.get("access_token"):
        logger.warning("SharePoint: token refresh returned no access token")
        return None

    try:
        expires_in = int(token_data.get("expires_in", 3600))
    except (TypeError, ValueError):
        expires_in = 3600

    return {
        "access_token": token_data["access_token"],
        # Entra rotates refresh tokens; keep the current one when it issues no new one.
        "refresh_token": token_data.get("refresh_token") or refresh_token,
        "expires_at": int(time.time()) + expires_in,
    }


def _store_refreshed_token(setting_id, refreshed: dict) -> None:
    from codemie.service.settings.settings import SettingsService

    try:
        SettingsService.update_sharepoint_oauth_tokens(
            str(setting_id),
            access_token=refreshed["access_token"],
            refresh_token=refreshed["refresh_token"],
            expires_at=refreshed["expires_at"],
        )
    except Exception as exc:
        # A failed write must not break the run that just obtained a valid token, but it
        # is not a transient annoyance either: Entra has already rotated the refresh
        # token, so the one still in the database is dead and every later refresh will
        # fail until the user signs in again. Logged with the setting id so the broken
        # integration can be found from the alert.
        logger.error(f"SharePoint: could not persist refreshed token for setting {setting_id}: {exc}")
