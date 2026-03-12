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

from typing import Tuple

from codemie.configs import logger
from codemie.rest_api.models.assistant import MCPServerDetails, MCPServerCheckRequest
from codemie.rest_api.security.user import User
from codemie.service.mcp.toolkit_service import MCPToolkitService


class MCPServerTester:
    mcp_server: MCPServerDetails

    def __init__(self, request: MCPServerCheckRequest, user: User):
        self.mcp_server = request.mcp_server
        self.user = user

    def test(self) -> Tuple[bool, str]:
        # Get the MCP toolkit service singleton

        try:
            tools = MCPToolkitService.get_mcp_server_tools(
                mcp_servers=[self.mcp_server], user_id=self.user.id, mcp_server_single_usage=True
            )

            logger.info(f"Testing passed for MCP tools from {self.mcp_server.name} server. Tools count={len(tools)}")
            return True, 'Success'
        except Exception as e:
            # Log error but continue with other MCP servers if this one fails
            return False, f"{str(e)}.\nPlease, check the configuration."
