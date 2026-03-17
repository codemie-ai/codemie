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
from typing import Optional, Type

from pydantic import BaseModel, Field

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.qa.zephyr_squad.models import ZephyrSquadConfig, ZephyrSquadToolInput
from codemie_tools.qa.zephyr_squad.tools_vars import ZEPHYR_SQUAD_TOOL
from codemie_tools.qa.zephyr_squad.api_wrapper import ZephyrRestAPI

# URL that is used for integration healthcheck
ZEPHYR_SQUAD_HEALTHCHECK_URL = "/serverinfo"
ZEPHYR_SQUAD_ERROR_MSG: str = "Access denied"


class ZephyrSquadGenericTool(CodeMieTool):
    config: Optional[ZephyrSquadConfig] = Field(exclude=True, default=None)
    name: str = ZEPHYR_SQUAD_TOOL.name
    description: str = ZEPHYR_SQUAD_TOOL.description
    args_schema: Type[BaseModel] = ZephyrSquadToolInput

    def _healthcheck(self):
        """Performs a healthcheck by querying the serverinfo endpoint"""
        content = self.execute(relative_path=ZEPHYR_SQUAD_HEALTHCHECK_URL, method="GET")
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError, ValueError):
            raise AssertionError(ZEPHYR_SQUAD_ERROR_MSG)
        if "baseUrl" not in data and "version" not in data:
            raise AssertionError(ZEPHYR_SQUAD_ERROR_MSG)

    def execute(
        self, method: str, relative_path: str, body: Optional[str] = None, content_type: str = 'application/json'
    ):
        if not self.config:
            raise ValueError("Zephyr Squad config is not provided. Please set it before using the tool.")

        api = ZephyrRestAPI(
            account_id=self.config.account_id,
            access_key=self.config.access_key,
            secret_key=self.config.secret_key,
        )

        data = json.loads(body) if body else {}

        return api.request(
            path=relative_path,
            method=method,
            json=data,
            headers={'Content-Type': content_type},
        ).content
