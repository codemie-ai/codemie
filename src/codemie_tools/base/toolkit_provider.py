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

import functools
import importlib
import inspect
import logging
import pkgutil
from typing import List, Type, Set, TypeVar, Optional

from codemie_tools.base.base_toolkit import BaseToolkit, DiscoverableToolkit
from codemie_tools.base.models import Tool, CodeMieToolConfig

T = TypeVar('T')

logger = logging.getLogger(__name__)

__CODEMIE_TOOLS_ALLOWED_PACKAGES = [
    "codemie_tools",
]


def is_toolkit_class(obj) -> bool:
    """Check if an object is a subclass of BaseToolkit (excluding BaseToolkit itself)
    and its get_definition method returns a non-None value."""
    try:
        return inspect.isclass(obj) and issubclass(obj, DiscoverableToolkit) and obj != DiscoverableToolkit
    except (TypeError, AttributeError) as e:
        logger.debug(f"Error checking if {obj} is a toolkit class: {e}")
        return False


def is_config_class(obj) -> bool:
    """Check if an object is a subclass of CodeMieToolConfig (excluding CodeMieToolConfig itself)."""
    try:
        return inspect.isclass(obj) and issubclass(obj, CodeMieToolConfig) and obj != CodeMieToolConfig
    except TypeError:
        return False


def _process_module_generic(
    module_name: str, seen_ids: Set[str], items: List[Type[T]], is_target_class, item_type_name: str
):
    """Process a single module to find target class subclasses."""
    try:
        module = importlib.import_module(module_name)

        # Find target classes in this module
        for name, obj in inspect.getmembers(module):
            if is_target_class(obj):
                item_id = f"{obj.__module__}.{obj.__name__}"
                if item_id not in seen_ids:
                    seen_ids.add(item_id)
                    items.append(obj)
                    logger.debug(f"Lookup {item_type_name}. Module: {module_name}, Found {obj}")
                    logger.info(f"Lookup {item_type_name}. Module: {module_name}, Found {item_type_name}: {len(items)}")
        return module
    except ImportError as e:
        logger.error(f"Error importing {module_name}: {e}")
        return None


def _process_module(module_name: str, seen_toolkit_ids: Set[str], toolkits: List[Type[BaseToolkit]]):
    """Process a single module to find BaseToolkit subclasses."""
    return _process_module_generic(module_name, seen_toolkit_ids, toolkits, is_toolkit_class, "toolkits")


def _process_config_module(module_name: str, seen_config_ids: Set[str], configs: List[Type[CodeMieToolConfig]]):
    """Process a single module to find CodeMieToolConfig subclasses."""
    return _process_module_generic(module_name, seen_config_ids, configs, is_config_class, "configs")


def _scan_submodules_generic(module, seen_modules: Set[str], seen_ids: Set[str], items: List, scan_func) -> None:
    """Scan submodules of a package using the provided scan function."""
    if hasattr(module, '__path__'):
        for _, submodule_name, _ in pkgutil.iter_modules(module.__path__, module.__name__ + '.'):
            scan_func(submodule_name, seen_modules, seen_ids, items)


def _scan_submodules(
    module, seen_modules: Set[str], seen_toolkit_ids: Set[str], toolkits: List[Type[BaseToolkit]]
) -> None:
    """Scan submodules of a package for BaseToolkit subclasses."""
    _scan_submodules_generic(module, seen_modules, seen_toolkit_ids, toolkits, _scan_recursively)


def _scan_config_submodules(
    module, seen_modules: Set[str], seen_config_ids: Set[str], configs: List[Type[CodeMieToolConfig]]
) -> None:
    """Scan submodules of a package for CodeMieToolConfig subclasses."""
    _scan_submodules_generic(module, seen_modules, seen_config_ids, configs, _scan_config_recursively)


def _scan_recursively(
    module_name: str, seen_modules: Set[str], seen_toolkit_ids: Set[str], toolkits: List[Type[BaseToolkit]]
) -> None:
    """Recursively scan a module and its submodules for BaseToolkit subclasses."""
    if module_name in seen_modules:
        return

    seen_modules.add(module_name)

    module = _process_module(module_name, seen_toolkit_ids, toolkits)
    if module:
        _scan_submodules(module, seen_modules, seen_toolkit_ids, toolkits)


def _scan_config_recursively(
    module_name: str, seen_modules: Set[str], seen_config_ids: Set[str], configs: List[Type[CodeMieToolConfig]]
) -> None:
    """Recursively scan a module and its submodules for CodeMieToolConfig subclasses."""
    if module_name in seen_modules:
        return

    seen_modules.add(module_name)

    module = _process_config_module(module_name, seen_config_ids, configs)
    if module:
        _scan_config_submodules(module, seen_modules, seen_config_ids, configs)


def _find_toolkits() -> List[Type[BaseToolkit]]:
    """
    Find all BaseToolkit subclasses in packages recursively by:
    1. Scanning all modules under codemie_tools looking for classes implementing DiscoverableToolkit
    2. Scanning the explicitly allowed packages listed in __CODEMIE_TOOLS_ALLOWED_PACKAGES

    Returns:
        List of unique BaseToolkit subclasses
    """

    toolkits = []
    seen_modules: Set[str] = set()
    seen_toolkit_ids: Set[str] = set()

    # Start the recursive scan
    for package_name in __CODEMIE_TOOLS_ALLOWED_PACKAGES:
        _scan_recursively(package_name, seen_modules, seen_toolkit_ids, toolkits)

    logger.info(f"Lookup toolkits. Found {len(toolkits)} toolkits in {len(seen_modules)} modules.")
    return toolkits


def _find_tool_configs() -> List[Type[CodeMieToolConfig]]:
    """
    Find all CodeMieToolConfig subclasses in a package recursively.

    Returns:
        List of unique CodeMieToolConfig subclasses
    """

    configs = []
    seen_modules: Set[str] = set()
    seen_config_ids: Set[str] = set()

    # Start the recursive scan
    for package_name in __CODEMIE_TOOLS_ALLOWED_PACKAGES:
        _scan_config_recursively(package_name, seen_modules, seen_config_ids, configs)

    logger.info(f"Lookup configs. Found {len(configs)} configs in {len(seen_modules)}.")
    return configs


@functools.lru_cache(maxsize=None)
def get_available_toolkits() -> List[Type[BaseToolkit]]:
    """
    Get all available BaseToolkit subclasses in a package.
    Results are cached for better performance.

    Returns:
        List of BaseToolkit subclasses found
    """
    toolkits = _find_toolkits()
    logger.info(f"Retrieved {len(toolkits)} available toolkits")
    return toolkits


@functools.lru_cache(maxsize=None)
def get_available_tools_configs() -> List[Type[CodeMieToolConfig]]:
    """
    Get all available CodeMieToolConfig subclasses in a package.
    Results are cached for better performance.

    Returns:
        List of CodeMieToolConfig subclasses found
    """
    configs = _find_tool_configs()
    logger.info(f"Retrieved {len(configs)} available tool configs")
    return configs


@functools.lru_cache(maxsize=128)
def get_toolkit(name: str, raise_error: bool = False) -> type[BaseToolkit] | None:
    """
    Get a specific BaseToolkit subclass by its name.
    Results are cached for better performance.

    Args:
        name: The name of the toolkit class to find
        raise_error: If True, raises ValueError when toolkit is not found. If False, returns None.

    Returns:
        The BaseToolkit subclass if found

    Raises:
        ValueError: If no toolkit with the given name is found
    """
    toolkits = get_available_toolkits()
    for toolkit in toolkits:
        definition = toolkit.get_definition()
        if definition and definition.toolkit == name:
            return toolkit
    if raise_error:
        raise ValueError(f"No toolkit found with name: {name}")
    return None


@functools.lru_cache(maxsize=128)
def get_tools() -> List[Tool]:
    """
    Get all tools from all available toolkits by first getting their UI info.
    Results are cached for better performance.

    Returns:
        List of all tools from all available toolkits
    """
    logger.info("Getting all tools from available toolkits")

    all_tools = []
    toolkits = get_available_toolkits()

    for toolkit_class in toolkits:
        try:
            # Get the toolkit definition which contains the tools
            toolkit_definition = toolkit_class.get_definition()
            if toolkit_definition:
                # Extract tools from the definition
                all_tools.extend(toolkit_definition.tools)
                logger.debug(f"Added {len(toolkit_definition.tools)} tools from {toolkit_class.__name__}")
            else:
                logger.warning(f"Toolkit {toolkit_class.__name__} returned None definition")
        except Exception as e:
            logger.error(f"Error getting tools from {toolkit_class.__name__}: {e}")

    return all_tools


@functools.lru_cache(maxsize=128)
def get_tool(name: str, raise_error: bool = False) -> Optional[Tool]:
    """
    Get a specific Tool by its name.
    Results are cached for better performance.

    Args:
        name: The name of the tool to find
        raise_error: If True, raises ValueError when tool is not found. If False, returns None.

    Returns:
        The Tool object if found, None if not found and raise_error is False

    Raises:
        ValueError: If no tool with the given name is found and raise_error is True
    """
    tools = get_tools()
    for tool in tools:
        if tool.name == name:
            return tool

    if raise_error:
        raise ValueError(f"No tool found with name: {name}")
    return None


@functools.lru_cache(maxsize=None)
def get_available_toolkits_info() -> List[dict]:
    """
    Get information about all available toolkits in a serializable format.
    Uses model_dump() for each toolkit to convert them to dictionaries.
    Results are cached for better performance.

    Returns:
        List of dictionaries containing toolkit information
    """
    toolkits_info = []
    toolkits = get_available_toolkits()

    for toolkit_class in toolkits:
        try:
            # Get the toolkit definition
            toolkit_definition = toolkit_class.get_definition()

            # Skip if toolkit_definition is None
            if toolkit_definition is None:
                logger.debug(f"Skipping {toolkit_class.__name__} - definition is None")
                continue

            # Convert to dictionary using model_dump
            toolkit_info = toolkit_definition.model_dump()
            # Add the class name for reference
            toolkit_info['class_name'] = f"{toolkit_class.__module__}.{toolkit_class.__name__}"
            toolkits_info.append(toolkit_info)
        except Exception as e:
            logger.error(f"Error getting info from {toolkit_class.__name__}: {e}")
    return toolkits_info


def _extract_field_data(field_info: dict) -> dict:
    """
    Extract field data from a field info dictionary.

    Args:
        field_info: Dictionary containing field information from model_json_schema

    Returns:
        Dictionary with processed field data
    """
    field_data = {
        'description': field_info.get('description', ''),
    }

    # Extract additional metadata from json_schema_extra if available
    for key, value in field_info.items():
        if key not in ['description', 'type', 'default'] and key != 'json_schema_extra':
            field_data[key] = value

    # Handle json_schema_extra separately as it contains nested metadata
    if 'json_schema_extra' in field_info:
        extra_info = field_info['json_schema_extra']
        for key, value in extra_info.items():
            field_data[key] = value

    # Handle type information correctly
    field_data['type'] = _determine_field_type(field_info)

    # Add default value if present
    if 'default' in field_info:
        field_data['default'] = field_info['default']

    # Remove unnecessary fields
    for key in ['anyOf', 'title']:
        if key in field_data:
            del field_data[key]

    return field_data


def _determine_field_type(field_info: dict) -> str:
    """
    Determine the field type from field info.

    Args:
        field_info: Dictionary containing field information

    Returns:
        String representing the field type
    """
    if 'type' in field_info:
        return field_info['type']
    elif 'anyOf' in field_info:
        # For fields with anyOf, extract the primary type
        types = [item.get('type') for item in field_info['anyOf'] if 'type' in item and item['type'] != 'null']
        if types:
            return types[0]

    # Default to string if no type found
    return 'string'


def _process_config_class(config_class) -> tuple:
    """
    Process a config class to extract its information.

    Args:
        config_class: The config class to process

    Returns:
        Tuple of (config_info_dict, success_flag)
    """
    try:
        # Extract the config name from the class name
        config_name = config_class.__name__.lower()  # Convert to lowercase for JSON compliance

        # Get the model schema which contains field information
        model_schema = config_class.model_json_schema()

        # Extract field information
        fields = {}
        if 'properties' in model_schema:
            for field_name, field_info in model_schema['properties'].items():
                fields[field_name] = _extract_field_data(field_info)

        # Create config info with class field and fields
        config_info = {"class": f"{config_class.__module__}.{config_class.__name__}", **fields}

        # Return the config info as a dictionary with the config name as key
        return {config_name: config_info}, True
    except Exception as e:
        logger.error(f"Error getting info from {config_class.__name__}: {e}")
        return None, False


@functools.lru_cache(maxsize=None)
def get_available_tools_configs_info() -> list:
    """
    Get information about all available tool configs in a serializable format.
    Extracts field information from Pydantic models without creating instances.
    Results are cached for better performance.

    Returns:
        List containing tool config information in the format:
        [
            {
                "config_name": {
                    "class": "fully.qualified.ClassName",
                    "field_name": {
                        "description": "field description",
                        "placeholder": "example value",
                        "type": "string",
                        "sensitive": true,  # if applicable
                        "help": "help URL",  # if applicable
                        "default": "default value"  # if applicable
                    },
                    ...
                }
            },
            ...
        ]
    """
    tools_configs_info = []
    tools_configs = get_available_tools_configs()
    successful_configs = 0
    failed_configs = 0

    for config_class in tools_configs:
        config_info, success = _process_config_class(config_class)
        if success:
            tools_configs_info.append(config_info)
            successful_configs += 1
        else:
            failed_configs += 1

    logger.info(f"Retrieved info for {successful_configs} tool configs, {failed_configs} failed")
    return tools_configs_info
