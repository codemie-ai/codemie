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

from __future__ import annotations

import html
from urllib.parse import urlsplit

from fastapi import status
from fastapi.responses import HTMLResponse, Response

from codemie.configs import config
from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException

from ._common import CallbackPageError
from ._constants import (
    _CALLBACK_EVENT_TYPE,
    _CALLBACK_FALLBACK_DELAY_MS,
    _CALLBACK_SECURITY_HEADERS,
    _CALLBACK_SUCCESS_CLOSE_MESSAGE,
    _CALLBACK_SUCCESS_MESSAGE,
    _CALLBACK_SUCCESS_OPEN_CODEMIE_MESSAGE,
    _CALLBACK_TRANSITION_MESSAGE,
    _OAUTH2_CALLBACK_DIAGNOSTICS_PATH,
    _OAUTH2_CALLBACK_PAGE_SCRIPT_PATH,
)


def _build_callback_page(
    *,
    title: str,
    message: str,
    outcome: str,
    server_name: str | None = None,
    auth_config_id: str | None = None,
    error_code: str | None = None,
    bridge_error_code: str | None = None,
    error_description: str | None = None,
    error_uri: str | None = None,
    noscript_message: str | None = None,
) -> HTMLResponse:
    escaped_title = html.escape(title)
    escaped_message = html.escape(message)
    bootstrap_attributes = [f'data-callback-result="{html.escape(outcome, quote=True)}"']
    if auth_config_id:
        target_origin = _derive_callback_target_origin()
        bootstrap_attributes.extend(
            [
                f'data-auth-config-id="{html.escape(auth_config_id, quote=True)}"',
                f'data-target-origin="{html.escape(target_origin, quote=True)}"',
            ]
        )
        if error_code:
            bootstrap_attributes.append(f'data-idp-error-code="{html.escape(error_code, quote=True)}"')
        if bridge_error_code:
            bootstrap_attributes.append(f'data-bridge-error-code="{html.escape(bridge_error_code, quote=True)}"')

    details: list[str] = []
    if server_name:
        details.append(f"<p>MCP server: <strong>{html.escape(server_name)}</strong></p>")
    if error_code:
        details.append(f"<p>Identity provider error: <code>{html.escape(error_code)}</code></p>")
    if error_description:
        details.append(f"<p>{html.escape(error_description)}</p>")
    if error_uri:
        escaped_error_uri = html.escape(error_uri, quote=True)
        details.append(f'<p><a href="{escaped_error_uri}">{escaped_error_uri}</a></p>')

    if noscript_message:
        details.append(f"<noscript><p>{html.escape(noscript_message)}</p></noscript>")

    content = "".join(
        [
            "<!DOCTYPE html>",
            "<html lang=\"en\">",
            "<head>",
            "<meta charset=\"utf-8\">",
            "<title>CodeMie MCP Authentication</title>",
            "</head>",
            "<body>",
            f"<main {' '.join(bootstrap_attributes)}>",
            f"<h1>{escaped_title}</h1>",
            f"<p data-callback-message>{escaped_message}</p>",
            *details,
            f"<script src=\"{_OAUTH2_CALLBACK_PAGE_SCRIPT_PATH}\"></script>",
            "</main>",
            "</body>",
            "</html>",
        ]
    )
    return HTMLResponse(content=content, status_code=status.HTTP_200_OK, headers=_CALLBACK_SECURITY_HEADERS)


def _build_success_callback_response(server_name: str | None, auth_config_id: str) -> HTMLResponse:
    logger.info(
        "MCP auth callback success page served: "
        f"auth_config_id={auth_config_id} target_origin={_safe_target_origin()} server_name={server_name}"
    )
    return _build_callback_page(
        title="Authentication complete",
        message=_CALLBACK_TRANSITION_MESSAGE,
        outcome="success",
        server_name=server_name,
        auth_config_id=auth_config_id,
        noscript_message=_CALLBACK_SUCCESS_MESSAGE,
    )


def _build_error_callback_response(error: CallbackPageError) -> HTMLResponse:
    logger.warning(
        "MCP auth callback error page served: "
        f"auth_config_id={error.auth_config_id} target_origin={_safe_target_origin()} "
        f"bridge_error_code={error.bridge_error_code} idp_error_code={error.error_code}"
    )
    return _build_callback_page(
        title=error.title,
        message=error.message,
        outcome="error",
        server_name=error.server_name,
        auth_config_id=error.auth_config_id,
        error_code=error.error_code,
        bridge_error_code=error.bridge_error_code,
        error_description=error.error_description,
        error_uri=error.error_uri,
    )


def _derive_callback_target_origin() -> str:
    parsed_origin = urlsplit(config.FRONTEND_URL)
    if not parsed_origin.scheme or not parsed_origin.netloc:
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="MCP auth callback origin is invalid",
            details="FRONTEND_URL must include scheme and host for callback postMessage target origin.",
            help="Set FRONTEND_URL to the exact frontend URL and retry.",
        )
    return f"{parsed_origin.scheme}://{parsed_origin.netloc}"


def _safe_target_origin() -> str | None:
    """Best-effort target origin for diagnostics logging; never raises."""
    try:
        return _derive_callback_target_origin()
    except Exception:
        return None


def _build_trusted_callback_error(
    message: str,
    *,
    auth_config_id: str,
    bridge_error_code: str,
    server_name: str | None = None,
    title: str = "Authentication could not be completed",
) -> CallbackPageError:
    return CallbackPageError(
        message,
        server_name=server_name,
        title=title,
        auth_config_id=auth_config_id,
        bridge_error_code=bridge_error_code,
    )


def build_oauth2_callback_page_script_response() -> Response:
    callback_script = f"""
const CALLBACK_EVENT_TYPE = '{_CALLBACK_EVENT_TYPE}';
const CALLBACK_SUCCESS_CLOSE_MESSAGE = '{_CALLBACK_SUCCESS_CLOSE_MESSAGE}';
const CALLBACK_SUCCESS_OPEN_CODEMIE_MESSAGE = '{_CALLBACK_SUCCESS_OPEN_CODEMIE_MESSAGE}';
const CALLBACK_FALLBACK_DELAY_MS = {_CALLBACK_FALLBACK_DELAY_MS};
const CALLBACK_DIAGNOSTICS_URL = '{_OAUTH2_CALLBACK_DIAGNOSTICS_PATH}';
const CALLBACK_KEEP_TAB_OPEN = {str(config.MCP_AUTH_CALLBACK_KEEP_TAB_OPEN).lower()};

const main = document.querySelector('main[data-callback-result]');

if (main instanceof HTMLElement) {{
  const message = main.querySelector('[data-callback-message]');
  const authConfigId = main.dataset.authConfigId;
  const targetOrigin = main.dataset.targetOrigin;
  const errorCode = main.dataset.idpErrorCode || main.dataset.bridgeErrorCode;

  const updateMessage = (text) => {{
    if (message instanceof HTMLElement) {{
      message.textContent = text;
    }}
  }};

  // Diagnostics-only beacon: report whether this page could notify the opener
  // window. Fired before postMessage/window.close so it survives the tab closing.
  // Never throws - diagnostics must not break the auth flow. Carries no secrets.
  const sendDiagnostics = (extra) => {{
    try {{
      const payload = Object.assign({{
        result: main.dataset.callbackResult,
        auth_config_id: authConfigId || null,
        target_origin: targetOrigin || null,
        opener_present: !!window.opener,
        post_message_attempted: false,
        post_message_error: null,
        window_should_close: false,
        bridge_error_code: main.dataset.bridgeErrorCode || null,
        idp_error_code: main.dataset.idpErrorCode || null,
      }}, extra || {{}});
      navigator.sendBeacon(
        CALLBACK_DIAGNOSTICS_URL,
        new Blob([JSON.stringify(payload)], {{ type: 'application/json' }})
      );
    }} catch (e) {{
      /* diagnostics must never break the auth flow */
    }}
  }};

  if (main.dataset.callbackResult === 'success') {{
    if (!window.opener) {{
      // The opener is cut on purpose (an IdP opener guard must not be able to
      // close the sign-in window), so CodeMie learns of success by polling
      // /status and this tab has to close itself. opener_present is null, not false:
      // the opener is absent by design, which must not log as a lost opener.
      sendDiagnostics({{ opener_present: null, window_should_close: !CALLBACK_KEEP_TAB_OPEN }});
      if (!CALLBACK_KEEP_TAB_OPEN) {{
        window.close();
        window.setTimeout(() => {{
          if (!window.closed) {{
            updateMessage(CALLBACK_SUCCESS_OPEN_CODEMIE_MESSAGE);
          }}
        }}, CALLBACK_FALLBACK_DELAY_MS);
      }} else {{
        updateMessage(CALLBACK_SUCCESS_OPEN_CODEMIE_MESSAGE);
      }}
    }} else if (authConfigId && targetOrigin) {{
      let postMessageAttempted = false;
      let postMessageError = null;
      try {{
        window.opener.postMessage({{
          type: CALLBACK_EVENT_TYPE,
          status: 'success',
          auth_config_id: authConfigId,
        }}, targetOrigin);
        postMessageAttempted = true;
      }} catch (err) {{
        postMessageError = String((err && err.message) || err);
      }}
      sendDiagnostics({{
        post_message_attempted: postMessageAttempted,
        post_message_error: postMessageError,
        window_should_close: !CALLBACK_KEEP_TAB_OPEN,
      }});
      if (!CALLBACK_KEEP_TAB_OPEN) {{
        window.close();
        window.setTimeout(() => {{
          if (!window.closed) {{
            updateMessage(CALLBACK_SUCCESS_CLOSE_MESSAGE);
          }}
        }}, CALLBACK_FALLBACK_DELAY_MS);
      }} else {{
        updateMessage(CALLBACK_SUCCESS_CLOSE_MESSAGE);
      }}
    }}
  }}

  if (main.dataset.callbackResult === 'error') {{
    // Known limit: an IdP error code can only travel to an opener. Windows the UI opens
    // have none (it is cut on purpose), so for them this branch only reports diagnostics
    // and the UI shows its generic hint or early-close wording, never the provider error.
    if (window.opener && authConfigId && targetOrigin && errorCode) {{
      let postMessageAttempted = false;
      let postMessageError = null;
      try {{
        window.opener.postMessage({{
          type: CALLBACK_EVENT_TYPE,
          status: 'error',
          error: errorCode,
          auth_config_id: authConfigId,
        }}, targetOrigin);
        postMessageAttempted = true;
      }} catch (err) {{
        postMessageError = String((err && err.message) || err);
      }}
      sendDiagnostics({{
        post_message_attempted: postMessageAttempted,
        post_message_error: postMessageError,
      }});
    }} else {{
      sendDiagnostics({{}});
    }}
  }}
}}
""".strip()
    return Response(content=callback_script, media_type="application/javascript")
