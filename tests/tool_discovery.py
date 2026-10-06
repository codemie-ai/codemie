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

"""Finds every concrete tool class of the platform, for the contract tests that walk all of them.

Shared by the test that lists the tool classes a workspace script may call and by any later contract test that needs
the same set (for example the one that checks that every tool declares the shape of its result).
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import sys
from collections.abc import Iterator
from functools import cache

from codemie_tools.base.codemie_tool import CodeMieTool

#: Packages whose modules define tool classes. Every module in them is imported.
TOOL_PACKAGES: tuple[str, ...] = (
    "codemie_tools",
    "codemie.agents.tools",
    "codemie.service.mcp",
    "codemie.service.provider",
)
#: Tool classes are only taken from modules under these roots (this leaves out classes defined by tests).
_TOOL_MODULE_ROOTS: tuple[str, ...] = ("codemie_tools.", "codemie.")


def _import_all(package_name: str) -> None:
    package = importlib.import_module(package_name)
    for module in pkgutil.walk_packages(package.__path__, prefix=f"{package_name}."):
        try:
            importlib.import_module(module.name)
        except Exception:  # noqa: BLE001, S112 - a module that needs an optional dependency has no tool to find
            continue


def _all_subclasses(base: type) -> Iterator[type]:
    for subclass in base.__subclasses__():
        yield subclass
        yield from _all_subclasses(subclass)


@cache
def discover_tool_classes() -> tuple[type[CodeMieTool], ...]:
    """Every concrete (non-abstract) ``CodeMieTool`` subclass that the platform's own tool packages define at import
    time, sorted by qualified name. Classes made at run time (the provider tools) are left out, so the result does not
    depend on which tests ran before."""
    for package_name in TOOL_PACKAGES:
        _import_all(package_name)
    found: dict[str, type[CodeMieTool]] = {}
    for subclass in _all_subclasses(CodeMieTool):
        if inspect.isabstract(subclass) or not subclass.__module__.startswith(_TOOL_MODULE_ROOTS):
            continue
        if getattr(sys.modules.get(subclass.__module__), subclass.__name__, None) is not subclass:
            continue  # created at run time (a provider tool built by ``type(...)``) or nested: not a class of the code base
        found[f"{subclass.__module__}.{subclass.__qualname__}"] = subclass
    return tuple(found[name] for name in sorted(found))


def qualified_name(tool_class: type) -> str:
    return f"{tool_class.__module__}.{tool_class.__qualname__}"
