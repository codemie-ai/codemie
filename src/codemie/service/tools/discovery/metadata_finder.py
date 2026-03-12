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

"""Tool metadata discovery functionality.

This module handles finding ToolMetadata and toolkit classes across packages.
"""

import importlib
import inspect
import pkgutil
from typing import Type

from codemie.agents.tools.base.base_toolkit import BaseToolkit as LocalBaseToolkit
from codemie.configs.logger import logger
from codemie.service.provider import ProviderToolkitsFactory
from codemie_tools.base.base_toolkit import BaseToolkit
from codemie_tools.base.models import ToolMetadata


class ToolMetadataFinder:
    """Discovers tool metadata and toolkit classes across packages."""

    EXTERNAL_PACKAGE = "codemie_tools"
    INTERNAL_PACKAGE = "codemie.agents.tools"

    @classmethod
    def find_provider_tool(cls, tool_name: str) -> tuple[Type[BaseToolkit], ToolMetadata] | None:
        """Find tool information from provider toolkits.

        Returns:
            Tuple of (toolkit_class, tool_metadata) if found, None otherwise
        """
        logger.debug(f"Searching for provider tool: tool_name={tool_name}")

        for toolkit in ProviderToolkitsFactory.get_toolkits():
            try:
                toolkit_instance = toolkit()
            except Exception as e:
                logger.warning(f"Failed to load toolkit: toolkit={toolkit.__name__}, error={str(e)}")
                continue

            for tool in toolkit_instance.get_tools():
                if tool.name != tool_name:
                    continue

                tool_metadata = ToolMetadata(name=tool.name, label=tool.name, description=tool.description)
                logger.debug(f"Found provider tool: tool_name={tool_name}, toolkit={toolkit.__name__}")
                return toolkit, tool_metadata

        logger.debug(f"Provider tool not found: tool_name={tool_name}")
        return None

    @classmethod
    def find_tool_metadata(
        cls, tool_name: str, return_var: bool = False
    ) -> ToolMetadata | tuple[str, ToolMetadata] | None:
        """Find the ToolMetadata object corresponding to the given tool name.

        Returns:
            ToolMetadata object or tuple of (variable_name, metadata) if return_var=True
        """
        logger.debug(f"Searching for tool metadata: tool_name={tool_name}, return_var={return_var}")

        if metadata := cls._find_in_package(cls.EXTERNAL_PACKAGE, tool_name, return_var):
            logger.debug(f"Found metadata in external package: tool_name={tool_name}")
            return metadata

        if metadata := cls._find_in_package(cls.INTERNAL_PACKAGE, tool_name, return_var):
            logger.debug(f"Found metadata in internal package: tool_name={tool_name}")
            return metadata

        logger.debug(f"Tool metadata not found: tool_name={tool_name}")
        return None

    @classmethod
    def find_toolkit_for_metadata(cls, tool_metadata: ToolMetadata) -> Type[BaseToolkit] | None:
        """Find the first toolkit class that contains the given tool metadata."""
        logger.debug(f"Searching for toolkit containing tool: tool_name={tool_metadata.name}")

        if toolkit := cls._find_toolkit_in_package(cls.EXTERNAL_PACKAGE, tool_metadata):
            logger.debug(
                f"Found toolkit in external package: tool_name={tool_metadata.name}, toolkit={toolkit.__name__}"
            )
            return toolkit

        if toolkit := cls._find_toolkit_in_package(cls.INTERNAL_PACKAGE, tool_metadata):
            logger.debug(
                f"Found toolkit in internal package: tool_name={tool_metadata.name}, toolkit={toolkit.__name__}"
            )
            return toolkit

        logger.debug(f"Toolkit not found for tool: tool_name={tool_metadata.name}")
        return None

    @classmethod
    def _find_in_package(
        cls, package_name: str, tool_name: str, return_var: bool = False
    ) -> ToolMetadata | tuple[str, ToolMetadata] | None:
        """Find tool metadata in package."""
        package = cls._import_module_safely(package_name)
        if not package:
            logger.debug(f"Package not found or failed to import: package={package_name}")
            return None
        return cls._find_in_package_modules(package, tool_name, return_var)

    @classmethod
    def _import_module_safely(cls, module_name: str):
        """Safely import a module, returning None if it fails."""
        try:
            return importlib.import_module(module_name)
        except ImportError as e:
            logger.debug(f"Failed to import module: module={module_name}, error={str(e)}")
            return None

    @classmethod
    def _find_in_package_modules(
        cls, package, tool_name: str, return_var: bool = False
    ) -> ToolMetadata | tuple[str, ToolMetadata] | None:
        """Find tool metadata in all modules of a package recursively."""
        for _, module_name, is_pkg in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
            if not (module := cls._import_module_safely(module_name)):
                continue

            if metadata := cls._find_tool_in_module(module, tool_name, return_var):
                return metadata

            if (
                is_pkg
                and hasattr(module, "__path__")
                and (metadata := cls._find_in_package_modules(module, tool_name, return_var))
            ):
                return metadata

        return None

    @classmethod
    def _find_tool_in_module(
        cls, module, tool_name: str, return_var_name: bool = False
    ) -> ToolMetadata | tuple[str, ToolMetadata] | None:
        """Find tool metadata in a specific module."""
        for var_name, obj in inspect.getmembers(module):
            if not isinstance(obj, ToolMetadata):
                continue

            if obj.name != tool_name:
                continue

            if return_var_name:
                return var_name, obj
            return obj

        return None

    @classmethod
    def _find_toolkit_in_package(cls, package_name: str, tool_metadata: ToolMetadata) -> Type[BaseToolkit] | None:
        """Find a toolkit in a specific package that contains the given tool recursively."""
        package = cls._import_module_safely(package_name)
        if not package:
            return None

        for _, module_name, is_pkg in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
            if "toolkit" in module_name and (toolkit := cls._find_toolkit_in_module(module_name, tool_metadata)):
                return toolkit

            if is_pkg and (toolkit := cls._find_toolkit_in_package(module_name, tool_metadata)):
                return toolkit

        return None

    @classmethod
    def _find_toolkit_in_module(cls, module_name: str, tool_metadata: ToolMetadata) -> Type[BaseToolkit] | None:
        """Find a toolkit class in a specific module that contains the given tool."""
        module = cls._import_module_safely(module_name)
        if not module:
            return None

        for _, cls_obj in inspect.getmembers(module, inspect.isclass):
            try:
                is_toolkit = (issubclass(cls_obj, BaseToolkit) and cls_obj != BaseToolkit) or (
                    issubclass(cls_obj, LocalBaseToolkit) and cls_obj != LocalBaseToolkit
                )
            except TypeError:
                continue

            if not is_toolkit:
                continue

            if cls._toolkit_contains_tool(cls_obj, tool_metadata):
                return cls_obj

        return None

    @classmethod
    def _toolkit_contains_tool(cls, toolkit_class: Type, tool_metadata: ToolMetadata) -> bool:
        """Check if the toolkit contains the specific tool."""
        try:
            if cls._check_tool_in_definition(toolkit_class, tool_metadata):
                return True

            if cls._check_tool_in_api_info(toolkit_class, tool_metadata):
                return True

            if cls._check_tool_in_ui_info(toolkit_class, tool_metadata):
                return True

            if cls._check_tool_in_source(toolkit_class, tool_metadata):
                return True

        except Exception as e:
            toolkit_name = getattr(toolkit_class, "__name__", str(toolkit_class))
            logger.debug(
                f"Error checking toolkit for tool: toolkit={toolkit_name}, "
                f"tool_name={tool_metadata.name}, error={str(e)}"
            )

        return False

    @classmethod
    def _check_tool_in_definition(cls, toolkit_class: Type, tool_metadata: ToolMetadata) -> bool:
        """Check if tool exists in get_definition() for DiscoverableToolkit."""
        if not hasattr(toolkit_class, "get_definition"):
            return False

        try:
            definition = toolkit_class.get_definition()
            if not hasattr(definition, "tools"):
                return False

            for tool in definition.tools:
                tool_name = tool.name if hasattr(tool, "name") else tool.get("name")
                if tool_name != tool_metadata.name:
                    continue
                return True
        except Exception as e:
            toolkit_name = getattr(toolkit_class, "__name__", str(toolkit_class))
            logger.debug(f"Error checking tool in definition: toolkit={toolkit_name}, error={str(e)}")

        return False

    @classmethod
    def _check_tool_in_api_info(cls, toolkit_class: Type, tool_metadata: ToolMetadata) -> bool:
        """Check if tool exists in get_tools_api_info()."""
        if not hasattr(toolkit_class, "get_tools_api_info"):
            return False

        try:
            api_info = toolkit_class.get_tools_api_info()
            tools = api_info.get("tools", [])
            return any(tool.get("name") == tool_metadata.name for tool in tools)
        except Exception as e:
            toolkit_name = getattr(toolkit_class, "__name__", str(toolkit_class))
            logger.debug(f"Error checking tool in API info: toolkit={toolkit_name}, error={str(e)}")
            return False

    @classmethod
    def _check_tool_in_ui_info(cls, toolkit_class: Type, tool_metadata: ToolMetadata) -> bool:
        """Check if tool exists in get_tools_ui_info()."""
        try:
            ui_info = toolkit_class.get_tools_ui_info()
            if isinstance(ui_info, dict) and "tools" in ui_info:
                tools = ui_info["tools"]
                return any(tool.get("name") == tool_metadata.name for tool in tools)
        except Exception as e:
            toolkit_name = getattr(toolkit_class, "__name__", str(toolkit_class))
            logger.debug(f"Error checking tool in UI info: toolkit={toolkit_name}, error={str(e)}")

        return False

    @classmethod
    def _check_tool_in_source(cls, toolkit_class: Type, tool_metadata: ToolMetadata) -> bool:
        """Check if tool name appears in get_tools() source code."""
        if not hasattr(toolkit_class, "get_tools"):
            return False

        try:
            source = inspect.getsource(toolkit_class.get_tools)
            return tool_metadata.name in source
        except Exception as e:
            toolkit_name = getattr(toolkit_class, "__name__", str(toolkit_class))
            logger.debug(f"Error checking tool in source: toolkit={toolkit_name}, error={str(e)}")
            return False
