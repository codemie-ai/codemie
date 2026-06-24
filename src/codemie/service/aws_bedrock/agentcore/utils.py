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

from typing import Any, Optional


def set_json_path(data: dict, path: str, value: Any) -> None:
    """Write *value* into *data* at the dot-notation JSON path, creating intermediate dicts as needed.

    Example: set_json_path({}, "body.query", "hello") → {"body": {"query": "hello"}}
    """
    parts = path.split(".")
    current = data
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}

        current = current[part]

    current[parts[-1]] = value


def resolve_json_path(data: Any, path: Optional[str]) -> Any:
    """Read a value from *data* at the dot-notation JSON path. Returns None if the path is absent.

    When a list is encountered and the next segment is not an integer index, fans out:
    resolves the remaining path for each element and returns a list of non-None results.

    Examples:
        resolve_json_path({"result": {"answer": "hi"}}, "result.answer") → "hi"
        resolve_json_path({"items": [{"text": "a"}, {"text": "b"}]}, "items.text") → ["a", "b"]
    """
    if not path or data is None:
        return None

    parts = path.split(".")
    current = data

    for i, part in enumerate(parts):
        if current is None:
            return None

        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                remaining = ".".join(parts[i:])
                results = [resolve_json_path(item, remaining) for item in current]
                results = [r for r in results if r is not None]

                return results or None
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None

    return current
