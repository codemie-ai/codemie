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
from codemie.service.workspace_script_bridge import resolve_tool_calling_settings
from codemie_tools.data_management.code_executor.tool_calling_limits import (
    DEFAULT_MAX_PARALLEL_CALLS,
    DEFAULT_RUN_TIMEOUT_SECONDS,
    ToolCallingSettings,
)

FEATURE = "workspaceScriptBridge"
ENV_OVERRIDE = "FEATURE_WORKSPACE_SCRIPT_BRIDGE"


@pytest.fixture(autouse=True)
def _jobs_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODE_EXECUTOR_SANDBOX_MODE", "sandbox-jobs")


def resolve_tool_calling_timeout() -> float | None:
    """The run limit of the resolved settings, or None when tool calling is off (the shape these tests check)."""
    settings = resolve_tool_calling_settings()
    return None if settings is None else settings.run_timeout_seconds


DEFAULT_TOOL_CALLING_TIMEOUT_SECONDS = DEFAULT_RUN_TIMEOUT_SECONDS


def _fake_customer_config(
    enabled: bool, timeout: object = 120, *, has_timeout: bool = True, parallel: object = 5, has_parallel: bool = True
) -> MagicMock:
    fake = MagicMock()
    fake.is_feature_enabled.side_effect = lambda key: enabled if key == FEATURE else False

    def get_setting(feature_key: str, setting_name: str, default: object = None) -> object:
        if feature_key == FEATURE and setting_name == "timeoutSeconds" and has_timeout:
            return timeout
        if feature_key == FEATURE and setting_name == "maxParallelCalls" and has_parallel:
            return parallel
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

    @pytest.mark.parametrize("raw", [601, 3600, "999", 10**6])
    def test_enabled_oversized_timeout_is_clamped_to_the_ceiling(self, raw: object) -> None:
        with patch.object(bridge_module, "customer_config", _fake_customer_config(enabled=True, timeout=raw)):
            assert resolve_tool_calling_timeout() == 480.0

    def test_enabled_invalid_timeout_warns_and_keeps_the_feature_on(self) -> None:
        with (
            patch.object(bridge_module, "customer_config", _fake_customer_config(enabled=True, timeout="abc")),
            patch("codemie_tools.data_management.code_executor.tool_calling_limits.logger.warning") as warn,
        ):
            assert resolve_tool_calling_timeout() == 120.0
        assert warn.call_count == 1


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


class TestSettingsValue:
    def test_enabled_resolves_a_frozen_settings_value_with_all_three_numbers(self) -> None:
        fake = _fake_customer_config(enabled=True, timeout=90, parallel=3)
        with patch.object(bridge_module, "customer_config", fake):
            settings = resolve_tool_calling_settings()

        assert settings == ToolCallingSettings(run_timeout_seconds=90.0, max_parallel_calls=3)

    def test_the_component_is_read_once_per_resolution(self) -> None:
        fake = _fake_customer_config(enabled=True)
        with patch.object(bridge_module, "customer_config", fake):
            resolve_tool_calling_settings()

        assert fake.is_feature_enabled.call_count == 1

    def test_a_missing_parallel_setting_falls_back_to_the_default(self) -> None:
        fake = _fake_customer_config(enabled=True, has_parallel=False)
        with patch.object(bridge_module, "customer_config", fake):
            settings = resolve_tool_calling_settings()

        assert settings is not None
        assert settings.max_parallel_calls == DEFAULT_MAX_PARALLEL_CALLS

    @pytest.mark.parametrize("raw", [None, "abc", 0, -1, True, float("nan")])
    def test_an_invalid_parallel_setting_falls_back_to_the_default_and_keeps_the_feature_on(self, raw: object) -> None:
        fake = _fake_customer_config(enabled=True, parallel=raw)
        with patch.object(bridge_module, "customer_config", fake):
            settings = resolve_tool_calling_settings()

        assert settings is not None
        assert settings.max_parallel_calls == DEFAULT_MAX_PARALLEL_CALLS

    def test_a_parallel_setting_above_the_process_limit_is_lowered_to_it(self) -> None:
        fake = _fake_customer_config(enabled=True, parallel=50)
        with (
            patch.object(bridge_module, "customer_config", fake),
            patch.object(bridge_module.config, "WORKSPACE_SCRIPT_TOOL_CALLS_MAX_CONCURRENT", 4),
        ):
            settings = resolve_tool_calling_settings()

        assert settings is not None
        assert settings.max_parallel_calls == 4

    def test_the_default_parallel_value_is_held_to_a_low_process_limit(self) -> None:
        fake = _fake_customer_config(enabled=True, has_parallel=False)
        with (
            patch.object(bridge_module, "customer_config", fake),
            patch.object(bridge_module.config, "WORKSPACE_SCRIPT_TOOL_CALLS_MAX_CONCURRENT", 2),
        ):
            settings = resolve_tool_calling_settings()

        assert settings is not None
        assert settings.max_parallel_calls == 2


class TestOnlyTheJobsModeHasABridge:
    def test_the_pooled_sandbox_resolves_off_although_the_component_is_on(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODE_EXECUTOR_SANDBOX_MODE", "sandbox-shared")
        with patch.object(bridge_module, "customer_config", _fake_customer_config(enabled=True)):
            assert resolve_tool_calling_settings() is None

    def test_the_jobs_sandbox_resolves_on(self) -> None:
        with patch.object(bridge_module, "customer_config", _fake_customer_config(enabled=True)):
            assert resolve_tool_calling_settings() is not None
