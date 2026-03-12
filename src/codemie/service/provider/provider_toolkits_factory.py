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

import traceback
from typing import List, Callable

from codemie.rest_api.models.index import IndexInfo
from codemie_tools.base.base_toolkit import BaseToolkit
from codemie_tools.base.models import ToolKit, ToolMetadata, Tool
from codemie.configs.logger import logger
from codemie.rest_api.models.provider import Provider, ProviderBase, ProviderToolkit
from .util import to_class_name
from .provider_tool_factory import ProviderToolFactory


class ProviderToolkitBase(BaseToolkit):
    """'Implement' ABC for provider toolkits."""

    def get_tools(self): ...

    def get_tools_ui_info(self): ...

    def get_toolkit(self): ...

    def get_datasource_tools(self, datasource: IndexInfo): ...


class ProviderToolkitsFactory:
    """Build toolkit based on provider configuration."""

    CLASSNAME_POSTFIX = "Toolkit"

    @classmethod
    def get_toolkits(cls) -> List[BaseToolkit]:
        all_toolkits = []
        providers = Provider.get_all()

        for provider in providers:
            try:
                toolkits = ProviderToolkitsFactory(provider).build()
                all_toolkits.extend(toolkits)
                logger.debug(f"Built toolkit: {provider.name}")
            except Exception:
                trace = traceback.format_exc()
                name = getattr(provider, "name", "Unknown")
                logger.error(f"Failed to build provider toolkit: {name}, error: {trace}")

        return all_toolkits

    @classmethod
    def get_toolkits_for_provider(cls, provider_id: str) -> List[BaseToolkit]:
        provider = Provider.get_by_id(provider_id)
        return ProviderToolkitsFactory(provider).build()

    def __init__(self, provider: ProviderBase):
        self.provider = provider

    def build(self) -> List[BaseToolkit]:
        toolkits = []
        for toolkit_config in self.provider.provided_toolkits:
            klass_name = to_class_name(toolkit_config.name) + self.CLASSNAME_POSTFIX
            klass = type(klass_name, (ProviderToolkitBase,), {})

            klass.get_tools_ui_info = classmethod(self._generate_get_tools_ui_info(toolkit_config))
            klass.get_toolkit = classmethod(self._generate_get_toolkit(toolkit_config))
            klass.get_tools = self._generate_get_tools(toolkit_config)
            klass.get_datasource_tools = self._generate_get_datasource_tools(toolkit_config)

            toolkits.append(klass)

        return toolkits

    def _generate_get_tools_ui_info(self, toolkit_config: ProviderToolkit) -> Callable:
        """Build tools UI info based on provider configuration."""

        def get_tools_ui_info(cls) -> ToolKit:
            tools = []
            for tool_config in toolkit_config.provided_tools:
                # Datasource actions are excluded and triggered separately
                if tool_config.is_datasource_action:
                    continue

                metadata = ToolMetadata(
                    name=tool_config.name,
                    description=tool_config.description,
                    label=tool_config.name,
                )
                tools.append(Tool.from_metadata(metadata, settings_config=False))

            return ToolKit(toolkit=toolkit_config.name, tools=tools, is_external=True).model_dump()

        return get_tools_ui_info

    def _generate_get_toolkit(self, _toolkit_config: ProviderToolkit) -> Callable:
        """Build toolkit based on provider configuration."""

        def get_toolkit(cls):
            # Fetch an actual user config for each tool
            # and pass it to the toolkit once config is implemented
            return cls()

        return get_toolkit

    def _generate_get_tools(self, toolkit_config: ProviderToolkit) -> Callable:
        """Build tools based on provider configuration."""
        provider = self.provider

        def get_tools(self) -> List[BaseToolkit]:
            tools = []

            for tool_config in toolkit_config.provided_tools:
                if tool_config.is_datasource_action or tool_config.is_datasource_tool:
                    continue

                tool_factory = ProviderToolFactory(provider, toolkit_config, tool_config)
                tools.append(tool_factory.build())

            return tools

        return get_tools

    def _generate_get_datasource_tools(self, toolkit_config: ProviderToolkit) -> Callable:
        """Build tools based on provider configuration that require datasource"""
        provider = self.provider

        def get_datasource_tools(self, datasource: IndexInfo) -> List[BaseToolkit]:
            tools = []

            for tool_config in toolkit_config.provided_tools:
                if not tool_config.is_datasource_tool:
                    continue

                tool_factory = ProviderToolFactory(
                    provider_config=provider,
                    toolkit_config=toolkit_config,
                    tool_config=tool_config,
                    datasource=datasource,
                )

                tools.append(tool_factory.build())

            return tools

        return get_datasource_tools
