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


from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ScriptResult:
    """Structured outcome of a tool call made from a workspace script."""

    result: JsonValue
    http_status: int | None = None
    http_reason: str | None = None


class ScriptResultTooLarge(Exception):
    """A tool output is already known to be over the size cap, so it was not converted for the script."""

    def __init__(self, size: int) -> None:
        super().__init__(f"result is at least {size} bytes")
        self.size: int = size


@runtime_checkable
class ScriptResultSource(Protocol):
    """A tool output that knows how to hand a script its structured result: the one mechanism for it.

    ``max_bytes`` is the size cap of the answer: an implementation whose size is cheap to read may raise
    :class:`ScriptResultTooLarge` before it parses anything.
    """

    def to_script_result(self, max_bytes: int | None = None) -> ScriptResult: ...


@runtime_checkable
class ScriptCallable(Protocol):
    """What the tool-call handler needs from a tool: a name, an argument schema and the script-path entry point."""

    @property
    def name(self) -> str: ...

    @property
    def args_schema(self) -> object: ...

    def run_for_script(self, arguments: Mapping[str, object], *, max_result_bytes: int | None = None) -> ScriptResult:
        """Validate the tool's configuration, run it with ``arguments`` and return the structured result."""
        ...
