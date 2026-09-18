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

"""Architecture guard: LiteLLM's own external routing-header vocabulary must stay isolated.

``LITELLM_ROUTER_FIELD_TO_HEADER`` (enterprise/litellm/litellm_router_headers.py) mirrors
LiteLLM's own external x-litellm-router-*/x-litellm-classifier-* wire vocabulary (field names
like ``cause``, ``router_model_name``, ``score`` are LiteLLM's own, not ours) — a foreign
contract this codebase reads but does not define. ``LiteLLMRouterHeaders.from_headers()`` (the
only production consumer of this mapping) translates that wire shape into RoutingInfo's own
canonical domain fields (``decision_source``, ``requested_model``, ``confidence``/``router_score``,
...) inside enterprise/litellm/router.py's ``routing_info_from_headers()``. Nothing outside that
one translation function needs to know LiteLLM's raw field names at all — every other consumer
(callbacks, RouterChatModel, the proxy's own client-facing header codec in
enterprise/switchyard/proxy.py) works exclusively off the already-translated RoutingInfo. This
test makes "some other module starts reading LiteLLM's raw wire vocabulary directly, instead of
going through the one translator" a CI failure instead of a silent architectural drift.

Deliberately NOT guarded: ``LITELLM_ROUTER_HEADERS`` (the plain frozenset of header-name
strings, with no field-name association) — historically used as a header-forwarding allowlist
in proxy_router.py; as of the routing-header-unification change that allowlist usage was
removed (raw x-litellm-router-* headers are no longer forwarded to clients at all), but the
frozenset itself may still have other legitimate, non-interpretive uses (testing header-name set
membership, never what a specific header *means*) — that's not "branching on wire-vocabulary
meaning" in the sense this guard cares about.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import codemie.core.routing_info as routing_info

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "codemie"

_GUARDED_NAMES: dict[str, Path] = {
    "LITELLM_ROUTER_FIELD_TO_HEADER": _SRC_ROOT / "enterprise" / "litellm" / "litellm_router_headers.py",
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
        f"{name} mirrors LiteLLM's own foreign wire vocabulary, owned by "
        f"{owner.relative_to(_SRC_ROOT.parent.parent)}. It must not be imported anywhere else "
        f"— found it imported by: {[str(f.relative_to(_SRC_ROOT.parent.parent)) for f in violations]}. "
        "Only LiteLLMRouterHeaders.from_headers() may translate this raw wire shape into "
        "RoutingInfo's canonical domain fields — importing this mapping elsewhere means generic "
        "code is about to branch on LiteLLM's own field names directly, bypassing that one "
        "translation point."
    )


def test_core_routing_info_does_not_own_provider_tier_normalization() -> None:
    assert not hasattr(routing_info, "normalize_routing_tier")
