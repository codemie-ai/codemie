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

import pytest

from codemie.configs.component_resolution import clear_snapshot, publish_snapshot
from codemie.configs.customer_config import Component, ComponentSetting, CustomerConfig


@pytest.fixture(autouse=True)
def reset_snapshot():
    clear_snapshot()
    yield
    clear_snapshot()


@pytest.fixture
def cfg():
    return CustomerConfig.model_construct(
        components=[
            Component(id="features:webSearch", settings=ComponentSetting(enabled=True, name="Web Search")),
        ],
        preconfigured_assistants=[],
        tool_defaults={},
    )


def test_is_component_enabled_follows_the_override(cfg):
    publish_snapshot({"features:webSearch": {"enabled": False}})
    assert cfg.is_component_enabled("features:webSearch") is False


def test_is_feature_enabled_follows_the_override(cfg):
    publish_snapshot({"features:webSearch": {"enabled": False}})
    assert cfg.is_feature_enabled("webSearch") is False


def test_yaml_still_wins_when_no_override(cfg):
    assert cfg.is_feature_enabled("webSearch") is True


def test_unknown_key_still_defaults_to_false(cfg):
    assert cfg.is_feature_enabled("nothingHere") is False
    assert cfg.is_component_enabled("nothingHere") is False


def test_get_feature_setting_follows_the_override(cfg):
    publish_snapshot({"features:webSearch": {"enabled": True, "name": "Overridden"}})
    assert cfg.get_feature_setting("webSearch", "name") == "Overridden"


def test_get_feature_setting_falls_back_to_yaml_for_fields_the_override_omits(cfg):
    publish_snapshot({"features:webSearch": {"enabled": True}})
    assert cfg.get_feature_setting("webSearch", "name") == "Web Search"
