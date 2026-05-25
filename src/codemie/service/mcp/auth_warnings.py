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

from contextvars import ContextVar
from typing import Any

_mcp_auth_warnings: ContextVar[list[dict[str, Any]] | None] = ContextVar("mcp_auth_warnings", default=None)


def clear_mcp_auth_warnings() -> None:
    _mcp_auth_warnings.set([])


def record_mcp_auth_warning(warning: dict[str, Any]) -> None:
    warnings = list(_mcp_auth_warnings.get() or [])
    warnings.append(dict(warning))
    _mcp_auth_warnings.set(warnings)


def record_mcp_auth_warnings(warnings: list[dict[str, Any]]) -> None:
    for warning in warnings:
        record_mcp_auth_warning(warning)


def get_mcp_auth_warnings(*, clear: bool = True) -> list[dict[str, Any]]:
    warnings = list(_mcp_auth_warnings.get() or [])
    if clear:
        clear_mcp_auth_warnings()
    return warnings


__all__ = [
    "clear_mcp_auth_warnings",
    "get_mcp_auth_warnings",
    "record_mcp_auth_warning",
    "record_mcp_auth_warnings",
]
