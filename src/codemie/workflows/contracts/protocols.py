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

"""Seam between the linker and whatever knows tool parameter schemas (EPMCDME-16503, T10).

`contracts/` never reads a service. A tool parameter schema reaches the simulator through a `ToolSchemaResolver`,
which answers with the set of mandatory parameter names or with `UNKNOWN` (AC-38). The implementation lives in
`workflows/validation/tool_schemas.py`, the only place that touches services. Leaf module: no runtime imports.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from codemie.core.workflow_models.workflow_models import WorkflowTool


class _Unknown:
    """The answer "no usable parameter schema"; a single instance, never a collection of names."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNKNOWN"

    def __copy__(self) -> _Unknown:
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> _Unknown:
        return self


UNKNOWN = _Unknown()


@runtime_checkable
class ToolSchemaResolver(Protocol):
    """Answers which parameters of a tool are mandatory, or `UNKNOWN` when no usable args schema is known."""

    def mandatory_params(self, tool_ref: WorkflowTool | str) -> frozenset[str] | _Unknown: ...
