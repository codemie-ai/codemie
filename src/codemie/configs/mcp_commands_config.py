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

import yaml
from pathlib import Path
from pydantic import BaseModel, Field

_DEFAULT_CONFIG_PATH = Path(__file__).absolute().parents[3] / "config/mcp/mcp-commands-config.yaml"

_TAG = "MCPCommandsConfig"
_COMMANDS_KEY = "allowed_commands"


class MCPCommandsConfig(BaseModel):
    """Load mcp-commands-config.yaml into allowed_commands and allowed_paths frozensets.

    The YAML uses a single ``allowed_commands`` list. Entries starting with ``/``
    are treated as absolute-path allowlist entries (``allowed_paths``); all others
    are plain binary names (``allowed_commands``).

    Fail-closed: raises ``ValueError`` at startup if the file is missing, malformed,
    or if no plain binary names remain after splitting.
    """

    allowed_commands: frozenset[str] = frozenset()
    allowed_paths: frozenset[str] = frozenset()
    config_path: Path = Field(default=_DEFAULT_CONFIG_PATH)

    def model_post_init(self, _) -> None:
        self._load_config()

    def _load_config(self) -> None:
        try:
            raw = self.config_path.read_text()
        except OSError as exc:
            raise ValueError(f"[{_TAG}] Config file not found: {self.config_path}") from exc

        try:
            config_data = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise ValueError(f"[{_TAG}] Error parsing YAML configuration: {exc}") from exc

        if not isinstance(config_data, dict):
            raise ValueError(f"[{_TAG}] Invalid YAML structure: root must be a mapping")

        entries: list[str] = config_data.get(_COMMANDS_KEY) or []
        if not isinstance(entries, list):
            raise ValueError(f"[{_TAG}] '{_COMMANDS_KEY}' must be a list")

        plain = frozenset(e for e in entries if not e.startswith("/"))
        paths = frozenset(e for e in entries if e.startswith("/"))

        if not plain:
            raise ValueError(
                f"[{_TAG}] '{_COMMANDS_KEY}' must not be empty: "
                "at least one plain binary name (not starting with '/') is required"
            )

        self.allowed_commands = plain
        self.allowed_paths = paths


mcp_commands_config = MCPCommandsConfig()
