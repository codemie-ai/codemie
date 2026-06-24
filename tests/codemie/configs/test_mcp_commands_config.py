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

import pytest
from pathlib import Path

from codemie.configs.mcp_commands_config import MCPCommandsConfig


class TestMCPCommandsConfig:
    def test_load_valid_yaml(self, tmp_path):
        cfg_file = tmp_path / "mcp-commands-config.yaml"
        cfg_file.write_text("allowed_commands:\n" "  - npx\n" "  - uvx\n" "  - /some/absolute/path/binary\n")
        cfg = MCPCommandsConfig(config_path=cfg_file)
        assert "npx" in cfg.allowed_commands
        assert "uvx" in cfg.allowed_commands
        assert "/some/absolute/path/binary" in cfg.allowed_paths
        assert "/some/absolute/path/binary" not in cfg.allowed_commands

    def test_load_missing_file_raises(self):
        with pytest.raises(ValueError, match="not found"):
            MCPCommandsConfig(config_path=Path("/nonexistent/path/mcp-commands-config.yaml"))

    def test_load_malformed_yaml_raises(self, tmp_path):
        cfg_file = tmp_path / "mcp-commands-config.yaml"
        cfg_file.write_text("allowed_commands: [unclosed bracket\n")
        with pytest.raises(ValueError, match="[Pp]arsing|YAML|malformed"):
            MCPCommandsConfig(config_path=cfg_file)

    def test_load_empty_commands_raises(self, tmp_path):
        cfg_file = tmp_path / "mcp-commands-config.yaml"
        cfg_file.write_text("allowed_commands: []\n")
        with pytest.raises(ValueError, match="must not be empty"):
            MCPCommandsConfig(config_path=cfg_file)

    def test_load_empty_paths_succeeds(self, tmp_path):
        cfg_file = tmp_path / "mcp-commands-config.yaml"
        cfg_file.write_text("allowed_commands:\n  - npx\n")
        cfg = MCPCommandsConfig(config_path=cfg_file)
        assert cfg.allowed_paths == frozenset()
        assert "npx" in cfg.allowed_commands

    def test_load_only_paths_raises(self, tmp_path):
        """All entries are absolute paths → allowed_commands (plain names) is empty → fail-closed."""
        cfg_file = tmp_path / "mcp-commands-config.yaml"
        cfg_file.write_text("allowed_commands:\n  - /abs/path/only\n")
        with pytest.raises(ValueError, match="must not be empty"):
            MCPCommandsConfig(config_path=cfg_file)

    def test_default_config_loads(self):
        """The shipped default YAML must load cleanly and contain expected commands."""
        cfg = MCPCommandsConfig()
        assert "npx" in cfg.allowed_commands
        assert "uvx" in cfg.allowed_commands
        assert "github-mcp-server" in cfg.allowed_commands
