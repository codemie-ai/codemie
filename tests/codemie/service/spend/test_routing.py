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

"""AD-2's C2 acceptance is a matrix, not a boolean.

The full cross-product of the three flags is asserted here because the divergence that
matters is a single cell: `lite_llm` mode with embeddings disabled, where chat routes to
LiteLLM and embeddings do not. A predicate that answers one value for both call classes
passes every other cell.
"""

from __future__ import annotations

import itertools
from unittest.mock import patch

import pytest

from codemie.configs import config
from codemie.service.spend.routing import LLMCallClass, uses_litellm


def _routing(has_litellm: bool, enabled: bool, mode: str, embeddings_disabled: bool):
    with (
        patch("codemie.enterprise.HAS_LITELLM", has_litellm),
        patch.object(config, "LLM_PROXY_ENABLED", enabled),
        patch.object(config, "LLM_PROXY_MODE", mode),
        patch.object(config, "LLM_PROXY_EMBEDDINGS_DISABLED", embeddings_disabled),
    ):
        return uses_litellm(LLMCallClass.CHAT), uses_litellm(LLMCallClass.EMBEDDINGS)


@pytest.mark.parametrize(
    ("has_litellm", "enabled", "mode", "embeddings_disabled"),
    list(itertools.product((True, False), (True, False), ("lite_llm", "internal"), (True, False))),
)
def test_matrix_matches_the_live_routing_gates(has_litellm, enabled, mode, embeddings_disabled):
    """Independently re-derives llm_factory's two gates rather than restating the predicate."""
    chat_routes = has_litellm and enabled and mode == "lite_llm"
    embeddings_route = chat_routes and not embeddings_disabled

    assert _routing(has_litellm, enabled, mode, embeddings_disabled) == (chat_routes, embeddings_route)


def test_the_one_mixed_configuration_splits_the_two_call_classes():
    """AD-27's mixed deployment: chat through LiteLLM, embeddings native.

    This is the cell a predicate copied from the chat form gets wrong, and getting it wrong
    points embedding consumers at a table that never received their rows.
    """
    assert _routing(True, True, "lite_llm", True) == (True, False)


def test_proxy_enabled_alone_is_not_routing():
    """LLM_PROXY_MODE defaults to "internal", so this is one env var away from being live."""
    assert _routing(True, True, "internal", False) == (False, False)


def test_embeddings_never_route_where_chat_does_not():
    """Embeddings carry every chat term plus one, so the implication holds in every cell."""
    for has_litellm, enabled, mode, embeddings_disabled in itertools.product(
        (True, False), (True, False), ("lite_llm", "internal"), (True, False)
    ):
        chat, embeddings = _routing(has_litellm, enabled, mode, embeddings_disabled)
        assert not (embeddings and not chat)
