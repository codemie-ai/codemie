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

from unittest.mock import MagicMock

import pytest

from codemie.rest_api.models.assistant import ToolKitDetails, ToolDetails
from codemie.rest_api.routers.assistant import _enrich_toolkit_settings_config


@pytest.fixture
def assistant_toolkits():
    """Fixture for assistant toolkits that need settings_config enrichment."""
    return [
        ToolKitDetails(
            toolkit="Toolkit1",
            label="Toolkit 1",
            settings=None,
            tools=[
                ToolDetails(name="Tool1", label="Tool 1", settings=None),
                ToolDetails(name="Tool2", label="Tool 2", settings=MagicMock()),
            ],
        ),
        ToolKitDetails(
            toolkit="Toolkit2",
            label="Toolkit 2",
            settings=MagicMock(),
            tools=[
                ToolDetails(name="Tool1", label="Tool 1", settings=None),
                ToolDetails(name="Tool2", label="Tool 2", settings=MagicMock()),
            ],
        ),
    ]


@pytest.fixture
def tools_info():
    """Fixture for tools info from ToolsInfoService."""
    return [
        {
            "toolkit": "Toolkit1",
            "settings_config": True,
            "tools": [{"name": "Tool1", "settings_config": True}, {"name": "Tool2", "settings_config": True}],
        },
        {
            "toolkit": "Toolkit2",
            "settings_config": False,
            "tools": [{"name": "Tool1", "settings_config": False}, {"name": "Tool2", "settings_config": True}],
        },
    ]


def test_create_settings_config_lookup(tools_info):
    """Test the creation of a settings config lookup dictionary."""
    from codemie.rest_api.routers.assistant import _create_settings_config_lookup

    lookup = _create_settings_config_lookup(tools_info)

    assert lookup["Toolkit1"]
    assert not lookup["Toolkit2"]
    assert lookup[("Toolkit1", "Tool1")]
    assert lookup[("Toolkit1", "Tool2")]
    assert not lookup[("Toolkit2", "Tool1")]
    assert lookup[("Toolkit2", "Tool2")]


def test_enrich_toolkit_settings_config_with_missing_toolkit():
    """Test enrichment when toolkit is missing from tools_info."""
    toolkits = [ToolKitDetails(toolkit="MissingToolkit", tools=[ToolDetails(name="Tool1")])]

    tools_info = [{"toolkit": "DifferentToolkit", "settings_config": True, "tools": []}]

    # Should not raise an exception
    _enrich_toolkit_settings_config(toolkits, tools_info)

    # Default to False if not found in tools_info
    assert not toolkits[0].settings_config
    assert not toolkits[0].tools[0].settings_config
