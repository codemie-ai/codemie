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

from codemie.configs.component_resolution import (
    Component,
    ComponentSetting,
    clear_snapshot,
    publish_snapshot,
    resolve,
    resolve_all,
)

RUNTIME_IDS = {"features:enterpriseEdition"}


@pytest.fixture(autouse=True)
def reset_snapshot():
    clear_snapshot()
    yield
    clear_snapshot()


def _yaml():
    return [
        Component(id="features:webSearch", settings=ComponentSetting(enabled=True, name="Web Search")),
        Component(id="features:codeInterpreter", settings=ComponentSetting(enabled=False)),
    ]


def _runtime():
    return [Component(id="features:enterpriseEdition", settings=ComponentSetting(enabled=True))]


def test_yaml_value_is_used_when_no_override():
    component = resolve("features:webSearch", _yaml(), _runtime(), RUNTIME_IDS)
    assert component is not None
    assert component.settings.enabled is True


def test_override_field_wins_over_yaml():
    publish_snapshot({"features:webSearch": {"enabled": False}})
    component = resolve("features:webSearch", _yaml(), _runtime(), RUNTIME_IDS)
    assert component.settings.enabled is False


def test_fields_absent_from_the_override_keep_coming_from_yaml():
    publish_snapshot({"features:webSearch": {"enabled": False}})
    component = resolve("features:webSearch", _yaml(), _runtime(), RUNTIME_IDS)
    assert component.settings.name == "Web Search"


def test_runtime_component_ignores_the_override():
    publish_snapshot({"features:enterpriseEdition": {"enabled": False}})
    component = resolve("features:enterpriseEdition", _yaml(), _runtime(), RUNTIME_IDS)
    assert component.settings.enabled is True


def test_unknown_component_resolves_to_none():
    assert resolve("features:nothingHere", _yaml(), _runtime(), RUNTIME_IDS) is None


def test_component_absent_from_yaml_resolves_from_the_snapshot_alone():
    publish_snapshot({"chatDisclaimer": {"enabled": True, "text": "hi"}})
    component = resolve("chatDisclaimer", _yaml(), _runtime(), RUNTIME_IDS)
    assert component is not None
    assert component.settings.enabled is True
    assert component.settings.text == "hi"


def test_resolve_all_covers_yaml_runtime_and_snapshot_only_components():
    publish_snapshot({"chatDisclaimer": {"enabled": True}})
    ids = {c.id for c in resolve_all(_yaml(), _runtime(), RUNTIME_IDS)}
    assert ids == {
        "features:webSearch",
        "features:codeInterpreter",
        "features:enterpriseEdition",
        "chatDisclaimer",
    }
