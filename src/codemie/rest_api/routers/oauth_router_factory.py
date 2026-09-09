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

"""Factory that builds a tool OAuth router from a per-provider configuration.

The GitLab, Jira, and Confluence OAuth routers exposed the same endpoints (``/initiate``,
``/callback``, ``/connect``, ``/connection``) with only the provider label, config flag,
credential type, prefix, flow service, and a couple of GitLab-specific wrinkles (``instance_url``,
its own callback) differing. This factory captures that shared shape so each provider router file is
reduced to a configuration declaration.

NOTE: this module must NOT use ``from __future__ import annotations`` — the ``/initiate`` endpoint's
request-body type is a per-provider model passed in via config, and FastAPI must see the real type
object at decoration time rather than a deferred string annotation.
"""

from dataclasses import dataclass
from typing import Callable, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from codemie.configs import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.utils import get_api_root_path
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.encryption.encryption_factory import EncryptionFactory
from codemie.service.oauth.authorization import load_authorized_setting
from codemie.service.oauth.token_port import ToolOAuthTokenPort, map_tms_error_to_http
from codemie.utils.oauth_html_utils import (
    build_tool_oauth_callback_script,
    html_callback_page,
    html_error_page,
    tool_oauth_callback_target_origin,
)

_CALLBACK_SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'",
    "X-Frame-Options": "DENY",
}


class InitiateOAuthRequest(BaseModel):
    """App-credential overrides accepted by ``/initiate`` (all optional — the form supplies them)."""

    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    callback_base_url: Optional[str] = None


class ConnectOAuthRequest(BaseModel):
    setting_id: str
    # When true, run the flow without saving the token (a "Test" from the setting form). The default
    # connects: the callback writes the calling user's token to TMS under this integration.
    test: bool = False


@dataclass(frozen=True)
class OAuthRouterConfig:
    """Per-provider configuration consumed by :func:`build_oauth_router`."""

    prefix: str
    tag: str
    provider_label: str
    enabled: Callable[[], bool]
    # EPMCDME-14586/14587: the folded OAuth provider key ('jira' | 'confluence' | 'gitlab') this
    # router serves, resolved from a setting's base type + auth_type=oauth marker.
    provider: str
    flow_service_factory: Callable[[], object]
    missing_app_credentials_message: str
    initiate_model: type[BaseModel] = InitiateOAuthRequest
    # App-credential keys beyond client_id/client_secret/callback_base_url (e.g. GitLab instance_url).
    extra_app_keys: tuple[str, ...] = ()
    # GitLab and Jira serve their own /callback; Confluence reuses the shared Atlassian callback.
    mount_callback: bool = False
    callback_enabled: Optional[Callable[[], bool]] = None
    callback_disabled_html_message: Optional[str] = None


def _authorize_setting(cfg: OAuthRouterConfig, setting_id: str, user: User) -> None:
    """Confirm the caller may act on this integration before touching its token state."""
    load_authorized_setting(setting_id, user, cfg.provider, cfg.provider_label)


def _app_credentials_from_setting(cfg: OAuthRouterConfig, setting_id: str, user: User) -> dict:
    """Load a shared integration's OAuth app credentials for a per-user connect flow.

    The caller must be authorized for the integration before its app configuration is read —
    the generated authorize URL embeds ``client_id`` (and, for GitLab, ``instance_url``).
    """
    setting = load_authorized_setting(setting_id, user, cfg.provider, cfg.provider_label)
    creds = {c.key: c.value for c in setting.credential_values}
    encrypted_secret = creds.get("client_secret", "")
    client_secret = None
    if encrypted_secret:
        try:
            client_secret = EncryptionFactory().get_current_encryption_service().decrypt(encrypted_secret)
        except Exception as exc:
            logger.error(f"{cfg.provider_label} OAuth: failed to decrypt client_secret: {exc}")
            raise ExtendedHTTPException(502, "OAuth credential service is temporarily unavailable. Please try again.")
    app = {
        "client_id": creds.get("client_id") or "",
        "client_secret": client_secret,
        "callback_base_url": creds.get("callback_base_url") or "",
    }
    for key in cfg.extra_app_keys:
        app[key] = creds.get(key) or ""
    if not all(app[key] for key in ("client_id", "client_secret", "callback_base_url", *cfg.extra_app_keys)):
        raise ExtendedHTTPException(400, cfg.missing_app_credentials_message)
    return app


def _register_callback(router: APIRouter, cfg: OAuthRouterConfig) -> None:
    """Mount the provider's own ``/callback`` + ``/callback-page.js`` (GitLab and Jira; Confluence
    reuses Jira's)."""

    callback_script_path = f"{get_api_root_path()}{cfg.prefix}/callback-page.js"

    @router.get("/callback")
    def oauth_callback(
        code: Optional[str] = Query(default=None, max_length=2048),
        state: Optional[str] = Query(default=None, max_length=1024),
        error: Optional[str] = Query(default=None, max_length=256),
    ) -> HTMLResponse:
        """Provider redirects here after the user consents. Renders a page whose external script
        hands the result to the opener window via postMessage (correlated by ``state``) and closes."""
        if cfg.callback_enabled is not None and not cfg.callback_enabled():
            return HTMLResponse(
                content=html_error_page(cfg.callback_disabled_html_message or "OAuth is disabled."),
                status_code=503,
                headers=_CALLBACK_SECURITY_HEADERS,
            )
        result = cfg.flow_service_factory().handle_callback(code, state, error)
        content = html_callback_page(
            success=result.success,
            message=result.message,
            state=state or "",
            target_origin=tool_oauth_callback_target_origin(),
            script_src=callback_script_path,
            username=result.username,
            error="" if result.success else result.message,
        )
        return HTMLResponse(content=content, status_code=result.status_code, headers=_CALLBACK_SECURITY_HEADERS)

    @router.get("/callback-page.js")
    def oauth_callback_page_script() -> Response:
        """Static JS the callback page loads (served same-origin so the ``script-src 'self'`` CSP
        allows it): postMessages the result to the opener and closes the popup."""
        return Response(
            content=build_tool_oauth_callback_script(),
            media_type="application/javascript",
            headers=_CALLBACK_SECURITY_HEADERS,
        )


def build_oauth_router(cfg: OAuthRouterConfig) -> APIRouter:
    """Build the standard tool OAuth router for one provider from ``cfg``."""
    router = APIRouter(tags=[cfg.tag], prefix=cfg.prefix)
    initiate_model = cfg.initiate_model

    def _service():
        return cfg.flow_service_factory()

    def _require_enabled() -> None:
        if not cfg.enabled():
            raise ExtendedHTTPException(503, f"{cfg.provider_label} OAuth is disabled")

    @router.post("/initiate")
    def initiate_oauth(
        request: initiate_model | None = None,
        user: User = Depends(authenticate),
    ) -> JSONResponse:
        """Initiate OAuth (Authorization Code + PKCE) using the app credentials from the form.

        ``/initiate`` has no integration to save under, so it is always a test: it validates the app
        credentials via a real flow without persisting a token.
        """
        _require_enabled()
        overrides = request.model_dump() if request else {}
        result = _service().initiate_flow(user.id, persist_token=False, **overrides)
        return JSONResponse(content=result)

    if cfg.mount_callback:
        _register_callback(router, cfg)

    @router.post("/connect")
    def connect_oauth(request: ConnectOAuthRequest, user: User = Depends(authenticate)) -> JSONResponse:
        """Initiate OAuth for the calling user against an existing integration's app credentials.

        Lets a project member authorize under their own account without re-entering the
        admin-provided client_id / client_secret. With ``test=true`` the flow runs without
        persisting the token (a "Test" from the setting form).
        """
        _require_enabled()
        app = _app_credentials_from_setting(cfg, request.setting_id, user)
        result = _service().initiate_flow(
            user.id, integration_id=request.setting_id, persist_token=not request.test, **app
        )
        result["setting_id"] = request.setting_id
        return JSONResponse(content=result)

    @router.get("/connection")
    def connection_status(setting_id: str = Query(...), user: User = Depends(authenticate)) -> JSONResponse:
        """Return only the calling user's connection state for a shared integration.

        Never reveals whether other users have connected.
        """
        _require_enabled()
        _authorize_setting(cfg, setting_id, user)
        try:
            token_data = ToolOAuthTokenPort.get_oauth2_token_or_none(user_id=user.id, integration_id=setting_id)
        except Exception as exc:
            raise map_tms_error_to_http(cfg.provider_label, exc)
        if token_data and token_data.access_token:
            return JSONResponse(content={"status": "connected", "username": token_data.provider_username or ""})
        return JSONResponse(content={"status": "not_connected", "username": ""})

    @router.delete("/connection")
    def disconnect(setting_id: str = Query(...), user: User = Depends(authenticate)) -> JSONResponse:
        """Revoke at the provider (best effort) and remove only the calling user's own token row."""
        _require_enabled()
        _authorize_setting(cfg, setting_id, user)
        try:
            _service().revoke_connection(user.id, setting_id)
        except Exception as exc:
            raise map_tms_error_to_http(cfg.provider_label, exc)
        return JSONResponse(content={"status": "disconnected"})

    return router
