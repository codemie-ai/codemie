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

from unittest import mock

import pytest

from codemie_tools.base.models import CredentialTypes, ToolSet
from codemie_tools.base.toolkit_provider import get_available_toolkits
from codemie_tools.ms_exchange.models import ExchangeToolConfig
from codemie_tools.ms_exchange.toolkit import ExchangeToolkit, ExchangeToolkitUI


@pytest.fixture(autouse=True)
def clear_toolkit_provider_cache():
    """Clear the toolkit provider lru_cache so the autodiscovery scan re-runs."""
    get_available_toolkits.cache_clear()
    yield
    get_available_toolkits.cache_clear()


def test_credential_types_ms_exchange_value():
    """CredentialTypes.MS_EXCHANGE must be the exact string the plan mandates."""
    assert CredentialTypes.MS_EXCHANGE.value == "MSExchange"


def test_toolset_ms_exchange_value():
    """ToolSet.MS_EXCHANGE must be the exact label the plan mandates."""
    assert ToolSet.MS_EXCHANGE.value == "Microsoft Exchange"


def test_exchange_toolkit_definition():
    """ExchangeToolkit.get_definition() returns the MS Exchange toolkit with its read tools."""
    definition = ExchangeToolkit.get_definition()

    assert definition.toolkit == ToolSet.MS_EXCHANGE
    assert {t.name for t in definition.tools} >= {
        "exchange_retrieve_folders",
        "exchange_read",
        "exchange_retrieve_by_id",
    }
    assert definition.settings_config is True
    assert all(t.config_class is ExchangeToolConfig for t in definition.tools)
    assert definition.label == "Microsoft Exchange"


def test_exchange_integration_is_configured_once_on_the_toolkit():
    """One shared integration per toolkit: the tools must not carry a second, per-tool configuration.

    A per-tool flag makes the UI render a second lookup switch and integration dropdown under every
    tool and makes the workflow save read the (empty) tool slot instead of the toolkit selection.
    The tools keep their config_class, which the runtime needs to resolve the stored credentials.
    """
    definition = ExchangeToolkit.get_definition()

    assert definition.settings_config is True
    assert all(t.settings_config is False for t in definition.tools)
    assert all(t.config_class is ExchangeToolConfig for t in definition.tools)


def test_exchange_toolkit_ui_default_definition():
    """ExchangeToolkitUI carries the same toolkit/tools defaults directly."""
    ui = ExchangeToolkitUI()

    assert ui.toolkit == ToolSet.MS_EXCHANGE
    assert len(ui.tools) >= 3


def test_exchange_toolkit_is_autodiscovered():
    """The provider scan (same mechanism as test_toolkit_provider.py) must find ExchangeToolkit."""
    toolkits = get_available_toolkits()

    assert ExchangeToolkit in toolkits


def test_get_tools_info_returns_exchange_toolkit_when_enterprise_and_provider_are_empty():
    """get_tools_info surfaces the autodiscovered Exchange toolkit (AC2), with no prior coverage."""
    from codemie.service.provider.provider_toolkits_factory import ProviderToolkitsFactory
    from codemie.service.tools.tools_info_service import ToolsInfoService

    with (
        mock.patch(
            "codemie.enterprise.plugin.get_plugin_toolkit_ui_info",
            return_value=None,
        ),
        mock.patch.object(ProviderToolkitsFactory, "get_toolkits", return_value=[]),
    ):
        toolkits = ToolsInfoService.get_tools_info()

    exchange_toolkits = [t for t in toolkits if t.get("toolkit") == ToolSet.MS_EXCHANGE.value]
    assert len(exchange_toolkits) == 1
    assert len(exchange_toolkits[0]["tools"]) >= 3
