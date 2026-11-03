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

"""Which call classes actually reach LiteLLM (AD-2).

`is_litellm_enabled()` tests `HAS_LITELLM and LLM_PROXY_ENABLED` and is **narrower than
routing**: with `LLM_PROXY_ENABLED=true` and the default `LLM_PROXY_MODE="internal"` it
answers True while no request goes anywhere near LiteLLM. It keeps that meaning for
provisioning and admin paths and must never decide where a spend figure comes from.

The routing predicate differs **per call class**, and that asymmetry is the whole point of
this module: chat is three terms, embeddings is four. `llm_factory.get_litellm_embedding_model`
returns early on `LLM_PROXY_EMBEDDINGS_DISABLED` before the other three terms are reached, so
a predicate copied from the chat form answers True for embeddings in the one genuinely mixed
configuration — chat through LiteLLM, embeddings native — and points embedding consumers at a
table that never received them.
"""

from __future__ import annotations

from enum import Enum


class LLMCallClass(str, Enum):
    """The call classes that route independently.

    Kept minimal on purpose: a class belongs here only when its routing predicate differs,
    because each member is a branch every ledger consumer inherits.
    """

    CHAT = "chat"
    EMBEDDINGS = "embeddings"


def uses_litellm(call_class: LLMCallClass) -> bool:
    """Does a call of this class actually reach LiteLLM in this deployment?

    Mirrors the live routing gates rather than restating them:

    * chat — `llm_factory.get_litellm_chat_model` requires
      `HAS_LITELLM and LLM_PROXY_ENABLED and LLM_PROXY_MODE == "lite_llm"`;
    * embeddings — `llm_factory.get_litellm_embedding_model` requires all of the above
      **and** `not LLM_PROXY_EMBEDDINGS_DISABLED`, which it tests first.

    Because embeddings carry every chat term plus one, `uses_litellm(CHAT)` doubles as
    "does any traffic reach LiteLLM at all".
    """
    from codemie.configs import config
    from codemie.core.constants import LLMProxyMode
    from codemie.enterprise import HAS_LITELLM  # local import: avoids a circular import

    if not (HAS_LITELLM and config.LLM_PROXY_ENABLED and LLMProxyMode.lite_llm == config.LLM_PROXY_MODE):
        return False

    if call_class is LLMCallClass.EMBEDDINGS:
        return not config.LLM_PROXY_EMBEDDINGS_DISABLED

    return True
