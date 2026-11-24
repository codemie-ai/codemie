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

"""Configuration vocabulary of the transform node, shared by the node and the save-time contract linker.

Dependency free on purpose: the linker reads these without importing the node, which pulls in services.
"""

from collections.abc import Callable
from typing import Any

# Configuration keys
CONFIG_KEY_ON_ERROR = 'on_error'
CONFIG_KEY_MAPPINGS = 'mappings'
CONFIG_KEY_OUTPUT_SCHEMA = 'output_schema'
CONFIG_KEY_INPUT_SOURCE = 'input_source'
CONFIG_KEY_INPUT_KEY = 'input_key'
CONFIG_KEY_DEFAULT_OUTPUT = 'default_output'

# Error strategies
ERROR_STRATEGY_FAIL = 'fail'
ERROR_STRATEGY_SKIP = 'skip'
ERROR_STRATEGY_DEFAULT = 'default'
ERROR_STRATEGY_PARTIAL = 'partial'

# Input sources
INPUT_SOURCE_CONTEXT_STORE = 'context_store'
INPUT_SOURCE_MESSAGES = 'messages'
INPUT_SOURCE_USER_INPUT = 'user_input'
INPUT_SOURCE_STATE_SCHEMA = 'state_schema'
INPUT_SOURCE_COMBINED = 'combined'

# Mapping types
MAPPING_TYPE_EXTRACT = 'extract'
MAPPING_TYPE_CONDITION = 'condition'
MAPPING_TYPE_TEMPLATE = 'template'
MAPPING_TYPE_CONSTANT = 'constant'
MAPPING_TYPE_SCRIPT = 'script'
MAPPING_TYPE_ARRAY_MAP = 'array_map'

# Every mapping type `TransformNode._apply_mapping` dispatches; any other value raises.
MAPPING_TYPES = frozenset(
    {
        MAPPING_TYPE_EXTRACT,
        MAPPING_TYPE_CONDITION,
        MAPPING_TYPE_TEMPLATE,
        MAPPING_TYPE_CONSTANT,
        MAPPING_TYPE_SCRIPT,
        MAPPING_TYPE_ARRAY_MAP,
    }
)

# Mapping field keys
MAPPING_KEY_OUTPUT_FIELD = 'output_field'
MAPPING_KEY_TYPE = 'type'
MAPPING_KEY_SOURCE_PATH = 'source_path'
MAPPING_KEY_DEFAULT = 'default'
MAPPING_KEY_CONDITION = 'condition'
MAPPING_KEY_THEN_VALUE = 'then_value'
MAPPING_KEY_ELSE_VALUE = 'else_value'
MAPPING_KEY_TEMPLATE = 'template'
MAPPING_KEY_VALUE = 'value'
MAPPING_KEY_SCRIPT = 'script'
MAPPING_KEY_ITEM_FIELD = 'item_field'
MAPPING_KEY_FILTER_CONDITION = 'filter_condition'

# Schema keys
SCHEMA_KEY_PROPERTIES = 'properties'
SCHEMA_KEY_REQUIRED = 'required'
SCHEMA_KEY_TYPE = 'type'

# Schema types
SCHEMA_TYPE_STRING = 'string'
SCHEMA_TYPE_INTEGER = 'integer'
SCHEMA_TYPE_BOOLEAN = 'boolean'
SCHEMA_TYPE_NUMBER = 'number'

# Output schema coercion: a value of one of the kept classes is returned as is, any other value is converted.
SCALAR_COERCIONS: dict[str, tuple[tuple[type, ...], Callable[[Any], Any]]] = {
    SCHEMA_TYPE_STRING: ((str,), str),
    SCHEMA_TYPE_INTEGER: ((int,), int),
    SCHEMA_TYPE_BOOLEAN: ((bool,), bool),
    SCHEMA_TYPE_NUMBER: ((int, float), float),
}


def split_source_path(source_path: str) -> list[str]:
    """The keys an `extract` mapping looks up, in order: its `source_path` split on dots, with no index syntax."""
    return source_path.split('.')
