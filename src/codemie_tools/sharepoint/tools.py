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

import json
import logging
import time
from typing import Any, Dict, Optional, Tuple, Type, Union
from urllib.parse import urlparse

import requests
from langchain_core.tools import ToolException
from pydantic import BaseModel, Field, PrivateAttr

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.file_tool_mixin import FileToolMixin
from codemie_tools.sharepoint.models import SharePointConfig
from codemie_tools.sharepoint.tools_vars import SHAREPOINT_TOOL

logger = logging.getLogger(__name__)

# codemie_tools must not import from codemie, so the datasource loader's
# SHAREPOINT_CONFIG timeouts are not reachable here.
_GRAPH_TIMEOUT = 60
_MAX_RETRY_AFTER_SECONDS = 60

_APP_AUTH = "app"
# Values stored by the delegated sign-in flows (integration, PKCE and device code).
_DELEGATED_AUTH = ("oauth", "oauth_codemie", "oauth_custom")

_UNSUPPORTED_AUTH_MESSAGE = (
    "This SharePoint integration uses an unsupported authentication type. Use an integration "
    "configured with an Azure app registration, or one connected with 'Sign in with Microsoft'."
)
_REAUTH_MESSAGE = (
    "Your SharePoint sign-in has expired. Open Settings and reconnect the SharePoint "
    "integration with 'Sign in with Microsoft' to continue."
)
_NOT_SIGNED_IN_MESSAGE = (
    "This SharePoint integration is not signed in. Open Settings and connect it with 'Sign in with Microsoft'."
)


class SharePointInput(BaseModel):
    method: str = Field(..., description="HTTP method: GET, POST, PATCH, PUT, DELETE")
    relative_url: str = Field(
        ...,
        description="""
        Graph API path starting with a single forward slash, relative to https://graph.microsoft.com/v1.0.
        Example: /sites/{site-id}/lists/{list-id}/items.
        Do not include query parameters in the URL, they must be provided separately in 'params'.
        """.strip(),
    )
    params: Union[str, Dict[str, Any], None] = Field(
        default=None,
        description="""
        Optional parameters: query parameters for GET/DELETE, JSON body for POST/PATCH/PUT.
        RECOMMENDED: provide as a dictionary (dict) — this avoids JSON escaping issues.
        Can also accept a JSON string.
        """.strip(),
    )
    raw_content: Optional[str] = Field(
        default=None,
        description="""
        Raw text body for document uploads (PUT .../root:/path/name.ext:/content).
        Overrides 'params' as the request body when provided.
        Use this only for text formats you generate yourself (.txt, .md, .csv, .json, .html).
        To upload a file the user attached to the conversation, use 'file_name' instead.
        """.strip(),
    )
    file_name: Optional[str] = Field(
        default=None,
        description="""
        Name of a file attached to the conversation to upload as the request body, e.g. 'report.docx'.
        Use with PUT .../root:/path/name.ext:/content. Works for any format, including binary ones
        (.docx, .xlsx, .pptx, .pdf, images). Takes precedence over 'raw_content' and 'params'.
        """.strip(),
    )


class SharePointTool(CodeMieTool, FileToolMixin):
    script_callable = True
    config: SharePointConfig
    name: str = SHAREPOINT_TOOL.name
    description: str = SHAREPOINT_TOOL.description
    args_schema: Type[BaseModel] = SharePointInput

    GRAPH_BASE: str = "https://graph.microsoft.com/v1.0"

    _token: Optional[str] = PrivateAttr(default=None)

    def is_safe(self, args: dict) -> bool:
        return self._http_method_is_safe(args)

    @property
    def _is_delegated(self) -> bool:
        return self.config.auth_type in _DELEGATED_AUTH

    def _validate_config(self) -> None:
        if self._is_delegated:
            # App-auth fields are legitimately absent here, so skip the required-field check.
            if not self.config.access_token:
                raise ToolException(_NOT_SIGNED_IN_MESSAGE)
            return
        if self.config.auth_type != _APP_AUTH:
            raise ToolException(_UNSUPPORTED_AUTH_MESSAGE)
        super()._validate_config()

    def _get_token(self) -> str:
        if not self._token:
            if self._is_delegated:
                # The platform refreshes this before the tool runs - the tool holds
                # neither the app registration nor a way to store a rotated token.
                if not self.config.access_token:
                    raise ToolException(_REAUTH_MESSAGE)
                self._token = self.config.access_token
            else:
                self._token = self._acquire_app_token()
        return self._token

    def _acquire_app_token(self) -> str:
        """Acquire a Graph token via the client-credentials grant."""
        response = requests.post(
            f"https://login.microsoftonline.com/{self.config.tenant_id}/oauth2/v2.0/token",
            data={
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            },
            timeout=_GRAPH_TIMEOUT,
        )
        if response.status_code >= 400:
            raise ToolException(
                f"Failed to acquire SharePoint access token (HTTP {response.status_code}). "
                "Verify the tenant ID, client ID and client secret of the integration."
            )
        try:
            token = response.json().get("access_token", "")
        except ValueError:
            token = ""
        if not token:
            raise ToolException(
                "Microsoft's token endpoint returned no access token. "
                "Verify the tenant ID, client ID and client secret of the integration."
            )
        return token

    @staticmethod
    def _validate_relative_url(relative_url: str) -> str:
        relative_url = relative_url.strip()
        if not relative_url.startswith("/") or relative_url.startswith("//"):
            raise ToolException(
                "relative_url must be a Graph API path beginning with a single '/', e.g. /sites/{site-id}/lists"
            )
        return relative_url

    @staticmethod
    def _parse_params(params: Union[str, Dict[str, Any], None]) -> Optional[Dict[str, Any]]:
        if params is None or isinstance(params, dict):
            return params
        stripped = params.strip()
        if not stripped:
            return None
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as e:
            raise ToolException(f"'params' is not valid JSON: {e}. Provide params as a dictionary instead.")
        if not isinstance(parsed, dict):
            raise ToolException("'params' must be a JSON object (dictionary).")
        return parsed

    def _resolve_attachment(self, file_name: str) -> Tuple[bytes, str]:
        """Look up a file attached to the conversation by name and return its bytes and MIME type.

        The bytes never pass through the model: the agent names the file, the tool resolves it.
        """
        available = self._resolve_files()
        if not available:
            raise ToolException(
                f"No files are attached to this conversation, so '{file_name}' cannot be uploaded. "
                "Ask the user to attach the file to the chat first."
            )
        if file_name not in available:
            raise ToolException(
                f"File '{file_name}' is not attached to this conversation. "
                f"Attached files: {', '.join(sorted(available))}."
            )
        return available[file_name]

    @staticmethod
    def _build_request_kwargs(
        method: str,
        params: Optional[Dict[str, Any]],
        raw_content: Optional[str],
        attachment: Optional[Tuple[bytes, str]] = None,
    ) -> Dict[str, Any]:
        if attachment is not None:
            content, mime_type = attachment
            return {"data": content, "headers": {"Content-Type": mime_type or "application/octet-stream"}}
        if raw_content is not None:
            return {"data": raw_content.encode("utf-8"), "headers": {"Content-Type": "application/octet-stream"}}
        if params is None:
            return {}
        if method in ("GET", "DELETE"):
            return {"params": params}
        return {"json": params}

    def _send_request(self, method: str, relative_url: str, request_kwargs: Dict[str, Any]) -> requests.Response:
        """Send a Graph request, retrying once on an expired token (401) and once on throttling (429)."""
        extra_headers = request_kwargs.pop("headers", {})

        def _do_request() -> requests.Response:
            headers = {"Authorization": f"Bearer {self._get_token()}", **extra_headers}
            return requests.request(
                method,
                f"{self.GRAPH_BASE}{relative_url}",
                headers=headers,
                timeout=_GRAPH_TIMEOUT,
                **request_kwargs,
            )

        response = _do_request()
        if response.status_code == 401 and not self._is_delegated:
            # App tokens are minted per call, so a fresh one can clear a rejected token.
            # A delegated token cannot be re-minted here, so retrying it is pointless.
            logger.info("SharePoint: access token rejected, re-acquiring and retrying once")
            self._token = None
            response = _do_request()
        if response.status_code == 429:
            try:
                retry_after = int(response.headers.get("Retry-After", 5))
            except (TypeError, ValueError):
                # Retry-After may legally be an HTTP-date rather than seconds.
                retry_after = 5
            retry_after = max(0, min(retry_after, _MAX_RETRY_AFTER_SECONDS))
            logger.warning(f"SharePoint: rate limited, retrying after {retry_after} seconds")
            time.sleep(retry_after)
            response = _do_request()
        return response

    def execute(
        self,
        method: str,
        relative_url: str,
        params: Union[str, Dict[str, Any], None] = None,
        raw_content: Optional[str] = None,
        file_name: Optional[str] = None,
        *args,
    ) -> str:
        method = method.upper()
        relative_url = self._validate_relative_url(relative_url)
        attachment = self._resolve_attachment(file_name) if file_name else None
        request_kwargs = self._build_request_kwargs(method, self._parse_params(params), raw_content, attachment)

        response = self._send_request(method, relative_url, request_kwargs)

        # Audit trail for every operation. Never log the token or the request body.
        operation = "read" if method == "GET" else "write"
        upload = f" (upload: {file_name}, {len(attachment[0])} bytes)" if attachment else ""
        logger.info(f"SharePoint {operation}: {method} {relative_url}{upload} -> HTTP {response.status_code}")

        if response.status_code == 401 and self._is_delegated:
            # The sign-in is no longer usable; a raw Graph 401 would not tell the user that.
            raise ToolException(_REAUTH_MESSAGE)

        if response.status_code >= 400:
            # Graph error bodies are descriptive and let the agent self-correct.
            raise ToolException(
                f"Graph API error (HTTP {response.status_code}) for {method} {relative_url}: {response.text}"
            )

        return response.text or f"{method} {relative_url}: HTTP {response.status_code} (success)"

    def _healthcheck(self):
        if self._is_delegated:
            if not self.config.access_token:
                raise ValueError(_NOT_SIGNED_IN_MESSAGE)
            token = self._get_token()
        elif self.config.auth_type != _APP_AUTH:
            raise ValueError(_UNSUPPORTED_AUTH_MESSAGE)
        else:
            token = self._acquire_app_token()
        hostname = urlparse(self.config.url).netloc or self.config.url.strip().rstrip("/")
        if not hostname:
            # No tenant URL configured — token acquisition alone proves the credentials.
            return
        response = requests.get(
            f"{self.GRAPH_BASE}/sites/{hostname}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=_GRAPH_TIMEOUT,
        )
        response.raise_for_status()
