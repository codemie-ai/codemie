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

from __future__ import annotations

from pathlib import Path

import pytest

from codemie.configs.config import Config
from codemie.configs.customer_config import CustomerConfig

SHIPPED_CUSTOMER_CONFIG = Path(__file__).resolve().parents[3] / "config/customer/customer-config.yaml"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("HEADROOM_MODE", "off"),
        ("HEADROOM_SDK_ALLOW_MODEL_DOWNLOAD", False),
        ("HEADROOM_SIDECAR_URL", "http://localhost:8787"),
        ("HEADROOM_PROXY_TOKEN", ""),
        ("HEADROOM_TIMEOUT_SECONDS", 10),
        ("HEADROOM_MIN_TOKENS", 1000),
        ("HEADROOM_AGGRESSIVE_TARGET_RATIO", 0.2),
    ],
)
def test_headroom_setting_default(name: str, expected: object) -> None:
    # Read the declared default so a developer's local env/.env overrides cannot mask a regression.
    default = Config.model_fields[name].default

    assert default == expected
    assert type(default) is type(expected)


def test_headroom_feature_flag_ships_disabled() -> None:
    config = CustomerConfig(config_path=SHIPPED_CUSTOMER_CONFIG)

    component = next((c for c in config.components if c.id == "features:headroom"), None)

    assert component is not None
    assert component.settings.enabled is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("", "off"), ("   ", "off"), ("SDK", "sdk"), (" Sidecar ", "sidecar"), ("off", "off"), ("OFF", "off")],
)
def test_headroom_mode_blank_is_off_and_case_is_normalized(raw: str, expected: str) -> None:
    mode = Config(HEADROOM_MODE=raw).HEADROOM_MODE

    assert mode == expected


def test_headroom_enabled_is_no_longer_a_setting() -> None:
    assert "HEADROOM_ENABLED" not in Config.model_fields


def test_headroom_mode_typo_is_still_rejected() -> None:
    with pytest.raises(ValueError):
        Config(HEADROOM_MODE="sidecr")
