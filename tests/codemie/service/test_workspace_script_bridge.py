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

"""The bridge timeout resolves from customer config: off when disabled, parsed and defaulted when enabled."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from codemie.configs import component_resolution
from codemie.configs.customer_config import CustomerConfig
from codemie.service import workspace_script_bridge as bridge_module
from codemie.service.workspace_script_bridge import (
    DEFAULT_TOOL_CALLING_TIMEOUT_SECONDS,
    resolve_tool_calling_timeout,
)

FEATURE = "workspaceScriptBridge"
ENV_OVERRIDE = "FEATURE_WORKSPACE_SCRIPT_BRIDGE"


def _fake_customer_config(enabled: bool, timeout: object = 120, *, has_timeout: bool = True) -> MagicMock:
    fake = MagicMock()
    fake.is_feature_enabled.side_effect = lambda key: enabled if key == FEATURE else False

    def get_setting(feature_key: str, setting_name: str, default: object = None) -> object:
        if feature_key == FEATURE and setting_name == "timeoutSeconds" and has_timeout:
            return timeout
        return default

    fake.get_feature_setting.side_effect = get_setting
    return fake


class TestToolCallingTimeoutSelection:
    def test_component_disabled_resolves_none(self) -> None:
        with patch.object(bridge_module, "customer_config", _fake_customer_config(enabled=False)):
            assert resolve_tool_calling_timeout() is None

    def test_component_absent_resolves_none(self) -> None:
        absent = CustomerConfig.model_construct(components=[])
        with patch.object(bridge_module, "customer_config", absent):
            assert resolve_tool_calling_timeout() is None

    def test_enabled_resolves_configured_timeout(self) -> None:
        with patch.object(bridge_module, "customer_config", _fake_customer_config(enabled=True, timeout=45)):
            assert resolve_tool_calling_timeout() == 45.0

    @pytest.mark.parametrize("raw", [120, 120.0, "120"])
    def test_enabled_valid_default_value(self, raw: object) -> None:
        with patch.object(bridge_module, "customer_config", _fake_customer_config(enabled=True, timeout=raw)):
            assert resolve_tool_calling_timeout() == 120.0

    def test_enabled_missing_timeout_falls_back_to_default(self) -> None:
        fake = _fake_customer_config(enabled=True, has_timeout=False)
        with patch.object(bridge_module, "customer_config", fake):
            assert resolve_tool_calling_timeout() == DEFAULT_TOOL_CALLING_TIMEOUT_SECONDS

    @pytest.mark.parametrize(
        "raw",
        [
            None,
            "abc",
            "",
            True,
            False,
            0,
            -5,
            "-1",
            float("nan"),
            float("inf"),
            "inf",
            "nan",
            [30],
            {"a": 1},
            10**400,
            -(10**400),
        ],
    )
    def test_enabled_invalid_timeout_falls_back_to_default(self, raw: object) -> None:
        with patch.object(bridge_module, "customer_config", _fake_customer_config(enabled=True, timeout=raw)):
            assert resolve_tool_calling_timeout() == 120.0

    def test_default_constant_is_120(self) -> None:
        assert DEFAULT_TOOL_CALLING_TIMEOUT_SECONDS == 120.0


class TestToolCallingWithLoadedYaml:
    YAML = (
        "components:\n"
        "  - id: 'features:workspaceScriptBridge'\n"
        "    settings:\n"
        "      enabled: false\n"
        "      timeoutSeconds: 120\n"
    )

    def _load(self, tmp_path: Path, env: dict[str, str]) -> CustomerConfig:
        config_file = tmp_path / "customer-config.yaml"
        config_file.write_text(self.YAML)
        with patch.dict(os.environ, env):
            if ENV_OVERRIDE not in env:
                os.environ.pop(ENV_OVERRIDE, None)
            return CustomerConfig(config_path=config_file)

    def test_env_override_enables_component_and_resolves_default_timeout(self, tmp_path: Path) -> None:
        loaded = self._load(tmp_path, {ENV_OVERRIDE: "true"})
        with patch.object(bridge_module, "customer_config", loaded):
            assert resolve_tool_calling_timeout() == 120.0

    def test_without_override_component_stays_disabled(self, tmp_path: Path) -> None:
        loaded = self._load(tmp_path, {})
        with patch.object(bridge_module, "customer_config", loaded):
            assert resolve_tool_calling_timeout() is None


class TestToolCallingFollowsRuntimeOverrides:
    """A value saved through the admin settings applies to the next run without a restart."""

    @pytest.fixture(autouse=True)
    def _reset_overrides(self) -> Iterator[None]:
        yield
        component_resolution.publish_snapshot({})

    def test_override_enables_component_missing_from_yaml_and_sets_timeout(self) -> None:
        absent = CustomerConfig.model_construct(components=[])
        component_resolution.publish_snapshot({f"features:{FEATURE}": {"enabled": True, "timeoutSeconds": "45"}})
        with patch.object(bridge_module, "customer_config", absent):
            assert resolve_tool_calling_timeout() == 45.0

    def test_override_can_switch_the_component_off_again(self, tmp_path: Path) -> None:
        config_file = tmp_path / "customer-config.yaml"
        config_file.write_text(TestToolCallingWithLoadedYaml.YAML.replace("enabled: false", "enabled: true"))
        loaded = CustomerConfig(config_path=config_file)
        with patch.object(bridge_module, "customer_config", loaded):
            assert resolve_tool_calling_timeout() == 120.0
            component_resolution.publish_snapshot({f"features:{FEATURE}": {"enabled": False}})
            assert resolve_tool_calling_timeout() is None
