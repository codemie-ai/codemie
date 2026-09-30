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

from typing import Literal

from fastapi import status
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from codemie.configs.logger import logger
from codemie.enterprise.mcp_auth._common import _sanitize_log_value


class OAuth2CallbackDiagnostics(BaseModel):
    """Client-reported outcome of the OAuth2 callback bridge page.

    Diagnostics only. The bridge page (which closes itself) reports whether it
    could notify the opener window, so the otherwise-unobservable client step
    lands in the backend logs. Carries no secrets — only non-secret identifiers,
    origins, booleans, and error codes. Unknown fields are ignored, never logged.
    """

    model_config = ConfigDict(extra="ignore")

    result: Literal["success", "error", "timeout"]
    auth_config_id: str | None = Field(default=None, max_length=256)
    # Optional: only the bridge page has a window.opener to report on. A timeout is reported by
    # the parent window, which omits it rather than asserting a meaningless false. The bridge page
    # also sends null when the UI cut the opener on purpose, so that success logs at INFO: only an
    # explicit False means a lost opener.
    opener_present: bool | None = None
    target_origin: str | None = Field(default=None, max_length=256)
    post_message_attempted: bool = False
    post_message_error: str | None = Field(default=None, max_length=512)
    window_should_close: bool = False
    bridge_error_code: str | None = Field(default=None, max_length=128)
    idp_error_code: str | None = Field(default=None, max_length=128)
    waited_ms: int | None = Field(default=None, ge=0, le=3_600_000)
    phase: str | None = Field(default=None, max_length=64)


def build_oauth2_callback_diagnostics_response(payload: OAuth2CallbackDiagnostics) -> Response:
    """Log the client-side callback outcome and return an empty 204.

    Failure-shaped outcomes (an ``error`` result, a lost ``window.opener``, or a
    ``postMessage`` exception) log at WARNING so they are easy to find; clean
    successes log at INFO. A ``timeout`` result — the client gave up waiting for
    a callback that never arrived — logs its own distinct WARNING message.
    """

    if payload.result == "timeout":
        logger.warning(
            "MCP OAuth2 callback never observed by client: "
            f"auth_config_id={_sanitize_log_value(payload.auth_config_id)} "
            f"waited_ms={payload.waited_ms} "
            f"phase={_sanitize_log_value(payload.phase)} "
            f"target_origin={_sanitize_log_value(payload.target_origin)}"
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    message = (
        "MCP OAuth2 callback client diagnostics: "
        f"result={payload.result} auth_config_id={_sanitize_log_value(payload.auth_config_id)} "
        f"opener_present={payload.opener_present} target_origin={_sanitize_log_value(payload.target_origin)} "
        f"post_message_attempted={payload.post_message_attempted} "
        f"post_message_error={_sanitize_log_value(payload.post_message_error)} "
        f"window_should_close={payload.window_should_close} "
        f"bridge_error_code={_sanitize_log_value(payload.bridge_error_code)} "
        f"idp_error_code={_sanitize_log_value(payload.idp_error_code)} "
        f"phase={_sanitize_log_value(payload.phase)}"
    )
    if payload.result == "error" or payload.opener_present is False or payload.post_message_error:
        logger.warning(message)
    else:
        logger.info(message)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
