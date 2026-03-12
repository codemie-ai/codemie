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

"""Tool configuration extraction functionality.

This module handles extracting configuration schemas from toolkit classes.
"""

import inspect
import re
import typing
from typing import Dict, Optional, Tuple, Type

from codemie_tools.base.base_toolkit import BaseToolkit


class ToolConfigExtractor:
    """Extracts configuration information from toolkit classes."""

    @classmethod
    def extract_config_for_tool(cls, toolkit_class: Type, tool_name: str) -> Tuple[Optional[Type], Dict, str]:
        """Find the configuration class, its schema, and the corresponding field name for the given tool.

        Tries first to locate configuration fields in class annotations, then inspects the source of
        the `get_tools` method as a fallback.

        Returns:
            Tuple of (config_class, config_schema, config_param_name)
        """
        tool_name_lower = tool_name.lower()

        # Try to extract config from get_definition() for DiscoverableToolkit
        config_from_definition = cls._find_config_from_definition(toolkit_class, tool_name_lower)
        if config_from_definition:
            return config_from_definition

        config_from_annotations = cls._find_config_from_annotations(toolkit_class, tool_name_lower)
        if config_from_annotations:
            return config_from_annotations

        config_from_tools_method = cls._find_config_in_tools_method(toolkit_class, tool_name_lower)
        if config_from_tools_method:
            return config_from_tools_method

        configs = cls._get_config_fields(toolkit_class, tool_name_lower)
        if configs:
            return cls._get_first_config(configs, tool_name_lower)

        return None, {}, ""

    @classmethod
    def get_config_schema(cls, config_class: Type) -> Dict:
        """Extract schema from the configuration class.

        Returns:
            Dict mapping field names to their schema info (type, required)
        """
        schema = {}
        if not hasattr(config_class, '__annotations__'):
            return schema

        for field_name, field_type in config_class.__annotations__.items():
            is_optional = cls._is_optional_type(field_type)
            actual_type = cls._get_actual_type(field_type) if is_optional else field_type

            schema[field_name] = {
                'type': actual_type,
                'required': not is_optional,
            }

        return schema

    @classmethod
    def _find_config_from_definition(
        cls, toolkit_class: Type, tool_name_lower: str
    ) -> Optional[Tuple[Type, Dict, str]]:
        """Find configuration from get_definition() for DiscoverableToolkit."""
        if not hasattr(toolkit_class, 'get_definition'):
            return None

        try:
            definition = toolkit_class.get_definition()
            if not hasattr(definition, 'tools'):
                return None

            # Find the tool in definition.tools
            for tool in definition.tools:
                tool_name = tool.name if hasattr(tool, 'name') else tool.get('name')
                if tool_name and tool_name.lower() == tool_name_lower:
                    # Extract config_class from Tool object
                    if hasattr(tool, 'config_class') and tool.config_class:
                        config_class = tool.config_class
                        # Use empty string as field_name since DiscoverableToolkit doesn't use field annotations
                        return config_class, cls.get_config_schema(config_class), ""
                    break
        except Exception:
            pass

        return None

    @classmethod
    def _find_config_from_annotations(
        cls, toolkit_class: Type, tool_name_lower: str
    ) -> Optional[Tuple[Type, Dict, str]]:
        """Find configuration from class annotations."""
        configs = cls._get_config_fields(toolkit_class, tool_name_lower)
        if not configs:
            return None

        best_match = cls._find_best_config_match(tool_name_lower, configs)
        if best_match:
            field_name, config_type = best_match
            return config_type, cls.get_config_schema(config_type), field_name

        return None

    @classmethod
    def _get_first_config(cls, configs: Dict[str, Type], tool_name: str) -> Tuple[Type, Dict, str]:
        """Get the first config from a dictionary of configs."""
        tool_name_normalized = tool_name.lower().replace('tool', '').strip()

        # Try to find exact match first
        for field_name, config_type in configs.items():
            config_name = config_type.__name__.lower()
            field_name_normalized = field_name.lower().replace('_config', '').strip()

            if (
                tool_name_normalized == field_name_normalized
                or tool_name_normalized == config_name.replace('config', '').strip()
            ):
                return config_type, cls.get_config_schema(config_type), field_name

        # If no exact match, try partial match
        for field_name, config_type in configs.items():
            config_name = config_type.__name__.lower()
            if tool_name_normalized in config_name or tool_name_normalized in field_name.lower():
                return config_type, cls.get_config_schema(config_type), field_name

        # Fallback to first config if no match found
        field_name, config_type = next(iter(configs.items()))
        return config_type, cls.get_config_schema(config_type), field_name

    @classmethod
    def _get_config_fields(cls, toolkit_class: Type[BaseToolkit], tool_name_lower: str) -> Dict[str, Type]:
        """Retrieve potential configuration fields from the toolkit class annotations."""
        configs = {}
        if hasattr(toolkit_class, '__annotations__'):
            for field_name, field_type in toolkit_class.__annotations__.items():
                if (
                    field_name.endswith(('_config', '_creds', '_credentials'))
                    or tool_name_lower.replace("_", "") in field_name.lower()
                ):
                    config_type = field_type.__args__[0] if hasattr(field_type, '__args__') else field_type
                    configs[field_name] = config_type
        return configs

    @classmethod
    def _find_best_config_match(cls, tool_name_lower: str, configs: Dict[str, Type]) -> Optional[Tuple[str, Type]]:
        """Choose the best matching configuration field based on the intersection of name parts."""
        tool_name_parts = set(tool_name_lower.split('_'))
        best_match = None
        best_match_score = 0
        for field_name, config_type in configs.items():
            field_parts = set(field_name.lower().split('_'))
            match_score = len(tool_name_parts.intersection(field_parts))
            if match_score > best_match_score:
                best_match_score = match_score
                best_match = (field_name, config_type)
        return best_match

    @classmethod
    def _find_config_in_tools_method(
        cls, toolkit_class: Type[BaseToolkit], tool_name_lower: str
    ) -> Optional[Tuple[Type, Dict, str]]:
        """Search the source of the `get_tools` method for configuration parameters corresponding to the tool."""
        if not hasattr(toolkit_class, 'get_tools'):
            return None

        try:
            source = inspect.getsource(toolkit_class.get_tools)
        except OSError:
            return None

        return cls._extract_config_from_source(toolkit_class, source, tool_name_lower)

    @classmethod
    def _extract_config_from_source(
        cls, toolkit_class: Type[BaseToolkit], source: str, tool_name_lower: str
    ) -> Optional[Tuple[Type, Dict, str]]:
        """Extract configuration information from method source code."""
        for line in source.split('\n'):
            if tool_name_lower not in line.lower():
                continue

            # Find patterns like: param_name=self.field_name
            config_matches = re.findall(r'(\w+)=self\.(\w+)', line)
            for _, field_name in config_matches:
                config_type = cls._get_field_type(toolkit_class, field_name)
                if config_type:
                    actual_type = cls._get_actual_type(config_type)
                    return actual_type, cls.get_config_schema(actual_type), field_name

        return None

    @classmethod
    def _get_field_type(cls, toolkit_class: Type[BaseToolkit], field_name: str) -> Optional[Type]:
        """Get the type of a field from class annotations."""
        if hasattr(toolkit_class, '__annotations__') and field_name in toolkit_class.__annotations__:
            return toolkit_class.__annotations__[field_name]
        return None

    @classmethod
    def _get_actual_type(cls, field_type: Type) -> Type:
        """Get the actual type from a potentially complex type (like Union)."""
        return field_type.__args__[0] if hasattr(field_type, '__args__') else field_type

    @classmethod
    def _is_optional_type(cls, field_type: Type) -> bool:
        """Check if a type is Optional (Union with None)."""
        return (
            hasattr(field_type, '__origin__')
            and field_type.__origin__ is typing.Union
            and type(None) in field_type.__args__
        )
