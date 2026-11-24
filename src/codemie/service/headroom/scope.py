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

"""Workflow-scope Headroom layers and the resolution of an assistant's effective config."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from codemie.configs.logger import logger
from codemie.service.headroom.models import HeadroomConfig, HeadroomOverride, ResolvedHeadroomConfig
from codemie.service.headroom.resolver import resolve


@dataclass(frozen=True)
class WorkflowHeadroomLayers:
    """Workflow-scope Headroom overrides for one node agent: the node's own block and the workflow's."""

    node: HeadroomOverride | None = None
    workflow: HeadroomOverride | None = None


def _typed_or_none(value: Any, expected: type, name: str) -> Any:
    """Return `value` when it has the expected type or is None; otherwise log its type only and ignore it."""
    if value is None or isinstance(value, expected):
        return value
    logger.warning(f"Headroom {name} ignored: unexpected type {type(value).__name__}")
    return None


def resolve_headroom_config(
    assistant: Any,
    node: HeadroomOverride | None = None,
    workflow: HeadroomOverride | None = None,
) -> ResolvedHeadroomConfig | None:
    """Resolve node > workflow > assistant Headroom config; None when unset, disabled or gated off.

    A value of the wrong type is logged (type name only) and treated as absent.
    """
    stored = _typed_or_none(getattr(assistant, "headroom_config", None), HeadroomConfig, "assistant config")
    return resolve(
        _typed_or_none(node, HeadroomOverride, "node override"),
        _typed_or_none(workflow, HeadroomOverride, "workflow override"),
        stored,
    )
