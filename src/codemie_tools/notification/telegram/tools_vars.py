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

from codemie_tools.base.models import ToolMetadata
from codemie_tools.notification.telegram.models import TelegramConfig

TELEGRAM_TOOL = ToolMetadata(
    name="Telegram",
    description="""
    Tool to interact with the Telegram Bot API.
    You must provide the following args: relative_url, method, params.
    1. 'method': The HTTP method, e.g. 'GET', 'POST', 'PUT', 'DELETE' etc.
    2. 'relative_url': Required relative URI of the Telegram Bot API. E.g. 'sendMessage'.
    Do not include query parameters in the URL, they must be provided separately in 'params'.
    3. 'params': Optional JSON of parameters to be sent in request body or query params. MUST be string with valid JSON.
    Important: to send message, you MUST get "chat_id" parameter first.
    """,
    label="Telegram",
    user_description="""
    Provides access to the Telegram Bot API, enabling interaction with Telegram users and groups through a bot. This tool allows the AI assistant to send messages, receive updates, and perform various bot-related operations on the Telegram platform.
    Before using it, it is necessary to add a new integration for the tool by providing:
    1. Alias (A friendly name for the Telegram bot integration)
    2. Telegram Bot Token
    Usage Note:
    Use this tool when you need to interact with Telegram users or groups via a bot, such as sending notifications, responding to commands, or managing group chats.
    """.strip(),
    settings_config=True,
    config_class=TelegramConfig,
)
