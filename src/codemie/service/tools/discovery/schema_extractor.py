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

"""Tool schema extraction functionality.

This module handles extracting args_schema (execution parameters) from tool classes.
"""

from __future__ import annotations

import importlib
import inspect
from typing import Type

from codemie.configs.logger import logger
from codemie_tools.base.codemie_tool import CodeMieTool

from .config_extractor import ToolConfigExtractor


class ToolSchemaExtractor:
    """Extracts execution parameter schemas from tool classes."""

    @classmethod
    def extract_args_schema(cls, toolkit_class: Type, tool_name: str) -> dict:
        """Extract args_schema from tool class.

        Supports two patterns:
        1. DiscoverableToolkit: via get_definition() method
        2. Direct tool classes: via tool class model_fields

        Returns:
            Dict mapping argument names to their schema info (type, required)
        """
        logger.debug(f"Extracting args_schema: toolkit={toolkit_class.__name__}, tool_name={tool_name}")

        # Try pattern 1: get_definition() for DiscoverableToolkit
        if hasattr(toolkit_class, "get_definition"):
            try:
                definition = toolkit_class.get_definition()
                if hasattr(definition, "tools"):
                    tool = cls._find_tool_in_definition(definition.tools, tool_name)
                    if tool:
                        args_schema_class = cls._extract_args_schema_class(tool)
                        if args_schema_class:
                            logger.debug(f"Extracted args_schema via get_definition: tool_name={tool_name}")
                            return ToolConfigExtractor.get_config_schema(args_schema_class)
            except Exception as e:
                logger.debug(f"Error extracting args_schema via get_definition: tool_name={tool_name}, error={str(e)}")

        # Try pattern 2: Direct tool class with args_schema field
        try:
            tool_class = cls._find_tool_class_in_toolkit_module(toolkit_class, tool_name)
            if tool_class:
                args_schema_class = cls._extract_args_schema_from_tool_class(tool_class)
                if args_schema_class:
                    logger.debug(f"Extracted args_schema from tool class: tool_name={tool_name}")
                    return ToolConfigExtractor.get_config_schema(args_schema_class)
        except Exception as e:
            logger.debug(f"Error extracting args_schema from tool class: tool_name={tool_name}, error={str(e)}")

        logger.debug(f"No args_schema found: tool_name={tool_name}")
        return {}

    @classmethod
    def _find_tool_in_definition(cls, tools, tool_name: str):
        """Find tool in definition.tools by name."""
        tool_name_lower = tool_name.lower()
        for tool in tools:
            tool_name_found = tool.name if hasattr(tool, "name") else tool.get("name")
            if not tool_name_found:
                continue

            if tool_name_found.lower() != tool_name_lower:
                continue

            return tool
        return None

    @classmethod
    def _extract_args_schema_class(cls, tool):
        """Extract args_schema class from tool object."""
        if not hasattr(tool, "tool_class") or not tool.tool_class:
            return None

        tool_class = tool.tool_class
        if not hasattr(tool_class, "model_fields"):
            return None

        if "args_schema" not in tool_class.model_fields:
            return None

        args_schema_class = tool_class.model_fields["args_schema"].default
        if not args_schema_class or not hasattr(args_schema_class, "model_fields"):
            return None

        return args_schema_class

    @classmethod
    def _find_tool_class_in_toolkit_module(cls, toolkit_class: Type, tool_name: str):
        """Find tool class by searching for CodeMieTool subclasses in toolkit's module."""
        try:
            module = importlib.import_module(toolkit_class.__module__)
            return cls._search_module_for_tool_class(module, tool_name)
        except (ImportError, AttributeError) as e:
            logger.debug(f"Failed to search toolkit module: toolkit={toolkit_class.__name__}, error={str(e)}")
            return None

    @classmethod
    def _search_module_for_tool_class(cls, module, tool_name: str):
        """Search module for a CodeMieTool subclass with matching name."""
        tool_name_lower = tool_name.lower()

        for _, obj in inspect.getmembers(module, inspect.isclass):
            # Only consider CodeMieTool subclasses
            try:
                if not issubclass(obj, CodeMieTool) or obj == CodeMieTool:
                    continue
            except TypeError:
                continue

            if not hasattr(obj, "model_fields") or "args_schema" not in obj.model_fields:
                continue

            if cls._tool_name_matches(obj, tool_name_lower):
                return obj

        return None

    @classmethod
    def _tool_name_matches(cls, tool_class, tool_name_lower: str) -> bool:
        """Check if tool class name matches the given tool name."""
        try:
            if "name" in tool_class.model_fields:
                obj_name = tool_class.model_fields["name"].default
                if obj_name and obj_name.lower() == tool_name_lower:
                    return True

            if "base_name" in tool_class.model_fields:
                obj_base_name = tool_class.model_fields["base_name"].default
                if obj_base_name and obj_base_name.lower() == tool_name_lower:
                    return True
        except (AttributeError, KeyError):
            pass

        return False

    @classmethod
    def _extract_args_schema_from_tool_class(cls, tool_class):
        """Extract args_schema class from a tool class."""
        if not hasattr(tool_class, "model_fields"):
            return None

        if "args_schema" not in tool_class.model_fields:
            return None

        args_schema_field = tool_class.model_fields["args_schema"]
        args_schema_class = args_schema_field.default

        if not args_schema_class or not hasattr(args_schema_class, "model_fields"):
            return None

        return args_schema_class
