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

"""Marketplace moderation reads MCP servers by identity only: the enabled flag does not change what it sees."""

from codemie.rest_api.models.assistant import MCPServerDetails
from codemie.service.marketplace_management.entity_adapter import _extract_mcp_ids


def test_moderation_sees_disabled_and_enabled_mcp_servers_alike() -> None:
    servers = [
        MCPServerDetails(name="on", mcp_config_id="cfg-1", enabled=True),
        MCPServerDetails(name="off", mcp_config_id="cfg-2", enabled=False),
    ]

    assert _extract_mcp_ids(servers) == ["on", "off"]
