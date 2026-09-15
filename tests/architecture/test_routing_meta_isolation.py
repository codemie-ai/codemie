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

"""Architecture guard for RoutingInfo.meta's "opaque per-router passthrough bag" invariant.

``RoutingInfo.meta`` (core/routing_info.py) exists purely for forwarding: the router that
produces a key is the only code allowed to populate or interpret it. Generic consumers
(callbacks, RouterChatModel, ...) must never branch on its contents. That invariant used to
live only in a docstring; this test makes a violation of it a CI failure instead.

The concrete, checkable proxy for "branching on meta's contents": the field-name -> header-
name mappings that give each key its *meaning* (``SWITCHYARD_FIELD_TO_HEADER`` in
enterprise/switchyard/routing_meta.py, ``LITELLM_ROUTER_FIELD_TO_HEADER`` in
enterprise/litellm/litellm_router_meta.py) should never be imported by any module other than
the one that defines them. Nothing outside those two files needs to know what a specific meta
key *means* — everywhere else either treats ``meta`` as an opaque dict (merge, forward as
headers wholesale) or reads RoutingInfo's own canonical fields (``routed_model``,
``classifier_cost_usd``), a different, generically-interpretable contract, not part of
``meta``.

Deliberately NOT guarded: ``SWITCHYARD_HEADERS``/``LITELLM_ROUTER_HEADERS`` (the plain
frozenset of header-name strings, with no field-name association). Those back a legitimate,
different use — e.g. proxy_router.py uses ``LITELLM_ROUTER_HEADERS`` as a header-forwarding
allowlist (``header_name in LITELLM_ROUTER_HEADERS``) — which tests set membership on a
header name, never interprets what a specific header *means*. That's not "branching on
meta's contents" in the sense this guard cares about.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "codemie"

_GUARDED_NAMES: dict[str, Path] = {
    "SWITCHYARD_FIELD_TO_HEADER": _SRC_ROOT / "enterprise" / "switchyard" / "routing_meta.py",
    "LITELLM_ROUTER_FIELD_TO_HEADER": _SRC_ROOT / "enterprise" / "litellm" / "litellm_router_meta.py",
}


def _from_imported_names(py_file: Path) -> set[str]:
    """Names pulled in via ``from <module> import <name>`` anywhere in *py_file* — the only
    import style this codebase uses for its own modules, so it's sufficient to catch a real
    violation (as opposed to also handling `import module` + dotted attribute access, which
    doesn't appear anywhere in this codebase's internal imports)."""
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    return {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names}


@pytest.mark.parametrize("name,owner", sorted(_GUARDED_NAMES.items()))
def test_header_name_mapping_not_imported_outside_owning_module(name: str, owner: Path) -> None:
    violations = [
        py_file for py_file in _SRC_ROOT.rglob("*.py") if py_file != owner and name in _from_imported_names(py_file)
    ]
    assert not violations, (
        f"{name} is RoutingInfo.meta's header-name mapping, owned by "
        f"{owner.relative_to(_SRC_ROOT.parent.parent)}. It must not be imported anywhere else "
        f"— found it imported by: {[str(f.relative_to(_SRC_ROOT.parent.parent)) for f in violations]}. "
        "RoutingInfo.meta is an opaque per-router passthrough bag (see its docstring in "
        "core/routing_info.py) — importing this mapping elsewhere means generic code is about "
        "to branch on a specific meta key by name, which is exactly what that contract forbids."
    )
