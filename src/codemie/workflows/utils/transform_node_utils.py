# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

import json
from typing import Any, Optional
import re

from codemie.configs import logger
from codemie.workflows.utils import get_context_store_from_state_schema
from codemie.workflows.models import AgentMessages


def parse_array_index(input_key: str) -> tuple[str, Optional[int | slice], Optional[str]]:
    """Parse array index syntax from input_key with support for chained paths.

    Supports single index access (positive and negative), slice syntax, and continued
    dot notation after the array index (e.g., 'users[0].name').

    Args:
        input_key: Key path like 'users[0]', 'api.users[-1]', 'data.items[0:3]',
                   or 'api.users[0].first_name'

    Returns:
        tuple: (base_path, index_or_slice, remaining_path)
            - base_path: Path before index (e.g., 'api.users')
            - index_or_slice: int for single index, slice for range, None if no index
            - remaining_path: Path after index (e.g., 'first_name'), None if no continuation

    Examples:
        >>> parse_array_index('users[0]')
        ('users', 0, None)

        >>> parse_array_index('api.users[-1]')
        ('api.users', -1, None)

        >>> parse_array_index('data.items[0:3]')
        ('data.items', slice(0, 3), None)

        >>> parse_array_index('api.users[0].first_name')
        ('api.users', 0, 'first_name')

        >>> parse_array_index('users[0].address.city')
        ('users', 0, 'address.city')

        >>> parse_array_index('no_index')
        ('no_index', None, None)
    """
    # Match array index with optional continued path: users[0].name, users[-1], users[0:3].prop
    # Use [^\[]+ instead of .+? to prevent ReDoS (catastrophic backtracking)
    # Use .+ for the remaining path to capture everything including nested array indices
    # Flatten optional groups to reduce complexity: separate :end and :step optionals
    match = re.match(r'^([^\[]+)\[(-?\d+)(?::(-?\d+))?(?::(-?\d+))?\](?:\.(.+))?$', input_key)

    if not match:
        return input_key, None, None

    base_path = match.group(1)
    start = int(match.group(2))
    end = match.group(3)
    step = match.group(4)
    remaining_path = match.group(5)  # Captures everything after '].' if present

    if end is not None:
        # Slice syntax: [0:3] or [0:3:1]
        end_int = int(end)
        step_int = int(step) if step else None
        return base_path, slice(start, end_int, step_int), remaining_path
    else:
        # Single index: [0] or [-1]
        return base_path, start, remaining_path


def handle_dict_value(value: dict, source_data: dict, wrapper_key: Optional[str]) -> dict:
    """Handle dictionary values with optional wrapping.

    Args:
        value: Dictionary value to handle
        source_data: Dictionary to populate with data
        wrapper_key: Optional key name to use when wrapping values

    Returns:
        dict: Source data with dictionary value wrapped or merged
    """
    if wrapper_key:
        source_data[wrapper_key] = value
    else:
        source_data.update(value)
    return source_data


def handle_string_value(value: str, source_data: dict, wrapper_key: Optional[str]) -> dict:
    """Handle string values with JSON parsing attempt.

    Args:
        value: String value to handle
        source_data: Dictionary to populate with data
        wrapper_key: Optional key name to use when wrapping values

    Returns:
        dict: Source data with parsed or raw string value
    """
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return handle_dict_value(parsed, source_data, wrapper_key)
        return wrap_or_replace_value(parsed, source_data, wrapper_key)
    except json.JSONDecodeError:
        return wrap_or_replace_value(value, source_data, wrapper_key)


def wrap_or_replace_value(value: Any, source_data: dict, wrapper_key: Optional[str]) -> dict:
    """Wrap value with key or replace source_data.

    Args:
        value: Value to wrap or use as replacement
        source_data: Dictionary to populate with data
        wrapper_key: Optional key name to use when wrapping values

    Returns:
        dict: Source data with value wrapped or replaced
    """
    if wrapper_key:
        source_data[wrapper_key] = value
    else:
        source_data = value
    return source_data


def extract_field(source_data: dict, source_path: str, default=None) -> Any:
    """Extract field using JSONPath/dot notation.

    Args:
        source_data: Source data dictionary
        source_path: Path to field (dot notation or JSONPath)
        default: Default value if field not found

    Returns:
        Any: Extracted value or default
    """
    if not source_path:
        return default

    # Try simple dot notation first
    try:
        value = source_data
        for key in source_path.split('.'):
            if isinstance(value, dict):
                value = value.get(key)
                if value is None:
                    return default
            else:
                return default
        return value
    except (KeyError, TypeError, AttributeError):
        return default


def parse_value_to_dict(value: Any, source_data: dict, wrapper_key: Optional[str] = None) -> dict:
    """Parse a value and merge it into source_data.

    This function intelligently handles different value types:
    - When wrapper_key is provided: ALL values (including dicts) are wrapped with the key
    - When wrapper_key is None: dicts are merged, other values replace source_data
    - JSON strings are parsed and handled according to their parsed type

    Args:
        value: The value to parse (can be dict, list, str, primitive, or any object)
        source_data: Dictionary to populate with parsed data (typically empty dict)
        wrapper_key: Optional key name to use when wrapping values (e.g., 'total', 'headings', 'items')

    Returns:
        dict: Source data with parsed values. For non-dict values without wrapper_key,
              returns the value directly (may not be a dict).

    Examples:
        >>> # Dict value WITH wrapper_key - wraps the dict
        >>> parse_value_to_dict({'count': 42, 'sum': 1050}, {}, wrapper_key='total')
        {'total': {'count': 42, 'sum': 1050}}

        >>> # Dict value WITHOUT wrapper_key - merges into source_data
        >>> parse_value_to_dict({'key1': 'val1', 'key2': 'val2'}, {}, wrapper_key=None)
        {'key1': 'val1', 'key2': 'val2'}

        >>> # Array value with wrapper_key - wraps with the specified key
        >>> parse_value_to_dict(
        ...     [{'heading': 'H1'}, {'heading': 'H2'}],
        ...     {},
        ...     wrapper_key='headings'
        ... )
        {'headings': [{'heading': 'H1'}, {'heading': 'H2'}]}

        >>> # Array value without wrapper_key - replaces source_data (returns array)
        >>> parse_value_to_dict([1, 2, 3, 4, 5], {}, wrapper_key=None)
        [1, 2, 3, 4, 5]

        >>> # Primitive value with wrapper_key - wraps with the specified key
        >>> parse_value_to_dict(42, {}, wrapper_key='count')
        {'count': 42}
        >>> parse_value_to_dict(True, {}, wrapper_key='active')
        {'active': True}
        >>> parse_value_to_dict('plain text', {}, wrapper_key='text')
        {'text': 'plain text'}

        >>> # Primitive value without wrapper_key - replaces source_data
        >>> parse_value_to_dict(42, {}, wrapper_key=None)
        42

        >>> # JSON string containing dict WITH wrapper_key - wraps the dict
        >>> parse_value_to_dict('{"status": "ok", "code": 200}', {}, wrapper_key='result')
        {'result': {'status': 'ok', 'code': 200}}

        >>> # JSON string containing dict WITHOUT wrapper_key - merges into source_data
        >>> parse_value_to_dict('{"status": "ok", "code": 200}', {}, wrapper_key=None)
        {'status': 'ok', 'code': 200}

        >>> # JSON string containing array with wrapper_key - wraps the parsed array
        >>> parse_value_to_dict(
        ...     '[{"name": "item1"}, {"name": "item2"}]',
        ...     {},
        ...     wrapper_key='items'
        ... )
        {'items': [{'name': 'item1'}, {'name': 'item2'}]}

        >>> # JSON string containing array without wrapper_key - replaces source_data
        >>> parse_value_to_dict('[1, 2, 3]', {}, wrapper_key=None)
        [1, 2, 3]

        >>> # Non-JSON string with wrapper_key - wraps as string
        >>> parse_value_to_dict('not json', {}, wrapper_key='text')
        {'text': 'not json'}

        >>> # Non-JSON string without wrapper_key - replaces source_data
        >>> parse_value_to_dict('not json', {}, wrapper_key=None)
        'not json'

    Note:
        When wrapper_key is provided, it's used consistently for ALL value types including dicts.
        This ensures that nested path extraction (e.g., 'stats.total') creates a 'total' variable
        containing the dict, rather than unwrapping it.
    """
    if isinstance(value, dict):
        return handle_dict_value(value, source_data, wrapper_key)

    if isinstance(value, str):
        return handle_string_value(value, source_data, wrapper_key)

    return wrap_or_replace_value(value, source_data, wrapper_key)


def extract_with_array_indices(data: Any, path: str) -> Any:
    """Extract value from data using a path that may contain multiple array indices.

    This function recursively processes paths with chained array indices, supporting
    complex expressions like 'social_media[0].profile_url' or 'items[0].tags[1].name'.

    Args:
        data: The data to extract from (can be dict, list, or any value)
        path: Path with potential array indices (e.g., 'social_media[0].profile_url')

    Returns:
        The extracted value or None if not found

    Examples:
        >>> # Single array index with field
        >>> data = {'users': [{'name': 'John', 'age': 30}]}
        >>> extract_with_array_indices(data, 'users[0].name')
        'John'

        >>> # Multiple array indices
        >>> data = {'users': [{'social_media': [{'url': 'twitter.com'}]}]}
        >>> extract_with_array_indices(data, 'users[0].social_media[0].url')
        'twitter.com'

        >>> # Complex nested structure
        >>> data = [{'items': [{'tags': [{'name': 'A'}]}]}]
        >>> extract_with_array_indices(data[0], 'items[0].tags[0].name')
        'A'
    """
    if not path:
        return data

    # Parse the first array index if present
    base_path, index_or_slice, remaining_path = parse_array_index(path)

    # Extract the base path first (if it's different from the original path)
    if base_path != path:
        # Path contains array index - extract base first
        value = extract_field(data if isinstance(data, dict) else {}, base_path, default=None)

        if value is None:
            return None

        # Apply array indexing
        if not isinstance(value, (list, tuple)):
            logger.warning(
                f"Transform node: Array index specified for '{path}' but value is not a "
                f"list/tuple: {type(value).__name__}"
            )
            return None

        try:
            value = value[index_or_slice]
        except (IndexError, TypeError) as e:
            logger.warning(f"Transform node: Failed to apply array index {index_or_slice} to '{path}': {e}")
            return None

        # Recursively process the remaining path (which may contain more array indices)
        if remaining_path:
            return extract_with_array_indices(value, remaining_path)

        return value
    else:
        # No array index in path - use simple field extraction
        return extract_field(data if isinstance(data, dict) else {}, path, default=None)


def handle_array_indexing(
    value: Any,
    index_or_slice: Any,
    remaining_path: Optional[str],
    input_key: str,
    base_path: str,
    source_data: dict,
) -> dict:
    """Handle array indexing logic for context store extraction with chained paths.

    Supports continued dot notation after array indexing, including multiple array indices:
    - 'users[0].first_name' extracts the 'first_name' field from the first user
    - 'api.data[0].nested.value' extracts nested values from indexed element
    - 'users[0].social_media[0].profile_url' extracts with multiple array indices

    Args:
        value: The value to apply indexing to
        index_or_slice: The index or slice to apply
        remaining_path: Path to continue extracting after array index (e.g., 'first_name')
        input_key: Original input key for error messages
        base_path: Base path without array index
        source_data: Dictionary to populate with extracted data

    Returns:
        dict: Source data with indexed value wrapped appropriately

    Examples:
        >>> # Simple array index without chaining
        >>> handle_array_indexing([{'name': 'John'}, {'name': 'Jane'}], 0, None, 'users[0]', 'users', {})
        {'users': {'name': 'John'}}

        >>> # Array index with chained path
        >>> handle_array_indexing([{'name': 'John'}, {'name': 'Jane'}], 0, 'name', 'users[0].name', 'users', {})
        {'name': 'John'}

        >>> # Nested chained path
        >>> data = [{'user': {'details': {'name': 'John'}}}]
        >>> handle_array_indexing(data, 0, 'user.details.name', 'data[0].user.details.name', 'data', {})
        {'name': 'John'}

        >>> # Multiple array indices in chain
        >>> data = [{'social_media': [{'url': 'linkedin.com', 'handle': '@john'}]}]
        >>> handle_array_indexing(data, 0, 'social_media[0].url', 'users[0].social_media[0].url', 'users', {})
        {'url': 'linkedin.com'}
    """
    if not isinstance(value, (list, tuple)):
        logger.warning(
            f"Transform node: Array index specified for '{input_key}' but value is not a "
            f"list/tuple: {type(value).__name__}"
        )
        return source_data

    try:
        value = value[index_or_slice]
    except (IndexError, TypeError) as e:
        logger.warning(f"Transform node: Failed to apply array index {index_or_slice} to '{input_key}': {e}")
        return source_data

    # If there's a remaining path, continue extraction from the indexed value
    if remaining_path:
        # Use extract_with_array_indices to handle potential chained array indices
        value = extract_with_array_indices(value, remaining_path)
        if value is None:
            logger.warning(
                f"Transform node: Failed to extract remaining path '{remaining_path}' from indexed value "
                f"in '{input_key}'"
            )
            return source_data

        # Use the last part of the remaining path as wrapper key
        # E.g., 'users[0].first_name' → wrapper_key='first_name'
        # E.g., 'users[0].address.city' → wrapper_key='city'
        # E.g., 'users[0].social_media[0].profile_url' → wrapper_key='profile_url'
        # Remove array indices from the path to extract the last field name
        clean_path = re.sub(r'\[\d+\]', '', remaining_path)
        wrapper_key = clean_path.split('.')[-1]
        return parse_value_to_dict(value, source_data, wrapper_key)

    # No remaining path - use the array name as wrapper key
    # E.g., 'api.users[0]' → wrapper_key='users'
    wrapper_key = base_path.split('.')[-1] if '.' in base_path else base_path
    return parse_value_to_dict(value, source_data, wrapper_key)


def extract_from_context_store(state_schema: AgentMessages, input_key: Optional[str], source_data: dict) -> dict:
    """Extract data from context store with support for nested key access and array indexing.

    The function uses the last part of the input_key path as the variable name, providing
    consistent and meaningful variable names in subsequent mappings for ALL value types.

    Supports dot notation for nested access and bracket notation for array indexing.

    Args:
        state_schema: The current state schema containing context_store
        input_key: Key or nested path to extract (supports dot notation and array indexing).
                   None extracts entire context_store.
        source_data: Dictionary to populate with extracted data (typically empty dict)

    Returns:
        dict: Source data with extracted values wrapped using the last part of the input_key path

    Examples:
        >>> # No input_key - returns entire context store (merged)
        >>> context_store = {'key1': 'value1', 'key2': 'value2'}
        >>> extract_from_context_store(state_schema, None, {})
        {'key1': 'value1', 'key2': 'value2'}

        >>> # Simple key (no dots) - merges dict into source_data
        >>> context_store = {'rules': {'type': 'standard', 'count': 5}}
        >>> extract_from_context_store(state_schema, 'rules', {})
        {'type': 'standard', 'count': 5}

        >>> # Nested key extracting dict - wraps with last part of path ('total')
        >>> context_store = {'stats': {'total': {'count': 42, 'sum': 1050, 'average': 25.0}}}
        >>> extract_from_context_store(state_schema, 'stats.total', {})
        {'total': {'count': 42, 'sum': 1050, 'average': 25.0}}

        >>> # Deeply nested dict extraction - wraps with last part ('settings')
        >>> context_store = {'config': {'app': {'settings': {'debug': True, 'port': 8080}}}}
        >>> extract_from_context_store(state_schema, 'config.app.settings', {})
        {'settings': {'debug': True, 'port': 8080}}

        >>> # Nested key extracting array - wraps with last part of path ('headings')
        >>> context_store = {
        ...     'standard_rules': {
        ...         'headings': [{'heading': 'H1', 'level': 1}, {'heading': 'H2', 'level': 2}]
        ...     }
        ... }
        >>> extract_from_context_store(state_schema, 'standard_rules.headings', {})
        {'headings': [{'heading': 'H1', 'level': 1}, {'heading': 'H2', 'level': 2}]}

        >>> # Deeply nested key extracting array - wraps with last part ('items')
        >>> context_store = {
        ...     'config': {
        ...         'validation': {
        ...             'rules': {'items': ['rule1', 'rule2', 'rule3']}
        ...         }
        ...     }
        ... }
        >>> extract_from_context_store(state_schema, 'config.validation.rules.items', {})
        {'items': ['rule1', 'rule2', 'rule3']}

        >>> # Nested key extracting primitive - wraps with last part ('count')
        >>> context_store = {'stats': {'total': {'count': 42}}}
        >>> extract_from_context_store(state_schema, 'stats.total.count', {})
        {'count': 42}

        >>> # Array indexing - single element with nested path
        >>> context_store = {
        ...     'api_response': {
        ...         'users': [
        ...             {'name': 'John', 'age': 30},
        ...             {'name': 'Jane', 'age': 25}
        ...         ]
        ...     }
        ... }
        >>> extract_from_context_store(state_schema, 'api_response.users[0]', {})
        {'users': {'name': 'John', 'age': 30}}

        >>> # Array indexing - negative index
        >>> extract_from_context_store(state_schema, 'api_response.users[-1]', {})
        {'users': {'name': 'Jane', 'age': 25}}

        >>> # Array indexing - simple key
        >>> context_store = {'users': [{'id': 1}, {'id': 2}, {'id': 3}]}
        >>> extract_from_context_store(state_schema, 'users[1]', {})
        {'users': {'id': 2}}

        >>> # Array slicing - extract range
        >>> context_store = {'items': ['a', 'b', 'c', 'd', 'e']}
        >>> extract_from_context_store(state_schema, 'items[1:4]', {})
        {'items': ['b', 'c', 'd']}

        >>> # Chained array indexing - extract field from indexed element
        >>> context_store = {
        ...     'api_response': {
        ...         'users': [
        ...             {'first_name': 'John', 'last_name': 'Doe'},
        ...             {'first_name': 'Jane', 'last_name': 'Smith'}
        ...         ]
        ...     }
        ... }
        >>> extract_from_context_store(state_schema, 'api_response.users[0].first_name', {})
        {'first_name': 'John'}

        >>> # Deeply nested chained array indexing
        >>> context_store = {
        ...     'data': {
        ...         'items': [
        ...             {'user': {'profile': {'name': 'Alice', 'age': 30}}},
        ...             {'user': {'profile': {'name': 'Bob', 'age': 25}}}
        ...         ]
        ...     }
        ... }
        >>> extract_from_context_store(state_schema, 'data.items[1].user.profile.name', {})
        {'name': 'Bob'}

        >>> # Chained array indexing with simple key
        >>> context_store = {'users': [{'name': 'Alice', 'id': 1}, {'name': 'Bob', 'id': 2}]}
        >>> extract_from_context_store(state_schema, 'users[0].name', {})
        {'name': 'Alice'}

        >>> # Multiple chained array indices
        >>> context_store = {
        ...     'api_response': {
        ...         'users': [
        ...             {
        ...                 'name': 'John',
        ...                 'social_media': [
        ...                     {'platform': 'linkedin', 'profile_url': 'linkedin.com/john'}
        ...                 ]
        ...             }
        ...         ]
        ...     }
        ... }
        >>> extract_from_context_store(state_schema, 'api_response.users[0].social_media[0].profile_url', {})
        {'profile_url': 'linkedin.com/john'}

        >>> # Complex nested structure with multiple array indices
        >>> context_store = {
        ...     'data': {
        ...         'items': [
        ...             {
        ...                 'tags': [
        ...                     {'name': 'python', 'count': 100},
        ...                     {'name': 'fastapi', 'count': 50}
        ...                 ]
        ...             }
        ...         ]
        ...     }
        ... }
        >>> extract_from_context_store(state_schema, 'data.items[0].tags[1].name', {})
        {'name': 'fastapi'}

        >>> # Non-existent key - returns empty dict with warning logged
        >>> context_store = {'existing': 'value'}
        >>> extract_from_context_store(state_schema, 'nonexistent.key', {})
        {}

    Note:
        - Simple keys (no dots): Dicts are merged, other types wrapped with key name
        - Nested keys (with dots): ALL types (including dicts) are wrapped with last part of path
        - Array indexing: Value wrapped with array name (part before '[')
        - Chained array indexing: Continues extraction after index, wraps with last part of remaining path
        - Multiple chained array indices: Supports expressions like 'users[0].social_media[0].profile_url'
        - This ensures consistent variable naming:
          * 'stats.total' creates a 'total' variable
          * 'users[0]' creates a 'users' variable containing the indexed element
          * 'users[0].first_name' creates a 'first_name' variable
          * 'api.data[0].user.name' creates a 'name' variable
          * 'api.users[0].social_media[0].profile_url' creates a 'profile_url' variable
    """
    context_store = get_context_store_from_state_schema(state_schema)

    if not input_key:
        # No input_key specified - use entire context store
        source_data.update(context_store)
        return source_data

    # Parse array index if present (also returns remaining path after index)
    base_path, index_or_slice, remaining_path = parse_array_index(input_key)

    # Use existing extract_field function which already supports dot notation
    value = extract_field(context_store, base_path, default=None)

    if value is not None:
        # Apply array indexing if specified
        if index_or_slice is not None:
            return handle_array_indexing(value, index_or_slice, remaining_path, input_key, base_path, source_data)

        # No array indexing - use existing logic
        # Only use wrapper_key for nested paths (containing dots)
        # Simple keys (no dots) should merge dicts, nested keys should wrap all values
        if '.' in base_path:
            last_key = base_path.split('.')[-1]
            return parse_value_to_dict(value, source_data, last_key)

        # Simple key - no wrapper, merge behavior for dicts
        return parse_value_to_dict(value, source_data, wrapper_key=None)

    # Key not found - return empty source_data
    return source_data
