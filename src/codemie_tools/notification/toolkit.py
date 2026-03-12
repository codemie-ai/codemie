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

from typing import List

from codemie_tools.base.base_toolkit import DiscoverableToolkit
from codemie_tools.base.models import ToolKit, ToolSet, Tool
from codemie_tools.notification.email.tools import EmailTool
from codemie_tools.notification.email.tools_vars import EMAIL_TOOL
from codemie_tools.notification.telegram.tools import TelegramTool
from codemie_tools.notification.telegram.tools_vars import TELEGRAM_TOOL


class NotificationToolkitUI(ToolKit):
    toolkit: ToolSet = ToolSet.NOTIFICATION
    tools: List[Tool] = [
        Tool.from_metadata(EMAIL_TOOL, tool_class=EmailTool),
        Tool.from_metadata(TELEGRAM_TOOL, tool_class=TelegramTool),
    ]
    label: str = ToolSet.NOTIFICATION.value


class NotificationToolkit(DiscoverableToolkit):
    @classmethod
    def get_definition(cls):
        return NotificationToolkitUI()
