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

import json
from typing import Any, List


class UnwrappingJsonPointerEvaluator:
    """
    This class duplicates basic JSON Pointer functionality, but handles the case where some JSON
    child objects were serialized into strings and stored this way in the parent object.

    Motivation:
    This was encountered in MCP tools execution. If the tool returns a valid JSON - it is converted
    into a string and stored simply as "text" node by MCP library. This prevents structured
    interaction with the said tool result. So we can't just use standard JSON-pointer
    library and have to go with our own implementation.
    """

    @classmethod
    def load_if_string(cls, data, traversed_path):
        """
        Handles JSONs serialized into strings. If data is a string - tries to load it into a JSON.
        Does nothing otherwise.
        """
        if isinstance(data, str):
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                raise ValueError(f"Invalid JSON string encountered at '{traversed_path}'.")
        return data

    @classmethod
    def traverse(cls, obj: Any, path_parts: List[str]):
        """
        Traverses the obj successively applying JSON-pointer style path components from the list.
        If a string node is encountered - it is deserialized into a child JSON object
        """
        traversed_path = ""
        current = obj

        for part in path_parts:
            current = cls.load_if_string(current, traversed_path)
            traversed_path += f"/{part}"
            if isinstance(current, dict):
                if part in current:
                    current = current[part]
                else:
                    raise KeyError(f"Path component '{part}' not found in the JSON object at '{traversed_path}'.")
            elif isinstance(current, list):
                try:
                    index = int(part)
                    current = current[index]
                except (ValueError, IndexError):
                    raise KeyError(f"Path component '{part}' not found in the JSON array at '{traversed_path}'.")
            else:
                raise ValueError(f"Unexpected non-JSON node encountered at '{traversed_path}'.")

        return current

    @classmethod
    def get_node_by_pointer(cls, source: Any, pointer: str):
        """
        Traverses the 'source' object using JSON-pointer style path expanding serialized JSONs as it goes.
        For example, in the path '/outer/node' - if the /outer content is a string, we
        load this string as JSON and apply /node path to that object. If /outer content
        is a normal JSON object already - we just continue applying the path components to it.

        Also note the 'trailing slash rule': if the path ends with a trailing slash, we
        also check the result: if it is a string, we deserialize it to JSON and return the
        resulting JSON. If the path doesn't end with a trailing slash, we return the content
        of the final node 'as-is'

        """
        path_parts = pointer.strip('/').split('/')
        result = cls.traverse(source, path_parts)

        # Handling trailing slash to (possibly) unwrap the found node
        if pointer.endswith('/'):
            result = cls.load_if_string(result, pointer)

        return result
