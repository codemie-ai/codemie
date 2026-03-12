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

from typing import Any, List


# Key-value combinations that should be skipped during text collection/replacement
# These are structural metadata in LangChain messages, not actual content
SKIP_KEY_VALUE_PAIRS: set[tuple[str, str]] = {
    ("type", "text"),
    ("type", "image_url"),
    ("type", "image"),
    ("type", "audio"),
    ("type", "video"),
    ("type", "tool_call"),
    ("tool_call", "function"),
}


def extract_message_texts(message) -> List[str]:
    """Grab text fragments from .content, tool_calls, additional_kwargs, etc."""
    texts: List[str] = []
    _collect_texts(getattr(message, "content", None), texts)
    _collect_texts(getattr(message, "tool_calls", None), texts)
    _collect_texts(getattr(message, "additional_kwargs", None), texts)
    _collect_texts(getattr(message, "metadata", None), texts)
    return texts


def update_message_texts(message, new_texts: List[str]):
    cursor = _ReplacementCursor(new_texts)

    new_content = _replace_texts(getattr(message, "content", None), cursor)
    new_tool_calls = _replace_texts(getattr(message, "tool_calls", None), cursor)
    new_kwargs = _replace_texts(getattr(message, "additional_kwargs", None), cursor)
    new_metadata = _replace_texts(getattr(message, "metadata", None), cursor)

    cursor.assert_drained()

    if hasattr(message, "content"):
        message.content = new_content
    if hasattr(message, "tool_calls"):
        message.tool_calls = new_tool_calls
    if hasattr(message, "additional_kwargs"):
        message.additional_kwargs = new_kwargs
    if hasattr(message, "metadata"):
        message.metadata = new_metadata
    return message


def _collect_texts(obj: Any, out: List[str]) -> None:
    """
    Recursively collect text-like strings from `obj`.

    The object is expected to be shallow, so we do not set any max_depth here.
    """
    if obj is None:
        return

    if isinstance(obj, str):
        out.append(obj)

    elif isinstance(obj, list):
        for item in obj:
            _collect_texts(item, out)

    elif isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, str) and (key, value) in SKIP_KEY_VALUE_PAIRS:
                continue
            _collect_texts(value, out)


class _ReplacementCursor:
    def __init__(self, replacements: List[str]):
        self._iter = iter(replacements)
        self.total = len(replacements)
        self.used = 0

    def next(self) -> str:
        try:
            value = next(self._iter)
        except StopIteration:
            raise ValueError("Not enough replacement strings supplied.")
        self.used += 1
        return value

    def assert_drained(self) -> None:
        if self.used != self.total:
            raise ValueError(f"Replacement strings were not all used ({self.used}/{self.total}).")


def _replace_texts(obj: Any, cursor: _ReplacementCursor) -> Any:
    """Return a mutated object with strings replaced in traversal order."""
    if obj is None:
        return obj

    if isinstance(obj, str):
        return cursor.next()

    if isinstance(obj, list):
        return [_replace_texts(item, cursor) for item in obj]

    if isinstance(obj, dict):
        result = {}
        for key, value in obj.items():
            # Skip specific key-value combinations - preserve original
            if isinstance(value, str) and (key, value) in SKIP_KEY_VALUE_PAIRS:
                result[key] = value
            else:
                result[key] = _replace_texts(value, cursor)
        return result

    return obj  # numbers, booleans, etc.
