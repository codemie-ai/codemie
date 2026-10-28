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

from codemie.configs.config import Config


def test_mcp_auth_cimd_disabled_defaults_false(monkeypatch):
    monkeypatch.delenv("MCP_AUTH_CIMD_DISABLED", raising=False)

    assert Config(_env_file=None).MCP_AUTH_CIMD_DISABLED is False


def test_mcp_auth_cimd_disabled_reads_env(monkeypatch):
    monkeypatch.setenv("MCP_AUTH_CIMD_DISABLED", "true")

    assert Config(_env_file=None).MCP_AUTH_CIMD_DISABLED is True
