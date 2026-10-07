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

"""The single source of static tool facts: which tool classes exist under which name.

Every concrete ``CodeMieTool`` subclass registers itself when it is defined (``CodeMieTool.__pydantic_init_subclass__``).
New static facts about a tool class belong in ``ToolRecord``; do not add another scan or catalog of tool classes.
This module must not import from ``codemie`` at module level (``codemie_tools`` is the lower layer).
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: Packages whose modules define tool classes. ``ensure_loaded`` imports every module in them.
TOOL_PACKAGES: tuple[str, ...] = (
    "codemie_tools",
    "codemie.agents.tools",
    "codemie.service.mcp",
    "codemie.service.provider",
)
#: Tool classes are only taken from modules under these roots (this leaves out classes defined by tests).
_TOOL_MODULE_ROOTS: tuple[str, ...] = ("codemie_tools.", "codemie.")

_CLASSES_BY_NAME: dict[str, list[type]] = {}
_LOADED: bool = False


@dataclass(frozen=True)
class ToolRecord:
    """Static facts about every tool class that carries one tool name."""

    name: str
    classes: tuple[type, ...]
    modules: tuple[str, ...]
    script_callable: tuple[bool, ...]  # one flag per class


def _default_name(cls: type) -> str | None:
    field = getattr(cls, "model_fields", {}).get("name")
    default = getattr(field, "default", None)
    return default if isinstance(default, str) and default else None


def register(cls: type) -> None:
    """Record ``cls`` under its default name. Abstract classes, classes without a default name and classes defined
    outside the platform's tool modules (tests) are skipped."""
    if inspect.isabstract(cls) or not cls.__module__.startswith(_TOOL_MODULE_ROOTS):
        return
    name = _default_name(cls)
    if name is None:
        return
    classes = _CLASSES_BY_NAME.setdefault(name, [])
    if cls not in classes:
        classes.append(cls)


def lookup(name: str) -> ToolRecord | None:
    classes = _CLASSES_BY_NAME.get(name)
    if not classes:
        return None
    return ToolRecord(
        name=name,
        classes=tuple(classes),
        modules=tuple(c.__module__ for c in classes),
        script_callable=tuple(bool(getattr(c, "script_callable", False)) for c in classes),
    )


def _import_all(package_name: str) -> None:
    package = importlib.import_module(package_name)
    for module in pkgutil.walk_packages(package.__path__, prefix=f"{package_name}."):
        try:
            importlib.import_module(module.name)
        except Exception as exc:  # noqa: BLE001 - one broken optional module must not hide the other tools
            logger.warning("Tool registry: could not import %s: %r", module.name, exc)


def ensure_loaded() -> None:
    """Import every tool package once so all tool classes have registered. Failures are logged, never raised."""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    for package_name in TOOL_PACKAGES:
        try:
            _import_all(package_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Tool registry: could not load package %s: %r", package_name, exc)


def is_known_excluded_from_script_calls(name: str) -> bool:
    """True only when the name is known and every class carrying it is not script-callable."""
    record = lookup(name)
    return record is not None and not any(record.script_callable)
