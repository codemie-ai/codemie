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

import json
from typing import Optional, Dict, Any, Type

import requests
from pydantic import BaseModel, Field

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.notification.telegram.models import TelegramConfig
from codemie_tools.notification.telegram.tools_vars import TELEGRAM_TOOL


class TelegramToolInput(BaseModel):
    method: str = Field(..., description="The HTTP method to use for the request (GET, POST). Required parameter.")
    relative_url: str = Field(
        ...,
        description="""
        The relative URL of the Telegram Bot API to call, e.g. 'sendMessage'. Required parameter.
        In case of GET method, you MUST include query parameters in the URL.
        """,
    )
    params: Optional[str] = Field(
        default="",
        description="""
        Optional JSON of parameters to be sent in request body or query params. MUST be string with valid JSON.
        Important: to send message, you MUST get "chat_id" parameter first.
        """,
    )


class TelegramTool(CodeMieTool):
    config: TelegramConfig
    name: str = TELEGRAM_TOOL.name
    description: str = TELEGRAM_TOOL.description
    args_schema: Type[BaseModel] = TelegramToolInput

    def execute(self, method: str, relative_url: str, params: Optional[str] = "") -> str:
        """
        Execute a Telegram Bot API request.

        Args:
            method: The HTTP method to use (GET, POST, etc.)
            relative_url: The relative URL of the Telegram API endpoint
            params: JSON string of parameters to send

        Returns:
            Response from the Telegram API
        """
        if not relative_url.startswith("/"):
            relative_url = f"/{relative_url}"

        # If config is None, raise a meaningful error
        if self.config is None:
            raise ValueError("Telegram config is provided set. Please set it before using the tool.")

        if not self.config.bot_token:
            raise ValueError("Telegram token is not set. Please provide it before using the tool.")

        base_url = f"https://api.telegram.org/bot{self.config.bot_token}"
        full_url = f"{base_url}{relative_url}"
        headers = {"Content-Type": "application/json"}
        payload_params = self._parse_payload_params(params)
        response = requests.request(method, full_url, headers=headers, json=payload_params)
        response.raise_for_status()
        return response.text

    def _parse_payload_params(self, params: Optional[str]) -> Dict[str, Any]:
        """Parse JSON string into dictionary for request parameters"""
        if params:
            json_acceptable_string = params.replace("'", '"')
            return json.loads(json_acceptable_string)
        return {}

    def _healthcheck(self):
        """
        Check if the Telegram Bot API token is valid.

        Returns:
            Tuple of (success: bool, error_message: str)
        """
        if self.config is None:
            raise ValueError("Telegram config is not set. Please set it before using the tool.")
        response = requests.get(f"https://api.telegram.org/bot{self.config.bot_token}/getMe")
        response.raise_for_status()
